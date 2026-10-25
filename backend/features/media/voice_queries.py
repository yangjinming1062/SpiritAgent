"""音色引用解析和资产查询；仅在媒体模块内访问音色持久模型。"""

import json
import re
from collections.abc import Collection

from infrastructure.assets.asset_store import client_asset_url
from infrastructure.text import ensure_utc, utc_now
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .voice_contracts import CustomVoiceSnapshot, CustomVoiceSummary, VoiceReferenceBindings
from .voice_models import CustomVoice

_REFERENCE = re.compile(r"([a-z0-9_]+):custom:profile:([a-f0-9]{32})\Z")


def custom_voice_reference(provider: str, token: str) -> str:
    return f"{provider}:custom:profile:{token}"


def custom_voice_tokens(values: object) -> set[str]:
    if isinstance(values, dict):
        return set().union(*(custom_voice_tokens(value) for value in values.values()))
    if isinstance(values, (list, tuple)):
        return set().union(*(custom_voice_tokens(value) for value in values))
    if not isinstance(values, str):
        return set()
    match = _REFERENCE.fullmatch(values)
    if match:
        return {match[2]}
    if values.lstrip().startswith(("{", "[")):
        try:
            return custom_voice_tokens(json.loads(values))
        except (ValueError, RecursionError):
            return set()
    return set()


async def _custom_voice_row(db: AsyncSession, user_id: int, voice_id: str, *, lock: bool = False) -> CustomVoice | None:
    match = _REFERENCE.fullmatch(voice_id)
    if match is None:
        if ":custom:" in voice_id:
            raise ValueError("自定义声音须使用已保存的方案")
        return None
    query = select(CustomVoice).where(CustomVoice.user_id == user_id, CustomVoice.token == match[2])
    row = await db.scalar(query.with_for_update() if lock else query)
    if row is None or row.kind == "sample" or row.provider != match[1]:
        raise ValueError("该声音不存在或不属于当前账户")
    if row.version == 0 and (row.expires_at is None or ensure_utc(row.expires_at) <= utc_now()):
        raise ValueError("试听方案已过期，请重新生成")
    if row.kind == "clone" and not row.reference_path:
        raise ValueError("声音样本已不可读取，请重新录制或上传")
    return row


async def resolve_custom_voice(db: AsyncSession, user_id: int, voice_id: str) -> CustomVoiceSnapshot | None:
    row = await _custom_voice_row(db, user_id, voice_id)
    if row is None:
        return None
    return CustomVoiceSnapshot(
        voice_id,
        row.provider,
        row.model,
        row.model_card_id,
        row.provider_voice_id,
        row.prompt,
        row.reference_path,
    )


async def resolve_custom_voice_references(
    db: AsyncSession, user_id: int, references: Collection[str]
) -> VoiceReferenceBindings:
    """批量绑定历史使用的本地版本；键共享锁与版本删除串行，外部声音原样保留。"""
    local: dict[str, tuple[str, str]] = {}
    for reference in references:
        match = _REFERENCE.fullmatch(reference)
        if match is not None:
            local[reference] = (match[1], match[2])
        elif ":custom:" in reference:
            raise ValueError("自定义声音须使用已保存的方案")
    if not local:
        return VoiceReferenceBindings({}, ())
    rows = list(
        await db.execute(
            select(
                CustomVoice.id,
                CustomVoice.provider,
                CustomVoice.token,
                CustomVoice.kind,
                CustomVoice.reference_path,
                CustomVoice.preview_path,
            )
            .where(
                CustomVoice.user_id == user_id,
                CustomVoice.token.in_({token for _, token in local.values()}),
                CustomVoice.kind.in_(("design", "clone")),
            )
            .order_by(CustomVoice.id)
            .with_for_update(read=True, key_share=True)
        )
    )
    found = {(row.provider, row.token): row.id for row in rows}
    if any(key not in found for key in local.values()):
        raise ValueError("消息引用的声音版本不存在或不属于当前账户")
    if any(row.kind == "clone" and not row.reference_path for row in rows):
        raise ValueError("声音样本已不可读取")
    paths = tuple(sorted({path for row in rows for path in (row.reference_path, row.preview_path) if path}))
    return VoiceReferenceBindings({reference: found[key] for reference, key in local.items()}, paths)


async def confirmed_custom_voices(
    db: AsyncSession,
    user_id: int,
    available_models: Collection[tuple[str, str, str]],
) -> list[CustomVoiceSummary]:
    rows = await db.scalars(
        select(CustomVoice)
        .where(CustomVoice.user_id == user_id, CustomVoice.is_current.is_(True))
        .order_by(CustomVoice.id),
    )
    return [
        CustomVoiceSummary(
            candidate_id=row.token,
            kind=row.kind,
            voice_id=custom_voice_reference(row.provider, row.token),
            prompt=row.prompt,
            preview_audio_url=client_asset_url(row.preview_path) if row.preview_path else "",
            reference_audio_url=client_asset_url(row.reference_path) if row.reference_path else None,
            provider=row.provider,
            model=row.model,
            version=row.version,
            available=(row.provider, row.model, row.model_card_id) in available_models,
        )
        for row in rows
    ]


async def voice_asset_references(db: AsyncSession, user_id: int, tokens: Collection[str]) -> set[str]:
    if not tokens:
        return set()
    return {
        path
        for path in await db.scalars(
            select(CustomVoice.reference_path).where(CustomVoice.user_id == user_id, CustomVoice.token.in_(tokens)),
        )
        if path
    }
