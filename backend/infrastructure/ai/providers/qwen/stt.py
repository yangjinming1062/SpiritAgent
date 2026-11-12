import asyncio
import contextlib
import json
import re
import struct
from collections.abc import AsyncIterable, AsyncIterator
from typing import ClassVar
from urllib.parse import urlsplit
from uuid import uuid4

from websockets.asyncio.client import ClientConnection, connect

from infrastructure.ai.providers.base import (
    ProviderCapabilityError,
    ProviderConfig,
    ProviderError,
    STTProvider,
    STTResult,
    STTStreamEvent,
)
from infrastructure.ai.providers.openai_compat import transcribe_input_audio
from infrastructure.network import configured_websocket_proxy


class QwenSTTProvider(STTProvider):
    """通过千问 Qwen-ASR 的 chat.completions + input_audio 提供 STT（仅识别，不进入陪伴对话）。"""

    provider_name = "qwen"
    DEFAULT_BASE_URL: ClassVar[str] = "https://maas.qianwenaiapi.com/compatible-mode/v1"
    supports_streaming: ClassVar[bool] = True
    # This adapter implements the Qwen-Audio-ASR-Streaming task protocol only.
    # Qwen-ASR-Realtime and Qwen-Audio-ASR-Message use different WebSocket
    # event contracts and must not be routed here by a loose ``stream`` check.
    STREAMING_MODELS: ClassVar[frozenset[str]] = frozenset(
        {
            "qwen-audio-3.1-asr-flash-streaming",
            "qwen-audio-3.0-asr-flash-streaming",
        }
    )
    STREAMING_SAMPLE_RATE: ClassVar[int] = 16_000

    async def transcribe(self, audio: bytes, *, mime_type: str = "audio/wav", language: str = "auto") -> STTResult:
        return await transcribe_input_audio(
            self.config,
            audio,
            mime_type=mime_type,
            asr_options={"language": language} if language and language != "auto" else None,
        )

    @classmethod
    def supports_streaming_config(cls, config: ProviderConfig) -> bool:
        model = config.model.strip().lower()
        if model not in cls.STREAMING_MODELS:
            return False
        host = urlsplit(config.base_url).hostname or ""
        # Workspace-specific DashScope endpoints identify the region and are the
        # documented endpoint for this task protocol. The generic qianwen API
        # endpoint and the /realtime protocol are intentionally not accepted.
        return host.endswith(".maas.aliyuncs.com") and host.count(".") >= 3

    def _stream_url(self) -> str:
        parsed = urlsplit(self.config.base_url)
        host = parsed.hostname or ""
        if self.supports_streaming_config(self.config):
            return f"wss://{host}/api-ws/v1/inference"
        raise ProviderCapabilityError(
            "Qwen realtime STT requires a supported streaming model and workspace-specific DashScope endpoint"
        )

    @staticmethod
    def _source_rate(mime_type: str) -> int:
        match = re.search(r"(?:^|;)\s*rate\s*=\s*(\d+)", mime_type, re.IGNORECASE)
        if match is None:
            return 24_000
        rate = int(match.group(1))
        if rate <= 0:
            raise ProviderCapabilityError("Qwen realtime STT requires a positive PCM sample rate")
        return rate

    @classmethod
    async def _resample_pcm(
        cls, audio: AsyncIterable[bytes], source_rate: int, target_rate: int
    ) -> AsyncIterator[bytes]:
        """Convert little-endian mono PCM16 while retaining streaming boundaries."""
        if source_rate == target_rate:
            async for chunk in audio:
                if len(chunk) % 2:
                    raise ProviderCapabilityError("Qwen realtime STT requires complete PCM16 samples")
                if chunk:
                    yield chunk
            return

        samples: list[int] = []
        position = 0.0
        step = source_rate / target_rate
        async for chunk in audio:
            if len(chunk) % 2:
                raise ProviderCapabilityError("Qwen realtime STT requires complete PCM16 samples")
            if chunk:
                samples.extend(value[0] for value in struct.iter_unpack("<h", chunk))
            output = bytearray()
            while position + 1 < len(samples):
                index = int(position)
                fraction = position - index
                value = round(samples[index] + (samples[index + 1] - samples[index]) * fraction)
                output.extend(struct.pack("<h", max(-32768, min(32767, value))))
                position += step
            consumed = int(position)
            if consumed:
                del samples[:consumed]
                position -= consumed
            if output:
                yield bytes(output)

        # Flush the final sample so a short utterance is not truncated by the
        # interpolation look-ahead used between chunks.
        if samples:
            yield struct.pack("<h", samples[-1])

    @staticmethod
    def _event(raw: str | bytes) -> dict:
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ProviderError("Qwen streaming STT returned invalid JSON", status_code=502) from exc
        if not isinstance(value, dict):
            raise ProviderError("Qwen streaming STT returned an invalid event", status_code=502)
        return value

    async def _send_audio(self, websocket: ClientConnection, audio: AsyncIterable[bytes], task_id: str) -> None:
        async for chunk in audio:
            if chunk:
                await websocket.send(chunk)
        await websocket.send(
            json.dumps(
                {
                    "header": {"action": "finish-task", "task_id": task_id, "streaming": "duplex"},
                    "payload": {"input": {}},
                }
            )
        )

    async def transcribe_stream(
        self,
        audio: AsyncIterable[bytes],
        *,
        mime_type: str = "audio/pcm;rate=24000",
        language: str = "auto",
    ) -> AsyncIterator[STTStreamEvent]:
        if "pcm" not in mime_type.lower():
            raise ProviderCapabilityError("Qwen realtime STT requires PCM input")
        task_id = str(uuid4())
        model = self.config.model.strip()
        if not self.supports_streaming_config(self.config):
            raise ProviderCapabilityError(
                "Qwen realtime STT requires a supported streaming model and workspace-specific DashScope endpoint"
            )
        source_rate = self._source_rate(mime_type)
        sample_rate = self.STREAMING_SAMPLE_RATE
        parameters = {"format": "pcm", "sample_rate": sample_rate, "heartbeat": True}
        language = language.split("-", 1)[0].lower() if language and language != "auto" else ""
        if language:
            parameters["language_hints"] = [language]
        websocket: ClientConnection | None = None
        sender: asyncio.Task[None] | None = None
        try:
            websocket = await connect(
                self._stream_url(),
                additional_headers={"Authorization": f"Bearer {self.config.api_key}"},
                open_timeout=30,
                proxy=configured_websocket_proxy(),
            )
            await websocket.send(
                json.dumps(
                    {
                        "header": {"action": "run-task", "task_id": task_id, "streaming": "duplex"},
                        "payload": {
                            "task_group": "audio",
                            "task": "asr",
                            "function": "recognition",
                            "model": model,
                            "parameters": parameters,
                            "input": {},
                        },
                    }
                )
            )
            async with asyncio.timeout(30):
                started = self._event(await websocket.recv())
            if started.get("header", {}).get("event") != "task-started":
                raise ProviderError("Qwen realtime STT task did not start", status_code=502, body=started)
            sender = asyncio.create_task(
                self._send_audio(websocket, self._resample_pcm(audio, source_rate, sample_rate), task_id),
                name="qwen.stt.audio",
            )
            while True:
                async with asyncio.timeout(120):
                    event = self._event(await websocket.recv())
                header = event.get("header") or {}
                event_type = header.get("event")
                if event_type == "task-failed":
                    raise ProviderError(
                        str(header.get("error_message") or "Qwen realtime STT failed"), status_code=502, body=event
                    )
                if event_type == "result-generated":
                    payload = event.get("payload") or {}
                    output = payload.get("output") or {}
                    sentence = output.get("sentence") if isinstance(output, dict) else None
                    if not isinstance(sentence, dict) or sentence.get("heartbeat"):
                        continue
                    text = sentence.get("text")
                    if not isinstance(text, str):
                        continue
                    yield STTStreamEvent(
                        text=text,
                        final=sentence.get("sentence_end") is True,
                        language=language or None,
                        sentence_id=sentence.get("sentence_id")
                        if isinstance(sentence.get("sentence_id"), int)
                        else None,
                    )
                elif event_type == "task-finished":
                    break
        finally:
            if sender is not None:
                sender.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await sender
            if websocket is not None:
                with contextlib.suppress(Exception):
                    await websocket.close()
