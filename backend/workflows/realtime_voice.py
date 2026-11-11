"""通话输入、已有聊天受理与播放回执的协调；无独立人格或历史。"""

import asyncio
import contextlib
import io
import wave
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from features.conversation.http_contracts import PromptSubmitRequest
from features.conversation.replies import CompanionReply, TextBubble, VoiceBubble
from features.conversation.reply_audio import client_reply_bubbles, discard_reply_audio, prepare_reply_audio
from features.realtime_voice.contracts import MAX_INPUT_BYTES, SAMPLE_RATE, TurnVersion
from features.realtime_voice.session import CallSession
from infrastructure.ai.providers.base import ProviderCapabilityError
from infrastructure.ai.stt_service import stream_transcribe_audio, transcribe_audio
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.tts_service import stream_synthesize_speech
from infrastructure.assets.asset_store import resolve_asset_reference
from infrastructure.assets.paths import parse_companion_asset_path
from infrastructure.async_io import cancel_and_wait, run_blocking, wait_for_task
from infrastructure.logger import get_logger
from infrastructure.video_processing.ffmpeg import ffmpeg_stdout
from runtime.business_actor import BusinessActor
from runtime.desktop.connection import UserEventPublisher

from workflows.chat.operations import chat_operations

logger = get_logger(__name__)


def pcm_wav(pcm: bytes) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)
    return output.getvalue()


def asset_pcm(user_id: int, path: str) -> bytes:
    parsed = parse_companion_asset_path(path)
    if parsed is None or parsed[0] != user_id:
        raise ValueError("Voice asset does not belong to this account")
    resolved = resolve_asset_reference(path)
    if resolved is None:
        raise ValueError("Voice asset is unavailable")
    pcm = ffmpeg_stdout(
        [
            "-i",
            str(resolved[0]),
            "-t",
            "300",
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-c:a",
            "pcm_s16le",
            "-f",
            "s16le",
            "pipe:1",
        ],
        failure_message="语音解码失败",
    )
    if not pcm or len(pcm) >= SAMPLE_RATE * 2 * 300:
        raise ValueError("Voice asset exceeds the playback budget")
    return pcm


@dataclass(frozen=True)
class CallTurn:
    request_id: str
    version: TurnVersion


class _PartialAudioError(RuntimeError):
    """流式 TTS 已经发出部分声音，不能再回退到另一份音频。"""


class RealtimeVoicePipeline:
    def __init__(self, session: CallSession, actor: BusinessActor) -> None:
        self.session = session
        self.actor = actor
        self.confirmed_indices: list[int] = []
        self._base = UserEventPublisher(actor.user_id)
        self._operations = chat_operations(actor, event_publisher=self, reply_delivery=self)
        self._input: bytearray | None = None
        self._input_queue: asyncio.Queue[bytes | None] | None = None
        self._input_finished = False
        self._finish_task: asyncio.Task[None] | None = None
        self._cancel_task: asyncio.Task[None] | None = None
        self._turn: CallTurn | None = None
        self._partial_texts: list[str] = []

    async def begin_input(self, version: TurnVersion) -> None:
        if not await self.session.begin_input(version):
            return
        self._input = bytearray()
        # 4096 × 20 ms is above the 60 s input budget; a capability fallback can
        # therefore always receive the end sentinel without blocking the socket.
        self._input_queue = asyncio.Queue(maxsize=4096)
        self._input_finished = False
        self._partial_texts.clear()
        # Stop the previous turn without holding the websocket receive loop.  The
        # next recognition task waits for this settlement before admission, so
        # audio capture remains full duplex while chat history stays serialized.
        if self._turn is not None or self._finish_task is not None:
            finish_task, turn = self._finish_task, self._turn
            if finish_task is not None:
                finish_task.cancel()
            self._finish_task = None
            self._turn = None
            previous = self._cancel_task

            async def cancel_previous() -> None:
                if previous is not None:
                    with contextlib.suppress(Exception):
                        await wait_for_task(previous)
                await self._cancel_refs(finish_task, turn)

            self._cancel_task = asyncio.create_task(cancel_previous(), name="call.cancel")
            self.actor.account.track(self._cancel_task)
        self._finish_task = asyncio.create_task(self._recognize(version, self._input_queue), name="call.transcribe")
        self.actor.account.track(self._finish_task)

    async def cancel_input(self) -> None:
        """取消尚未提交的识别，不触碰已经受理的伙伴回合。"""
        self._input = None
        self._input_queue = None
        self._input_finished = True
        finish_task = self._finish_task
        self._finish_task = None
        await self._cancel_refs(finish_task, None)
        if self.session.current(self.session.version) and self.session.state == "thinking":
            await self.session.set_state("listening", self.session.version)

    def receive_audio(self, pcm: bytes) -> None:
        if self._input is None or self._input_finished or self.session.muted:
            return
        if len(self._input) + len(pcm) > MAX_INPUT_BYTES:
            self._input = None
            raise ValueError("单次发言超过 60 秒，请缩短后重试")
        self._input.extend(pcm)
        if self._input_queue is not None:
            with contextlib.suppress(asyncio.QueueFull):
                self._input_queue.put_nowait(pcm)

    async def finish_input(self, version: TurnVersion) -> None:
        if not self.session.current(version) or self._input is None or self._input_finished:
            return
        self._input_finished = True
        pcm_length = len(self._input)
        queue = self._input_queue
        if queue is not None:
            while True:
                try:
                    queue.put_nowait(None)
                    break
                except asyncio.QueueFull:
                    # The full PCM copy remains available for the explicit buffered
                    # fallback; make sure a slow streaming provider still sees end-of-input.
                    with contextlib.suppress(asyncio.QueueEmpty):
                        queue.get_nowait()
        if pcm_length < SAMPLE_RATE // 5:  # 少于 100ms 的声音不能形成有效发言。
            if self._finish_task is not None:
                self._finish_task.cancel()
                self._finish_task = None
            self._input = None
            self._input_queue = None
            await self.session.set_state("listening", version)
            return

    async def _wait_previous_cancel(self) -> None:
        task = self._cancel_task
        if task is None:
            return
        await wait_for_task(task)
        if self._cancel_task is task:
            self._cancel_task = None

    async def _input_chunks(self, queue: asyncio.Queue[bytes | None]):
        while True:
            chunk = await queue.get()
            if chunk is None:
                return
            yield chunk

    async def _stream_text(self, queue: asyncio.Queue[bytes | None], version: TurnVersion) -> str:
        final_sentences: dict[int, str] = {}
        current_sentence = ""
        next_sentence_id = 0
        events = stream_transcribe_audio(
            self.actor.user_id, self._input_chunks(queue), mime_type="audio/pcm;rate=24000"
        )
        try:
            async for event in events:
                if not self.session.current(version):
                    raise asyncio.CancelledError
                if not event.text.strip():
                    continue
                sentence_id = event.sentence_id
                if sentence_id is None:
                    next_sentence_id += 1
                    sentence_id = next_sentence_id
                if event.final:
                    final_sentences[sentence_id] = event.text.strip()
                    current_sentence = ""
                else:
                    current_sentence = event.text.strip()
                latest = "".join(final_sentences[index] for index in sorted(final_sentences)) + current_sentence
                await self.session.emit("transcript.partial", {"text": latest, "final": event.final}, version=version)
        finally:
            await events.aclose()
        return "".join(final_sentences[index] for index in sorted(final_sentences)) or current_sentence

    async def _recognize(self, version: TurnVersion, queue: asyncio.Queue[bytes | None]) -> None:
        try:
            await self._wait_previous_cancel()
            try:
                text = await self._stream_text(queue, version)
            except ProviderCapabilityError:
                # Streaming provider 尚未配置时保留整段路径作为明确降级；能力错误不会伪装成流式结果。
                while await queue.get() is not None:
                    pass
                pcm = bytes(self._input or b"")
                await self.session.set_state("thinking", version)
                text = (await transcribe_audio(self.actor.user_id, pcm_wav(pcm), "audio/wav")).strip()
            if not self.session.current(version):
                return
            if self.session.muted:
                await self.session.set_state("listening", version)
                return
            if not text:
                await self.session.set_state("listening", version)
                return
            await self.session.set_state("thinking", version)
            await self.session.emit("transcript.final", {"text": text}, version=version)
            turn = CallTurn(str(uuid4()), version)
            self._turn = turn
            # 受理任务沿用账户所有权；取消调用方仍先等待账本确认，再定向取消原请求。
            submitted = asyncio.create_task(
                self._operations.submit(
                    self.session.session_id,
                    PromptSubmitRequest(request_id=turn.request_id, text=text, response_preference="voice"),
                )
            )
            await wait_for_task(submitted)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Call transcription or admission failed", extra={"user_id": self.actor.user_id})
            await self.session.emit(
                "error", {"message": "本次语音未能处理，请重新说一次", "fatal": False}, version=version
            )
            await self.session.set_state("listening", version)
        finally:
            if self._input_queue is queue:
                self._input = None
                self._input_queue = None
                self._input_finished = True
            if self._finish_task is asyncio.current_task():
                self._finish_task = None

    async def _cancel_refs(self, finish_task: asyncio.Task[None] | None, turn: CallTurn | None) -> None:
        await cancel_and_wait([finish_task])
        if turn is None:
            return
        await self._operations.cancel({"session_id": self.session.session_id, "request_id": turn.request_id})
        runtime = self.actor.account.runtime_sessions.get(self.session.session_id)
        # cancel API only requests cancellation; the next turn must await tool/history settlement.
        if runtime is not None and runtime.chat_task is not None:
            await wait_for_task(asyncio.gather(runtime.chat_task, return_exceptions=True))
        if runtime is not None and runtime.settlement_task is not None:
            await wait_for_task(asyncio.gather(runtime.settlement_task, return_exceptions=True))

    async def cancel_current(self, *, clear_input: bool = True) -> None:
        if clear_input:
            self._input = None
            self._input_queue = None
            self._input_finished = True
        finish_task = self._finish_task
        turn = self._turn
        self._finish_task = None
        self._turn = None
        await self._cancel_refs(finish_task, turn)

    async def deliver_partial(self, index: int, bubble: dict[str, Any]) -> None:
        """交付已经闭合且独立可验证的文字气泡；语音气泡留给终态以保持音色校验完整。"""
        if bubble.get("type") != "text":
            raise ValueError("Only text bubbles can be delivered incrementally")
        validated = TextBubble.model_validate(bubble)
        turn = self._turn
        if turn is None or not self.session.current(turn.version):
            raise asyncio.CancelledError
        if any(text == validated.text for text in self._partial_texts):
            return
        await self.session.deliver_part(index, validated.model_dump(), turn.version)
        self._partial_texts.append(validated.text)
        await self.session.emit("transcript.assistant", {"text": validated.text}, version=turn.version)

    async def _deliver_stream_voice(self, bubble: VoiceBubble, version: TurnVersion) -> None:
        sent = False
        sequence = 0
        try:
            await self.session.set_state("speaking", version)
            async for chunk in stream_synthesize_speech(
                self.actor.user_id,
                bubble.text,
                voice=bubble.voice_id,
                language=bubble.language,
                speech_style=bubble.speech,
                task=AiTaskKey.companion_voice,
                preserve_performance=True,
            ):
                if not chunk.audio or not chunk.mime.startswith("audio/pcm") or len(chunk.audio) % 2:
                    raise ValueError("Streaming TTS returned invalid PCM")
                for offset in range(0, len(chunk.audio), 9600):
                    await self.session.send_audio(chunk.audio[offset : offset + 9600], version)
                    sent = True
                    sequence += 1
            if sequence == 0:
                raise ProviderCapabilityError("Streaming TTS returned no audio")
            await self.session.wait_played(sequence, version)
        except asyncio.CancelledError:
            raise
        except ProviderCapabilityError:
            raise
        except Exception as exc:
            if sent:
                raise _PartialAudioError("Streaming TTS stopped after audio was sent") from exc
            raise ProviderCapabilityError("Streaming TTS unavailable") from exc

    async def _deliver_asset_voice(self, part: CompanionReply, bubble: VoiceBubble, version: TurnVersion) -> None:
        await prepare_reply_audio(self.actor.user_id, part)
        if bubble.audio is None:
            raise RuntimeError("Call voice synthesis failed")
        pcm = await run_blocking(asset_pcm, self.actor.user_id, bubble.audio.url)
        await self.session.set_state("speaking", version)
        sequence = 0
        for offset in range(0, len(pcm), 9600):
            sequence = await self.session.send_audio(pcm[offset : offset + 9600], version)
        await self.session.wait_played(sequence, version)

    async def _deliver_text_fallback(self, reply: CompanionReply, index: int, version: TurnVersion) -> None:
        if not self.session.current(version):
            raise asyncio.CancelledError
        await self.session.emit("error", {"message": "语音暂时不可用，已显示文字", "fatal": False}, version=version)
        await self.session.deliver_part(index, client_reply_bubbles(reply)[index], version)

    async def deliver(self, reply: CompanionReply) -> None:
        self.confirmed_indices = []
        turn = self._turn
        if turn is None or not self.session.current(turn.version):
            raise asyncio.CancelledError
        partial_texts = list(self._partial_texts)
        self._partial_texts.clear()
        for index, bubble in enumerate(reply.bubbles):
            if not self.session.current(turn.version):
                raise asyncio.CancelledError
            if isinstance(bubble, TextBubble) and bubble.text in partial_texts:
                partial_texts.remove(bubble.text)
                self.confirmed_indices.append(index)
                continue
            if isinstance(bubble, VoiceBubble):
                # 原始音色和演绎冻结在已验证气泡中，复用普通语音合成及资产保护。
                part = reply.model_copy(update={"bubbles": [bubble]})
                existing_audio = bubble.audio
                try:
                    await self._deliver_stream_voice(bubble, turn.version)
                except _PartialAudioError:
                    raise
                except ProviderCapabilityError:
                    try:
                        await self._deliver_asset_voice(part, bubble, turn.version)
                    except asyncio.CancelledError:
                        if existing_audio is None:
                            await discard_reply_audio(part)
                        raise
                    except Exception:
                        if existing_audio is None:
                            await discard_reply_audio(part)
                        await self._deliver_text_fallback(reply, index, turn.version)
                    else:
                        # 完整语音播放确认后才发布台词。被打断的半句没有可靠字词时间戳，不当成已说完。
                        bubble.audio = part.bubbles[0].audio
                        self.confirmed_indices.append(index)
                        await self.session.emit("transcript.assistant", {"text": bubble.text}, version=turn.version)
                except asyncio.CancelledError:
                    if existing_audio is None:
                        await discard_reply_audio(part)
                    raise
                except Exception:
                    if existing_audio is None:
                        await discard_reply_audio(part)
                    # A voice asset is an enhancement to the validated reply;
                    # if synthesis/decoding fails, preserve the text bubble so
                    # the turn can still complete and be recovered in history.
                    if not self.session.current(turn.version):
                        raise asyncio.CancelledError
                    await self.session.emit(
                        "error",
                        {"message": "语音暂时不可用，已显示文字", "fatal": False},
                        version=turn.version,
                    )
                    await self.session.deliver_part(index, client_reply_bubbles(reply)[index], turn.version)
                    self.confirmed_indices.append(index)
            else:
                await self.session.deliver_part(index, client_reply_bubbles(reply)[index], turn.version)
                self.confirmed_indices.append(index)

    async def push_event(self, event_type: str, payload: Any = None, session_id: str | None = None) -> None:
        await self._base.push_event(event_type, payload, session_id=session_id)
        turn = self._turn
        if not isinstance(payload, dict) or turn is None or payload.get("request_id") != turn.request_id:
            return
        if not self.session.current(turn.version):
            return
        if event_type == "error":
            await self.session.emit(
                "error", {"message": payload.get("message", "本次回复未完成"), "fatal": False}, version=turn.version
            )
        if event_type == "session.state" and not payload.get("running"):
            await self.session.set_state("listening", turn.version)
            await self.session.emit("turn.complete", {"status": payload.get("status")}, version=turn.version)

    async def close(self) -> None:
        try:
            await self._wait_previous_cancel()
            await self.cancel_current()
        finally:
            await self.session.close()
