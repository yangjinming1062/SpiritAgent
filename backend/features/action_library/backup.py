"""共享动作定义、目标排除与手动制作意图的备份贡献。"""

import json
from typing import Any

from infrastructure.backup import BackupContext, BackupRows, BackupTable
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ActionDefinition, ActionSyncTarget
from .schemas import ActionDefinitionDesign, ActionSourceSnapshot, ActionTargetSnapshot
from .service import normalize_action_name

_TARGET_TABLES = {
    "window": ("companion_action_packs", "companion_actions", "pack_id"),
    "desktop": ("desktop_video_sets", "desktop_video_actions", "set_id"),
}
_RESTORED_REASON = "备份恢复的动作制作需手动继续"


def _mapped(context: BackupContext, table: str, value: Any) -> int:
    mapped = context.id_map.get(table, {}).get(str(value))
    if mapped is None:
        raise ValueError(f"共享动作缺少 {table} 引用")
    return int(mapped)


def _prepare_definition(raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    ActionDefinitionDesign.model_validate(
        {
            **{
                name: raw[name]
                for name in (
                    "name",
                    "motion_description",
                    "clip_kind",
                    "duration_seconds",
                    "reason",
                )
            },
            "use_when": json.loads(raw["use_when_json"]),
            "avoid_when": json.loads(raw["avoid_when_json"]),
        },
    )
    if raw["status"] not in {"pending", "reviewing", "deferred", "approved", "reused", "rejected"}:
        raise ValueError("共享动作评审状态无效")
    if raw["source"] not in {"user_requested", "autonomous"}:
        raise ValueError("共享动作设计来源无效")
    if raw["normalized_name"] != normalize_action_name(raw["name"]):
        raise ValueError("共享动作规范名称无效")
    source = ActionSourceSnapshot.model_validate_json(payload["review_source_json"])
    if source.target_kind != raw["origin_kind"] or source.target_id != raw["origin_target_id"]:
        raise ValueError("共享动作评审来源与原始目标不一致")
    if source.source_visual_key != raw["source_visual_key"] or (
        raw.get("outfit_id") is not None and source.outfit_id != raw["outfit_id"]
    ):
        raise ValueError("共享动作评审来源与冻结着装不一致")
    updates: dict[str, Any] = {"user_id": context.user_id, "outfit_id": payload.get("outfit_id")}
    if source.identity is not None:
        avatar_id = context.id_map.get("avatar_assets", {}).get(str(source.identity.avatar_id))
        updates["identity"] = source.identity.model_copy(
            update={"avatar_id": int(avatar_id) if avatar_id is not None else 0},
        )
        if avatar_id is None:
            updates.update(ready=False, blocked_reason="原始身份已删除，可使用已有动作版本")
    payload["review_source_json"] = source.model_copy(update=updates).model_dump_json()
    # 同表复用引用和两类来源目标在依赖行齐备后再映射。
    payload["reuse_definition_id"] = None
    payload["fanout_pending"] = False
    if payload["status"] in {"pending", "reviewing", "deferred"}:
        payload["status"] = "deferred"
        payload["review_reason"] = _RESTORED_REASON


async def _existing_definition(
    db: AsyncSession,
    raw: dict[str, Any],
    payload: dict[str, Any],
    context: BackupContext,
) -> int | None:
    # 来源着装已删除的定义各自独立；NULL 着装不能作为跨定义合并依据。
    if context.mode != "merge" or payload.get("outfit_id") is None:
        return None
    existing = await db.scalar(
        select(ActionDefinition).where(
            ActionDefinition.user_id == context.user_id,
            ActionDefinition.outfit_id == payload["outfit_id"],
            ActionDefinition.source_visual_key == payload["source_visual_key"],
            ActionDefinition.normalized_name == payload["normalized_name"],
        ),
    )
    if existing is None:
        return None
    if any(
        getattr(existing, field) != payload[field]
        for field in (
            "motion_description",
            "use_when_json",
            "avoid_when_json",
            "clip_kind",
            "duration_seconds",
        )
    ):
        raise ValueError("合并的同名共享动作与现有冻结设计不一致。")
    return existing.id


async def _definition_rows(
    db: AsyncSession,
    rows: list[dict[str, Any]],
    mapping: dict[str, int | str],
    context: BackupContext,
) -> None:
    originals = {str(raw["id"]): raw for raw in rows}
    for raw in rows:
        if ("action_definitions", str(raw["id"])) not in context.rewriter.inserted_rows:
            continue
        definition = await db.get(ActionDefinition, int(mapping[str(raw["id"])]))
        if definition is None:
            raise ValueError("共享动作定义恢复失败")
        reused = raw.get("reuse_definition_id")
        if reused is not None:
            if str(reused) not in mapping or reused == raw["id"]:
                raise ValueError("共享动作复用引用无效")
            canonical = originals[str(reused)]
            if (
                raw["status"] != "reused"
                or canonical["status"] != "approved"
                or (
                    raw["outfit_id"],
                    raw["source_visual_key"],
                )
                != (canonical["outfit_id"], canonical["source_visual_key"])
            ):
                raise ValueError("共享动作复用目标不属于同一冻结着装来源")
            definition.reuse_definition_id = int(mapping[str(reused)])


def _prepare_target(raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    # 使用公开 DTO 校验判别字段，禁止将未知目标类别当作窗口目标。
    ActionTargetSnapshot.model_validate(
        {**{name: raw[name] for name in ActionTargetSnapshot.model_fields if name in raw}, "user_id": context.user_id},
    )
    container, action_table, _ = _TARGET_TABLES[raw["target_kind"]]
    payload["target_id"] = _mapped(context, container, raw["target_id"])
    payload["action_id"] = _mapped(context, action_table, raw["action_id"]) if raw.get("action_id") else None
    payload["automatic"] = False
    if raw["state"] != "excluded":
        payload["state"] = "manual"
        payload["reason"] = _RESTORED_REASON if raw["state"] != "manual" else raw.get("reason")


async def _restore_links(db: AsyncSession, rows: BackupRows, context: BackupContext) -> None:
    definitions = {str(raw["id"]): raw for raw in rows.get("action_definitions", [])}
    target_rows = {
        (raw["definition_id"], raw["target_kind"], raw["target_id"]): raw for raw in rows.get("action_sync_targets", [])
    }
    for raw in rows.get("action_sync_targets", []):
        definition = definitions.get(str(raw["definition_id"]))
        container, action_table, container_key = _TARGET_TABLES[raw["target_kind"]]
        containers = {str(item["id"]): item for item in rows.get(container, [])}
        target = containers.get(str(raw["target_id"]))
        if definition is None or target is None:
            raise ValueError("共享动作目标缺少定义或所属形象")
        if definition["outfit_id"] != target.get("outfit_id") or (
            definition["source_visual_key"] != target["source_visual_key"]
            and raw["state"] not in {"blocked", "excluded"}
        ):
            raise ValueError("共享动作目标的冻结着装来源不匹配")
        if raw.get("action_id") is not None:
            action = next((item for item in rows.get(action_table, []) if item["id"] == raw["action_id"]), None)
            if action is None or (action[container_key], action.get("definition_id")) != (
                raw["target_id"],
                raw["definition_id"],
            ):
                raise ValueError("共享动作版本不属于对应定义或目标")
        elif raw["state"] == "dispatched":
            raise ValueError("已提交的共享动作目标缺少版本")
    for kind, (_, action_table, container_key) in _TARGET_TABLES.items():
        for action in rows.get(action_table, []):
            if action.get("definition_id") is None:
                continue
            target = target_rows.get((action["definition_id"], kind, action[container_key]))
            if target is None or target["state"] != "excluded" and target.get("action_id") != action["id"]:
                raise ValueError("共享动作版本缺少对应的制作目标或排除记录")
    for old_id, raw in definitions.items():
        if ("action_definitions", old_id) not in context.rewriter.inserted_rows:
            continue
        definition = await db.get(ActionDefinition, _mapped(context, "action_definitions", old_id))
        if definition is None:
            raise ValueError("共享动作定义恢复失败")
        container = _TARGET_TABLES[raw["origin_kind"]][0]
        origin_id = context.id_map.get(container, {}).get(str(raw["origin_target_id"]))
        source = ActionSourceSnapshot.model_validate_json(definition.review_source_json)
        updates: dict[str, Any] = {"target_id": int(origin_id) if origin_id is not None else 0}
        if origin_id is None:
            updates.update(ready=False, blocked_reason="原始形象已删除，可使用已有动作版本")
        definition.origin_target_id = updates["target_id"]
        definition.review_source_json = source.model_copy(update=updates).model_dump_json()
    await db.flush()


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable(
            "action_definitions",
            ActionDefinition,
            foreign_keys={"outfit_id": "companion_outfits"},
            strict_references=True,
            prepare_payload=_prepare_definition,
            find_existing=_existing_definition,
            after_rows=_definition_rows,
        ),
        BackupTable(
            "action_sync_targets",
            ActionSyncTarget,
            foreign_keys={"definition_id": "action_definitions"},
            unique_keys=("definition_id", "target_kind", "target_id"),
            strict_references=True,
            prepare_payload=_prepare_target,
            restore_context=_restore_links,
        ),
    )
