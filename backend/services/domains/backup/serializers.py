import asyncio
import json
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime
from functools import cache
from pathlib import Path
from typing import Any, Literal, get_args

from common import ModelBase
from components import AIConfig, ensure_utc, utc_now
from modules.auth import User, UserModelConfig
from modules.companion import (
    COMPANION_CRON_SOURCE_PREFIX,
    AvatarAsset,
    CharacterCardSnapshot,
    CharacterFeatures,
    CharacterOverrides,
    CompanionAction,
    CompanionActionPack,
    CompanionCharacterCard,
    CompanionDiaryEntry,
    CompanionIntent,
    CompanionIntentView,
    CompanionOutfit,
    CompanionPost,
    CompanionPostComment,
    CompanionScene,
    DiaryContent,
    Persona,
    PostCommentResponse,
    PostCommentRole,
    PostContentType,
    PostContext,
    SceneDescriptionRequest,
    companion_cron_source_key,
)
from modules.conversation import CompanionReply, Conversation, MediaBubble, Message
from modules.memory import MEMORY_EMBEDDING_DIM, MEMORY_SLOT_CONTEXT_PREFIXES, Memory
from modules.scheduler import CronJob
from modules.settings import UserSetting
from pydantic import ValidationError
from sqlalchemy import Date, DateTime, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.contracts import MemoryScope, MemorySource
from services.domains.conversation import CHECKPOINT_SUBTYPES, validate_memory_scope
from services.infrastructure.assets import parse_companion_asset_path

from .action_assets import restore_action_payload
from .file_packing import UrlRewriter

# 表白名单与依赖顺序单源维护；列从模型读取，新增持久字段不会静默漏备份。
TABLE_MODELS: dict[str, type[ModelBase]] = {
    "user_preferences": User,
    "conversations": Conversation,
    "user_model_configs": UserModelConfig,
    "avatar_assets": AvatarAsset,
    "companion_character_cards": CompanionCharacterCard,
    "companion_outfits": CompanionOutfit,
    "companion_action_packs": CompanionActionPack,
    "companion_actions": CompanionAction,
    "companion_scenes": CompanionScene,
    "personas": Persona,
    "user_settings": UserSetting,
    "cron_jobs": CronJob,
    "companion_intents": CompanionIntent,
    "memories": Memory,
    "companion_posts": CompanionPost,
    "companion_post_comments": CompanionPostComment,
    "companion_diary_entries": CompanionDiaryEntry,
    "messages": Message,
}
TABLES = tuple(TABLE_MODELS)
CONVERSATION_TABLES = frozenset({"conversations", "messages"})
ACTION_TABLES = frozenset({"companion_action_packs", "companion_actions"})
IDENTITY_TABLES = frozenset({"personas", "avatar_assets", "companion_character_cards"})
# 旧版片刻表已重构为动态；导入时静默跳过，视为没有动态。
RETIRED_TABLES = frozenset({"companion_moments", "companion_moment_comments"})
# 导入勾选粒度：分组 ID → 表；Admin 与 API 共用，不在页面里再维护表名。
BACKUP_SECTIONS: dict[str, tuple[str, ...]] = {
    "identity": ("personas", "avatar_assets", "companion_character_cards"),
    "conversations": ("conversations", "messages"),
    "memories": ("memories",),
    "posts": ("companion_posts", "companion_post_comments"),
    "diary": ("companion_diary_entries",),
    "wardrobe": ("companion_outfits", "companion_action_packs", "companion_actions"),
    "scenes": ("companion_scenes",),
    "automation": ("cron_jobs", "companion_intents"),
    "settings": ("user_model_configs", "user_settings", "user_preferences"),
}
BACKUP_SECTION_IDS = tuple(BACKUP_SECTIONS)
# 选中即整组恢复，不允许落成半个身份/会话/视频包。
ATOMIC_SECTION_GROUPS: tuple[frozenset[str], ...] = (
    IDENTITY_TABLES,
    CONVERSATION_TABLES,
    ACTION_TABLES,
)
# 预检时身份三表成组写入的顺序：先头像，再角色卡与人设。
IDENTITY_GROUP: tuple[str, ...] = ("avatar_assets", "companion_character_cards", "personas")
IDENTITY_INCOMPLETE_REASON = "基础身份必须同时包含人设、头像与角色卡。"
IDENTITY_BLOCKED_REASON = "目标现有场景、动作包等仍引用身份，无法安全覆盖基础身份；请一并恢复这些类别，或先处理引用。"
IDENTITY_DEPENDENT_REASON = "缺少可映射的基础身份，无法恢复此类别。"
FOREIGN_KEYS: dict[str, dict[str, str]] = {
    "companion_character_cards": {"avatar_id": "avatar_assets"},
    "companion_action_packs": {"avatar_id": "avatar_assets", "outfit_id": "companion_outfits"},
    "companion_actions": {"pack_id": "companion_action_packs", "outfit_id": "companion_outfits"},
    "personas": {"active_scene_id": "companion_scenes"},
    "cron_jobs": {"conversation_id": "conversations"},
    "companion_post_comments": {"post_id": "companion_posts"},
    "messages": {"conversation_id": "conversations"},
}
UNIQUE_KEYS: dict[str, tuple[str, ...]] = {
    "companion_character_cards": ("avatar_id",),
    "personas": (),
    "user_model_configs": (),
    "user_settings": ("setting_key",),
    "companion_diary_entries": ("entry_date",),
}
# 运行期状态不导出；恢复时取模型默认值（必填列在 _build_payload 中显式置空）。
_EXCLUDED_COLUMNS: dict[str, frozenset[str]] = {
    "messages": frozenset({"dedup_key"}),
    "companion_scenes": frozenset({"generation_state_json", "regeneration_state_json", "secondary_reference_image"}),
    "companion_character_cards": frozenset(
        {
            "portrait_result_json",
            "body_result_json",
            "portrait_source_path",
            "body_source_path",
        },
    ),
}
# 恢复时按 MemorySource.kind 原样保留记忆来源类型，备份中的其他取值落为 import。
_MEMORY_SOURCE_KINDS = frozenset(get_args(MemorySource.__annotations__["kind"]))
IdMap = dict[str, dict[str, int | str]]
BackupImportMode = Literal["overwrite", "merge"]


def tables_for_sections(sections: Sequence[str] | None) -> frozenset[str]:
    """解析导入分组为表集合；None 表示全部，空列表或未知 ID 由调用方拒绝。"""
    if sections is None:
        return frozenset(TABLES)
    normalized = [item.strip() for item in sections if item and item.strip()]
    if not normalized:
        raise ValueError("恢复范围不能为空")
    if "all" in normalized:
        if len(normalized) > 1:
            raise ValueError("恢复范围 all 不能与其他分组同时指定")
        return frozenset(TABLES)
    unknown = [item for item in normalized if item not in BACKUP_SECTIONS]
    if unknown:
        raise ValueError(f"未知的恢复范围：{', '.join(unknown)}")
    tables: set[str] = set()
    for item in normalized:
        tables.update(BACKUP_SECTIONS[item])
    return frozenset(tables)


@cache
def _columns(table: str) -> tuple[str, ...]:
    if table == "user_preferences":
        return ("nightly_activity_enabled",)
    excluded = _EXCLUDED_COLUMNS.get(table, frozenset()) | {"user_id"}
    return tuple(column.name for column in TABLE_MODELS[table].__table__.columns if column.name not in excluded)


async def serialize_rows(
    db: AsyncSession,
    table: str,
    user_id: int,
    *,
    batch_size: int = 1000,
) -> list[dict[str, Any]]:
    model = TABLE_MODELS[table]
    columns = _columns(table)
    if table == "messages":
        base_stmt = select(Message).join(Conversation).where(Conversation.user_id == user_id)
    else:
        base_stmt = select(model).where((User.id if table == "user_preferences" else model.user_id) == user_id)
    if table == "memories":
        base_stmt = base_stmt.where(
            Memory.source_kind != "diary",
            or_(Memory.context.is_(None), ~Memory.context.like("diary:%")),
        )

    result: list[dict[str, Any]] = []
    offset = 0
    while True:
        stmt = base_stmt.order_by(model.id).offset(offset).limit(batch_size)
        rows = (await db.execute(stmt)).scalars().all()
        if not rows:
            break
        for row in rows:
            payload = {col: getattr(row, col) for col in columns}
            for col, value in payload.items():
                if isinstance(value, datetime):
                    payload[col] = ensure_utc(value).isoformat()
                elif isinstance(value, date):
                    payload[col] = value.isoformat()
            if table == "memories" and payload.get("embedding") is not None:
                payload["embedding"] = list(map(float, payload["embedding"]))
            result.append(payload)
        if len(rows) < batch_size:
            break
        offset += len(rows)
        await asyncio.sleep(0)
    return result


async def insert_rows(
    db: AsyncSession,
    table: str,
    raw_rows: list[dict[str, Any]],
    target_user_id: int,
    rewriter: UrlRewriter,
    id_map: IdMap,
    *,
    mode: BackupImportMode,
    import_batch_id: str,
    asset_owner_id: int,
) -> tuple[dict[str, int | str], int]:
    model = TABLE_MODELS[table]
    new_map: dict[str, int | str] = {}
    inserted = 0
    lineages: list[tuple[Conversation, Any, Any, datetime | None]] = []
    for raw in sorted(raw_rows, key=lambda row: int(row["id"])) if table == "messages" else raw_rows:
        payload = _build_payload(table, raw, target_user_id, rewriter, id_map)
        if table == "memories":
            payload["source_refs"]["import_batch_id"] = import_batch_id
        if table == "user_preferences":
            if mode == "overwrite":
                user = await db.get(User, target_user_id)
                user.nightly_activity_enabled = payload["nightly_activity_enabled"]
                inserted += 1
            continue
        if table == "cron_jobs" and payload.get("conversation_id") is not None:
            conversation = await db.get(Conversation, payload["conversation_id"])
            if conversation.user_id != target_user_id or not conversation.is_automation:
                raise ValueError("Standard job requires an automation conversation")
        if table == "companion_posts":
            if not isinstance(payload.get("is_read"), bool):
                raise ValueError("Invalid post read state")
            if payload.get("content_type") not in {kind.value for kind in PostContentType}:
                raise ValueError("Invalid post content type")
            PostContext.model_validate(payload["context_json"])
            if payload.get("quota_kind") not in {"autonomous", "user_requested"}:
                raise ValueError("Invalid post quota kind")
            if payload["content_type"] != "text" and not payload.get("media_url"):
                raise ValueError("Post main media is missing")
            for path in (payload.get("media_url"), payload.get("audio_url")):
                if path and (not (parsed := parse_companion_asset_path(path)) or parsed[0] != asset_owner_id):
                    raise ValueError("Post media must belong to the target account")
        if table == "companion_diary_entries":
            if not isinstance(payload.get("is_read"), bool):
                raise ValueError("Invalid diary read state")
            content = DiaryContent.model_validate({key: payload.get(key) for key in ("title", "body", "mood")})
            payload.update(content.model_dump())
        if table == "companion_post_comments" and payload.get("role") not in {role.value for role in PostCommentRole}:
            raise ValueError("Invalid post comment role")
        if table == "companion_post_comments":
            PostCommentResponse.model_validate({**payload, "id": raw["id"]})
            if (payload["role"] == "companion") != (payload["reply_status"] == "none"):
                raise ValueError("Invalid reply status for comment role")
        existing = None
        if mode == "merge" and table in UNIQUE_KEYS:
            existing = await db.scalar(
                select(model).where(
                    model.user_id == target_user_id,
                    *(getattr(model, key) == payload[key] for key in UNIQUE_KEYS[table]),
                ),
            )
        if (
            mode == "merge"
            and table == "memories"
            and (payload.get("context") or "").startswith(MEMORY_SLOT_CONTEXT_PREFIXES)
        ):
            existing = await db.scalar(
                select(Memory).where(
                    Memory.user_id == target_user_id,
                    Memory.system_preset_id == payload["system_preset_id"],
                    Memory.context == payload["context"],
                ),
            )
        if table == "conversations" and payload.get("kind") == "special" and payload.get("system_preset_id"):
            existing = await db.scalar(
                select(Conversation).where(
                    Conversation.user_id == target_user_id,
                    Conversation.kind == "special",
                    Conversation.system_preset_id == payload["system_preset_id"],
                ),
            )
        if existing is not None:
            if table == "conversations" and (raw["context_after_message_id"] or existing.context_after_message_id):
                raise ValueError("Cannot merge conversation histories with context watermarks")
            new_map[str(raw["id"])] = existing.id
            continue
        if (
            mode == "merge"
            and payload.get("active")
            and await db.scalar(
                select(model.id).where(model.user_id == target_user_id, model.active.is_(True)).limit(1),
            )
        ):
            payload["active"] = False
        instance = model(**payload)
        db.add(instance)
        await db.flush()
        new_map[str(raw["id"])] = instance.id
        inserted += 1
        if table == "conversations":
            lineages.append((instance, raw.get("parent_id"), raw.get("forked_from_id"), payload.get("updated_at")))
    if table == "companion_post_comments":
        originals = {str(raw["id"]): raw for raw in raw_rows}
        for raw in raw_rows:
            target_id = raw.get("reply_to_comment_id")
            if target_id is None:
                continue
            target = originals.get(str(target_id))
            if (
                target is None
                or target["post_id"] != raw["post_id"]
                or target["role"] != "user"
                or raw["role"] != "companion"
            ):
                raise ValueError("Invalid post comment reply target")
            row = await db.get(CompanionPostComment, new_map[str(raw["id"])])
            row.reply_to_comment_id = new_map[str(target_id)]
    for conversation, parent_id, forked_from_id, updated_at in lineages:
        if parent_id is None and forked_from_id is None:
            continue
        if parent_id is not None:
            if str(parent_id) not in new_map:
                raise ValueError("Conversation parent is missing from backup")
            conversation.parent_id = new_map[str(parent_id)]
            parent = await db.get(Conversation, conversation.parent_id)
            if parent.user_id != target_user_id or parent.system_preset_id != conversation.system_preset_id:
                raise ValueError("Conversation parent belongs to a different scope")
        if forked_from_id is not None:
            # 派生来源只记录血缘，来源不在备份中时置空，不拒绝恢复。
            conversation.forked_from_id = new_map.get(str(forked_from_id))
        await db.flush()
        if updated_at is not None:
            conversation.updated_at = updated_at
    await db.flush()
    return new_map, inserted


def _validated_ai_config(value: Any) -> dict[str, Any]:
    """按运行期读取方式整体校验模型配置；配置含供应商密钥，错误只报告字段位置与类型，不回显取值。"""
    try:
        return AIConfig.model_validate(value).model_dump()
    except ValidationError as exc:
        fields = ", ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'ai_config'} ({error['type']})"
            for error in exc.errors(include_input=False, include_url=False)
        )
        raise ValueError(f"Invalid AI config: {fields}") from None


def _original_source(refs: dict[str, Any]) -> Any:
    """再次导入的记录沿用最初的来源，反复导出导入时来源不逐层嵌套。"""
    return refs.get("original_source", refs)


def _build_payload(
    table: str,
    raw: dict[str, Any],
    user_id: int,
    rewriter: UrlRewriter,
    id_map: IdMap,
) -> dict[str, Any]:
    model = TABLE_MODELS[table]
    columns = _columns(table)
    payload = {key: value for key, value in raw.items() if key in columns and key != "id"}
    for column in model.__table__.columns:
        value = payload.get(column.name)
        if value is None:
            continue
        if isinstance(column.type, DateTime):
            payload[column.name] = ensure_utc(datetime.fromisoformat(value))
        elif isinstance(column.type, Date):
            payload[column.name] = date.fromisoformat(value)
    payload = rewriter.rewrite(payload)
    for key, ref_table in FOREIGN_KEYS.get(table, {}).items():
        value = raw.get(key)
        mapped = id_map.get(ref_table, {}).get(str(value)) if value is not None else None
        if (
            value is not None
            and mapped is None
            and (table in ACTION_TABLES or ref_table in id_map and key != "avatar_id")
        ):
            raise ValueError(f"Missing {ref_table} reference in {table}.{key}")
        payload[key] = mapped
    if table in ACTION_TABLES:
        restore_action_payload(table, payload, id_map, user_id)
    if table == "companion_character_cards":
        if payload.get("avatar_id") is None:
            raise ValueError("Character card avatar is missing from backup")
        revision = payload.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise ValueError("Invalid character card revision")
        payload["automatic_json"] = CharacterFeatures.model_validate_json(payload["automatic_json"]).model_dump_json()
        payload["overrides_json"] = CharacterOverrides.model_validate_json(payload["overrides_json"]).model_dump_json(
            exclude_none=True,
        )
        payload["portrait_source_path"] = payload["body_source_path"] = ""
        payload["status"] = payload["portrait_status"] = payload["body_status"] = "ready" if revision else "failed"
        payload["error"] = None if revision else "恢复的角色资料尚未完成分析，请重试"
    if table == "messages" and payload.get("conversation_id") is None:
        raise ValueError("Message conversation is missing from backup")
    if table == "messages":
        through_id = payload.get("summary_through_message_id")
        if payload.get("subtype") in CHECKPOINT_SUBTYPES:
            if type(through_id) is not int or through_id <= 0:
                raise ValueError("Conversation summary requires an original message boundary")
        elif through_id is not None:
            raise ValueError("Only summary messages may have a summary boundary")
    if table == "conversations":
        payload["parent_id"] = payload["forked_from_id"] = None
        if not isinstance(payload.get("context_after_message_id"), int) or payload["context_after_message_id"] < 0:
            raise ValueError("Invalid conversation context watermark")
        payload["context_after_message_id"] = 0
        payload["memory_reviewed_message_id"] = 0
        if payload.get("is_automation"):
            if payload.get("system_preset_id") != "automation":
                raise ValueError("Invalid automation preset")
        else:
            validate_memory_scope(MemoryScope(user_id, payload.get("system_preset_id")))
    if table == "cron_jobs":
        validate_memory_scope(MemoryScope(user_id, payload.get("system_preset_id")))
        if payload.get("kind") == "special" and payload["system_preset_id"] != "companion":
            raise ValueError("Special job belongs to a different preset")
    if table == "companion_intents":
        CompanionIntentView.model_validate({**payload, "id": raw["id"]}, strict=True)
        if payload["status"] == "queued":
            payload["status"] = "waiting"
        elif payload["status"] == "running":
            payload["status"] = "failed"
            payload["last_error"] = "Restored interrupted run; verify previous tool effects before rescheduling."
            payload["updated_at"] = utc_now()
        payload["lease_token"] = None
        payload["lease_until"] = None
        source_key = payload.get("source_key")
        if isinstance(source_key, str) and source_key.startswith(COMPANION_CRON_SOURCE_PREFIX):
            job_id = id_map.get("cron_jobs", {}).get(source_key.removeprefix(COMPANION_CRON_SOURCE_PREFIX))
            payload["source_key"] = companion_cron_source_key(int(job_id)) if job_id is not None else None
        payload["event_received_at"] = None
    if table == "memories":
        if (payload.get("context") or "").startswith("diary:") or payload.get("source_kind") == "diary":
            raise ValueError("Diary indexes must be rebuilt from published diaries")
        validate_memory_scope(MemoryScope(user_id, payload.get("system_preset_id")))
        if not isinstance(payload.get("content_version"), int) or payload["content_version"] <= 0:
            raise ValueError("Invalid memory content version")
        if not isinstance(payload.get("source_refs"), dict) or not isinstance(payload.get("source_kind"), str):
            raise ValueError("Memory source is required")
        if payload.get("status") not in {"active", "candidate", "invalidated", "forgotten"}:
            raise ValueError("Invalid memory status")
        if payload.get("basis") not in {"explicit", "inferred", "observed", "system"}:
            raise ValueError("Invalid memory basis")
        if payload.get("usage") not in {"background", "contextual"}:
            raise ValueError("Invalid memory usage")
        if not isinstance(payload.get("evidence"), list) or not isinstance(payload.get("history"), list):
            raise ValueError("Memory evidence and history are required")
        embedding = payload.get("embedding")
        if embedding is not None:
            if not isinstance(embedding, list) or not all(isinstance(v, int | float) for v in embedding):
                raise ValueError("Memory embedding must be a list of numbers")
            if len(embedding) != MEMORY_EMBEDDING_DIM:
                raise ValueError(f"Memory embedding dim {len(embedding)} != {MEMORY_EMBEDDING_DIM}")
        if payload["source_kind"] not in _MEMORY_SOURCE_KINDS:
            payload["source_kind"] = "import"
        payload["source_refs"] = {
            "imported_memory_id": raw["id"],
            "original_source": _original_source(payload["source_refs"]),
        }
    if table == "user_model_configs":
        payload["ai_config"] = _validated_ai_config(payload.get("ai_config"))
    if table == "user_settings":
        # 读取方逐值 json.loads；无法解析的值不能写入，否则每次读取设置都会失败。
        try:
            json.loads(payload.get("setting_value"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid user setting value: {payload.get('setting_key')!r}") from exc
    if table == "messages" and payload.get("reply_json"):
        if payload.get("media_json"):
            raise ValueError("Structured replies cannot contain separate media attachments")
        reply = CompanionReply.model_validate_json(payload["reply_json"])
        for bubble in reply.bubbles:
            if isinstance(bubble, MediaBubble):
                bubble.job_id = None
                if bubble.status == "pending":
                    bubble.status, bubble.url, bubble.error = "failed", None, "恢复的生成任务不可继续，请重新提出请求"
        reply.validate_content(payload.get("content") or "")
        payload["reply_json"] = reply.model_dump_json()
    if table == "companion_scenes":
        if payload.get("status") == "ready":
            SceneDescriptionRequest.model_validate(
                {"title": payload.get("title"), "description": payload.get("description")},
            )
            if not payload.get("media_path"):
                raise ValueError("Ready scene image is missing from backup")
        snapshot = CharacterCardSnapshot.model_validate_json(payload["character_card_json"])
        avatar_id = id_map.get("avatar_assets", {}).get(str(snapshot.avatar_id))
        if avatar_id is None:
            raise ValueError("Scene character reference is missing from backup")
        payload["character_card_json"] = snapshot.model_copy(update={"avatar_id": int(avatar_id)}).model_dump_json()
        payload["auto_activate"] = False
        payload["regeneration_status"] = None
        payload["regeneration_stage"] = None
        payload["regeneration_error"] = None
        payload["regeneration_task_id"] = None
        if payload.get("status") == "pending":
            payload["status"] = "description_failed" if payload.get("media_path") else "failed"
            payload["error"] = "恢复的场景任务需要手动重试"
    if table == "companion_post_comments":
        payload["reply_to_comment_id"] = None
        if payload.get("reply_status") in {"pending", "running"}:
            payload["reply_status"] = "failed"
            payload["reply_error"] = "恢复的评论可手动重试回复"
    if table == "companion_diary_entries":
        payload["post_ids"] = [
            str(id_map["companion_posts"][str(value)])
            for value in raw.get("post_ids", [])
            if str(value) in id_map.get("companion_posts", {})
        ]
    if table not in {"messages", "user_preferences"}:
        payload["user_id"] = user_id
    return payload


async def restore_conversation_context(
    db: AsyncSession,
    rows: dict[str, list[dict[str, Any]]],
    id_map: IdMap,
) -> None:
    messages = {str(row["id"]): row for row in rows.get("messages", [])}
    for original_id, raw in messages.items():
        if raw.get("subtype") not in CHECKPOINT_SUBTYPES:
            continue
        source_id = str(raw["summary_through_message_id"])
        source = messages.get(source_id)
        if (
            source is None
            or str(source["conversation_id"]) != str(raw["conversation_id"])
            or source.get("subtype") in CHECKPOINT_SUBTYPES
        ):
            raise ValueError("Conversation summary boundary is missing from backup")
        checkpoint = await db.get(Message, int(id_map["messages"][original_id]))
        checkpoint.summary_through_message_id = int(id_map["messages"][source_id])
    by_conversation: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for message in sorted(messages.values(), key=lambda message: int(message["id"])):
        by_conversation[str(message["conversation_id"])].append(message)
    for raw in rows.get("conversations", []):
        original_id = str(raw["id"])
        ordered_messages = by_conversation[original_id]
        original_ids = [int(message["id"]) for message in ordered_messages]
        mapped_ids = [int(id_map["messages"][str(mid)]) for mid in original_ids]
        for message, mapped_id in zip(ordered_messages, mapped_ids, strict=True):
            if (order := message.get("context_order")) is None:
                continue
            # 消费位置可能落在两个接收 id 之间；保留其相对次序，不沿用导入前的数值。
            position = bisect_left(original_ids, order)
            restored = await db.get(Message, mapped_id)
            restored.context_order = mapped_ids[position] if position < len(mapped_ids) else mapped_ids[-1] + 1
        watermark = raw["context_after_message_id"]
        if watermark:
            conv = await db.get(Conversation, int(id_map["conversations"][original_id]))
            conv.context_after_message_id = max(
                (
                    mapped_id
                    for original, mapped_id in zip(original_ids, mapped_ids, strict=True)
                    if original <= watermark
                ),
                default=0,
            )
    await db.flush()


async def restore_memory_context(
    db: AsyncSession,
    rows: dict[str, list[dict[str, Any]]],
    id_map: IdMap,
    user_id: int,
    import_batch_id: str,
) -> None:
    conversations = {str(row["id"]): row for row in rows.get("conversations", [])}
    messages = {str(row["id"]): row for row in rows.get("messages", [])}
    for raw in rows.get("memories", []):
        memory = await db.get(Memory, int(id_map["memories"][str(raw["id"])]))
        # 只处理本批插入的记录；merge 命中的现有槽位保留原来源。
        if memory.source_refs.get("import_batch_id") != import_batch_id:
            continue
        refs = raw["source_refs"]
        restored: dict[str, Any] = {
            "imported_memory_id": raw["id"],
            "import_batch_id": import_batch_id,
            "original_source": _original_source(refs),
        }
        session_id = refs.get("session_id")
        if session_id is not None and str(session_id) in conversations:
            conv = conversations[str(session_id)]
            if conv["system_preset_id"] != raw["system_preset_id"]:
                raise ValueError("Memory source belongs to a different preset")
            restored["session_id"] = int(id_map["conversations"][str(session_id)])
            for mid in refs.get("message_ids", []):
                message = messages.get(str(mid))
                if message is not None and str(message["conversation_id"]) != str(session_id):
                    raise ValueError("Invalid memory source message")

        def remap_evidence(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
            result = []
            for item in evidence:
                entry = dict(item)
                mid = entry.get("message_id")
                if mid is not None:
                    message = messages.get(str(mid))
                    conv = conversations.get(str(message["conversation_id"])) if message else None
                    if conv and conv["system_preset_id"] != raw["system_preset_id"]:
                        raise ValueError("Memory evidence belongs to a different preset")
                    if not message or not conv or str(mid) not in id_map.get("messages", {}):
                        entry.pop("message_id", None)
                        entry.pop("session_id", None)
                        entry["source_unavailable"] = True
                    else:
                        if message["role"] != "user" or message.get("subtype") is not None:
                            raise ValueError("Memory evidence must reference an original user message")
                        entry["message_id"] = int(id_map["messages"][str(mid)])
                        entry["session_id"] = int(id_map["conversations"][str(message["conversation_id"])])
                result.append(entry)
            return result

        memory.evidence = remap_evidence(raw["evidence"])
        if any(e.get("source_unavailable") for e in memory.evidence) and memory.status in {"active", "candidate"}:
            memory.status = "invalidated"
            memory.reason = "Original evidence was not included in the restored backup"
        history = []
        for old in raw["history"]:
            item = dict(old)
            item["id"] = memory.id
            item["evidence"] = remap_evidence(item.get("evidence", []))
            history.append(item)
        memory.history = history
        restored["message_ids"] = sorted({e["message_id"] for e in memory.evidence if "message_id" in e})
        memory.source_refs = restored
        # 赋列表达式使 UPDATE 显式写入原值；否则 onupdate 会把备份中的 updated_at 改成导入时刻。
        memory.updated_at = Memory.updated_at
    await db.flush()


def read_table_rows(extract_root: Path, table: str) -> list[dict[str, Any]]:
    payload = json.loads((extract_root / "db" / f"{table}.json").read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"Invalid rows in {table}.json")
    if table == "user_preferences":
        if len(rows) > 1:
            raise ValueError("Backup contains multiple user preference rows")
        return rows
    if any("id" not in row for row in rows):
        raise ValueError(f"Missing row id in {table}.json")
    if len({str(row["id"]) for row in rows}) != len(rows):
        raise ValueError(f"Duplicate row id in {table}.json")
    return rows
