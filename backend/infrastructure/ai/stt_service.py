"""STT 供应商链调用：真实增量与累计音频快照共用同一路识别事件。"""

import asyncio
import io
import struct
import wave
from collections.abc import AsyncGenerator, AsyncIterable, Callable
from contextlib import aclosing, suppress
from dataclasses import dataclass

from infrastructure.ai.llm_client import MissingLlmConfigError, build_provider, resolve_provider_chain
from infrastructure.ai.llm_fallback import execute_with_fallback
from infrastructure.ai.providers.base import ServiceType, STTProvider, STTStreamEvent
from infrastructure.ai.providers.registry import provider_supports_streaming
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.async_io import cancel_and_wait
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger

logger = get_logger(__name__)

# 非增量供应商的快照节奏与预算：快照只影响字幕和结束等待，最终结果仍以完整音频为准。
SNAPSHOT_INTERVAL_SECONDS = 1.2
SNAPSHOT_MAX_REQUESTS = 4
SNAPSHOT_MIN_AUDIO_SECONDS = 1.0
SNAPSHOT_SILENCE_WINDOW_SECONDS = 0.1
SNAPSHOT_SILENCE_RMS = 0.006


@dataclass(frozen=True)
class SpeechAudioSource:
    """一路待识别语音：分片只消费一次，快照返回当前累计的 16 位单声道 PCM。"""

    chunks: AsyncIterable[bytes]
    snapshot: Callable[[], bytes]
    finished: asyncio.Event
    sample_rate: int


def _pcm_wav(pcm: bytes, sample_rate: int) -> bytes:
    """把裸 PCM 包成 WAV，供只接受整段音频的供应商识别。"""
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return output.getvalue()


def _silent_tail(pcm: bytes, sample_rate: int) -> bool:
    """快照之后的音频是否全是静音；按窗口取最大能量，避免短促语音被平均掉。"""
    window = max(2, int(sample_rate * 2 * SNAPSHOT_SILENCE_WINDOW_SECONDS))
    for start in range(0, len(pcm), window):
        chunk = pcm[start : start + window]
        samples = len(chunk) // 2
        if samples == 0:
            continue
        total = sum(value * value for (value,) in struct.iter_unpack("<h", chunk[: samples * 2]))
        if (total / samples) ** 0.5 / 32768 >= SNAPSHOT_SILENCE_RMS:
            return False
    return True


async def transcribe_audio(user_id: int, audio: bytes, mime_type: str, language: str = "auto") -> str:
    """走供应商链整段转写，返回识别文本；链解析为空抛 MissingLlmConfigError。"""
    async with SESSION_LOCAL() as db:
        chain = await resolve_provider_chain(db, user_id, ServiceType.stt, task=AiTaskKey.speech_input)

    result = await execute_with_fallback(
        chain,
        STTProvider,
        lambda provider: provider.transcribe(audio, mime_type=mime_type, language=language),
        user_id=user_id,
    )
    return result.text


async def _snapshot_request(user_id: int, pcm: bytes, sample_rate: int, language: str) -> tuple[int, str]:
    text = await transcribe_audio(user_id, _pcm_wav(pcm, sample_rate), "audio/wav", language)
    return len(pcm), text.strip()


async def _snapshot_result(task: asyncio.Task[tuple[int, str]], user_id: int) -> tuple[int, str]:
    """快照失败只保留上一次结果，不影响最终识别。"""
    try:
        return await task
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("STT snapshot request failed", extra={"user_id": user_id}, exc_info=True)
        return 0, ""


async def _snapshot_recognize(
    user_id: int, source: SpeechAudioSource, *, language: str
) -> AsyncGenerator[STTStreamEvent]:
    """非增量供应商的滚动快照：边说话边出临时结果，结束时尽量复用最后一次识别。"""
    min_bytes = int(source.sample_rate * 2 * SNAPSHOT_MIN_AUDIO_SECONDS)
    pending: asyncio.Task[tuple[int, str]] | None = None
    requests = 0
    last_bytes, last_text = 0, ""
    try:
        while not source.finished.is_set():
            if pending is not None and pending.done():
                snapshot_bytes, text = await _snapshot_result(pending, user_id)
                pending = None
                if text:
                    last_bytes, last_text = snapshot_bytes, text
                    yield STTStreamEvent(text=text)
            elif pending is None and requests < SNAPSHOT_MAX_REQUESTS:
                if len(pcm := source.snapshot()) >= min_bytes:
                    requests += 1
                    pending = asyncio.create_task(
                        _snapshot_request(user_id, pcm, source.sample_rate, language), name="stt.snapshot"
                    )
            with suppress(TimeoutError):
                await asyncio.wait_for(source.finished.wait(), timeout=SNAPSHOT_INTERVAL_SECONDS)
        if pending is not None:
            snapshot_bytes, text = await _snapshot_result(pending, user_id)
            pending = None
            if text:
                last_bytes, last_text = snapshot_bytes, text
        final_pcm = source.snapshot()
        if not final_pcm:
            return
        reusable = (
            bool(last_text)
            and len(final_pcm) >= last_bytes
            and _silent_tail(final_pcm[last_bytes:], source.sample_rate)
        )
        if reusable:
            logger.info("STT snapshot reused", extra={"user_id": user_id, "requests": requests})
            yield STTStreamEvent(text=last_text, final=True)
            return
        text = (await transcribe_audio(user_id, _pcm_wav(final_pcm, source.sample_rate), "audio/wav", language)).strip()
        logger.info("STT snapshot finalized", extra={"user_id": user_id, "requests": requests})
        if text:
            yield STTStreamEvent(text=text, final=True)
    finally:
        if pending is not None:
            await cancel_and_wait([pending])


async def stream_transcribe_audio(
    user_id: int,
    source: SpeechAudioSource,
    *,
    language: str = "auto",
) -> AsyncGenerator[STTStreamEvent]:
    """按配置的能力分派：声明真实增量时持续识别，其余供应商使用累计音频快照。

    流式输入不可无损重放，因此不在识别中途切换供应商；同一路音频始终由首个可用配置
    处理，运行时错误由通话协调层按当前回合处理。
    """
    async with SESSION_LOCAL() as db:
        chain = await resolve_provider_chain(db, user_id, ServiceType.stt, task=AiTaskKey.speech_input)
    if not chain:
        raise MissingLlmConfigError("no STT provider configured")
    streaming = [
        config for config in chain if provider_supports_streaming(ServiceType.stt, config.provider_name, config)
    ]
    if streaming:
        provider = build_provider(streaming[0], STTProvider)
        events = provider.transcribe_stream(
            source.chunks, mime_type=f"audio/pcm;rate={source.sample_rate}", language=language
        )
        async with aclosing(events) as stream:
            async for event in stream:
                yield event
        return
    async with aclosing(_snapshot_recognize(user_id, source, language=language)) as events:
        async for event in events:
            yield event
