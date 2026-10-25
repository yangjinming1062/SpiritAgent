"""消息保存时登记活文件引用；正文只保留展示与模型输入所需快照。"""

from collections import defaultdict
from collections.abc import Collection

from sqlalchemy import BigInteger, column, delete, inspect, select, values
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from features.assets.references import message_asset_paths
from features.assets.service import asset_paths_in_session, ensure_assets_in_session
from features.media.voice_queries import resolve_custom_voice_references

from .models import Message, MessageAsset, MessageVoiceRef

_FIELDS = ("content", "content_type", "role", "tool_calls", "media_json", "reply_json")


def message_voice_references(reply: object) -> set[str]:
    if not isinstance(reply, dict) or not isinstance(reply.get("bubbles"), list):
        return set()
    return {
        bubble["voice_id"]
        for bubble in reply["bubbles"]
        if isinstance(bubble, dict) and bubble.get("type") == "voice" and isinstance(bubble.get("voice_id"), str)
    }


async def sync_message_assets(db: AsyncSession) -> None:
    owners: dict[int, list[tuple[Message, set[str], bool]]] = defaultdict(list)
    voice_changes: dict[int, list[tuple[Message, set[str], bool]]] = defaultdict(list)
    for row in set(db.new) | set(db.dirty):
        if not isinstance(row, Message):
            continue
        state = inspect(row)
        if row not in db.new and not any(state.attrs[field].history.has_changes() for field in _FIELDS):
            continue
        owners[row.user_id].append((row, message_asset_paths(row, row.user_id), row in db.new))
        history = state.attrs.reply_json.history
        current = message_voice_references(row.reply_json)
        previous = message_voice_references(history.deleted[0]) if history.deleted else None
        if row in db.new or (history.has_changes() and current != previous):
            voice_changes[row.user_id].append((row, current, row in db.new))
    for user_id, records in owners.items():
        voice_records = voice_changes.get(user_id, [])
        voices = await resolve_custom_voice_references(
            db, user_id, {reference for _, references, _ in voice_records for reference in references}
        )
        paths = {path for _, values, _ in records for path in values}
        assets = await ensure_assets_in_session(db, user_id, paths | set(voices.asset_paths))
        await db.flush()
        existing = [row.id for row, _, created in records if not created]
        if existing:
            await db.execute(
                delete(MessageAsset).where(MessageAsset.user_id == user_id, MessageAsset.message_id.in_(existing))
            )
        bindings = [
            {"user_id": user_id, "message_id": row.id, "asset_id": assets[path]}
            for row, values, _ in records
            for path in sorted(values)
        ]
        if bindings:
            await db.execute(insert(MessageAsset), bindings)
        changed = [row.id for row, _, created in voice_records if not created]
        if changed:
            await db.execute(
                delete(MessageVoiceRef).where(
                    MessageVoiceRef.user_id == user_id, MessageVoiceRef.message_id.in_(changed)
                )
            )
        voice_bindings = [
            {"user_id": user_id, "message_id": row.id, "voice_id": voices.identities[reference]}
            for row, references, _ in voice_records
            for reference in sorted(references)
            if reference in voices.identities
        ]
        if voice_bindings:
            await db.execute(insert(MessageVoiceRef), voice_bindings)


async def commit_conversation(db: AsyncSession) -> None:
    await sync_message_assets(db)
    await db.commit()


async def flush_conversation(db: AsyncSession) -> None:
    await sync_message_assets(db)
    await db.flush()


async def message_binding_paths(db: AsyncSession, user_id: int) -> set[str]:
    ids = list(await db.scalars(select(MessageAsset.asset_id).where(MessageAsset.user_id == user_id)))
    return set((await asset_paths_in_session(db, user_id, ids)).values())


async def used_message_voice_ids(db: AsyncSession, user_id: int, candidates: Collection[int]) -> set[int]:
    """从声音候选出发做索引存在性检查，不扫描消息正文或全部历史引用。"""
    identifiers = sorted(set(candidates))
    result: set[int] = set()
    for offset in range(0, len(identifiers), 500):
        selected = (
            values(column("voice_id", BigInteger))
            .data([(identity,) for identity in identifiers[offset : offset + 500]])
            .alias("candidates")
        )
        used = (
            select(MessageVoiceRef.id)
            .where(MessageVoiceRef.user_id == user_id, MessageVoiceRef.voice_id == selected.c.voice_id)
            .exists()
        )
        result.update(await db.scalars(select(selected.c.voice_id).where(used)))
    return result
