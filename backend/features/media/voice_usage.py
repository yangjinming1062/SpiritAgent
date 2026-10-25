"""声音拥有者通过装配端口核对历史引用，不反向访问消费方 ORM。"""

from collections.abc import Awaitable, Callable, Collection

from sqlalchemy.ext.asyncio import AsyncSession

VoiceUsageReader = Callable[[AsyncSession, int, Collection[int]], Awaitable[set[int]]]
_reader: VoiceUsageReader | None = None


def set_voice_usage_reader(reader: VoiceUsageReader) -> None:
    global _reader
    _reader = reader


async def used_voice_ids(db: AsyncSession, user_id: int, candidates: Collection[int]) -> set[int]:
    if not candidates:
        return set()
    if _reader is None:
        raise RuntimeError("Voice usage reader has not been registered")
    return await _reader(db, user_id, candidates)
