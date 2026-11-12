"""通话输入、已有聊天受理与播放回执的协调；无独立人格或历史。"""

import asyncio
import contextlib
import io
import wave
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from features.conversation.http_contracts import PromptSubmitRequest
from features.conversation.replies import CompanionReply, TextBubble, VoiceBubble
from features.conversation.reply_audio import (
    client_reply_bubbles,
    discard_reply_audio,
    prepare_reply_audio,
    save_stream_reply_audio,
)
from features.media.speech import stream_synthesize_speech
from features.realtime_voice.contracts import MAX_INPUT_BYTES, SAMPLE_RATE, TurnVersion
from features.realtime_voice.session import CallSession
from infrastructure.ai.providers.base import ProviderCapabilityError
from infrastructure.ai.stt_service import stream_transcribe_audio, transcribe_audio
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets.asset_store import resolve_asset_reference
from infrastructure.assets.paths import parse_companion_asset_path
from infrastructure.async_io import cancel_and_wait, run_blocking, wait_for_task
from infrastructure.logger import get_logger
from infrastructure.video_processing.ffmpeg import ffmpeg_stdout
from runtime.business_actor import BusinessActor
from runtime.desktop.connection import UserEventPublisher

from workflows.chat.operations import chat_operations
from workflows.chat.reply_delivery import companion_reply_source

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


class CallInputOverflowError(ValueError):
    """实时识别输入队列溢出；当前发言必须作废并重新开始。"""


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
        # Store the normalized bubble that was actually delivered so terminal
        # persistence can reuse its audio asset and identity.
        self._partial_delivered: dict[int, TextBubble | VoiceBubble] = {}
        self._partial_queue: asyncio.Queue[tuple[int, dict[str, Any], asyncio.Future[None]] | None] | None = None
        self._partial_worker: asyncio.Task[None] | None = None
        self._partial_futures: dict[int, asyncio.Future[None]] = {}
        self._partial_worker_done: asyncio.Event | None = None
        self._partial_worker_error: BaseException | None = None
        self._input_error: str | None = None

    async def begin_input(self, version: TurnVersion) -> None:
        if not await self.session.begin_input(version):
            return
        self._input = bytearray()
        # 4096 × 20 ms is above the 60 s input budget; a capability fallback can
        # therefore always receive the end sentinel without blocking the socket.
        self._input_queue = asyncio.Queue(maxsize=4096)
        self._input_finished = False
        self._input_error = None
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
                # The cancelled submission has had a chance to persist its
                # independently confirmed bubbles before the next turn starts.
                self._partial_delivered.clear()

            self._cancel_task = asyncio.create_task(cancel_previous(), name="call.cancel")
            self.actor.account.track(self._cancel_task)
        else:
            self._partial_delivered.clear()
        self._finish_task = asyncio.create_task(self._recognize(version, self._input_queue), name="call.transcribe")
        self.actor.account.track(self._finish_task)

    async def cancel_input(self) -> None:
        """取消尚未提交的识别，不触碰已经受理的伙伴回合。"""
        self._input = None
        self._input_queue = None
        self._input_finished = True
        self._input_error = None
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
            self._input_finished = True
            self._input_error = "duration"
            self._input_queue = None
            if self._finish_task is not None:
                self._finish_task.cancel()
            raise ValueError("单次发言超过 60 秒，请缩短后重试")
        self._input.extend(pcm)
        if self._input_queue is not None:
            try:
                self._input_queue.put_nowait(pcm)
            except asyncio.QueueFull as exc:
                self._input = None
                self._input_finished = True
                self._input_error = "backpressure"
                self._input_queue = None
                if self._finish_task is not None:
                    self._finish_task.cancel()
                raise CallInputOverflowError("实时识别输入过载，请重新开始发言") from exc

    async def finish_input(self, version: TurnVersion) -> None:
        if not self.session.current(version) or self._input is None or self._input_finished:
            return
        self._input_finished = True
        pcm_length = len(self._input)
        queue = self._input_queue
        if queue is not None:
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                # Dropping an older frame would make the final transcript
                # unknowingly depend on a hole in the recording. Invalidate the
                # input and stop recognition instead of forcing a bad fallback.
                self._input = None
                self._input_queue = None
                self._input_error = "backpressure"
                finish_task = self._finish_task
                self._finish_task = None
                await cancel_and_wait([finish_task])
                await self.session.set_state("listening", version)
                await self.session.emit(
                    "error", {"message": "语音接收过载，请重新开始发言", "fatal": False}, version=version
                )
                return
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
        current_sentence_id: int | None = None
        next_sentence_id = 0

        def join_sentences(parts: list[str]) -> str:
            result = ""
            punctuation = "，。！？；：,.!?;:)]}»、"
            for part in parts:
                if not part:
                    continue
                if not result:
                    result = part
                elif result.endswith((" ", "\n")) or part[0] in punctuation:
                    result += part
                elif result[-1].isascii() and part[0].isascii() and (result[-1].isalnum() or part[0].isalnum()):
                    result += " " + part
                else:
                    result += part
            return result

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
                    if current_sentence_id is None:
                        next_sentence_id += 1
                        current_sentence_id = next_sentence_id
                    sentence_id = current_sentence_id
                if event.final:
                    final_sentences[sentence_id] = event.text.strip()
                    current_sentence = ""
                    current_sentence_id = None
                else:
                    current_sentence = event.text.strip()
                latest = join_sentences(
                    [*(final_sentences[index] for index in sorted(final_sentences)), current_sentence]
                )
                await self.session.emit("transcript.partial", {"text": latest, "final": event.final}, version=version)
        finally:
            await events.aclose()
        # Interim recognition is UI feedback only. A sentence without a final
        # event must not become a user message when the stream closes.
        return join_sentences([final_sentences[index] for index in sorted(final_sentences)])

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
            if self._input_error is not None:
                await self.session.set_state("listening", version)
                return
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
            self._start_partial_worker(turn.version)
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
            await self._cancel_partial_worker()
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
        await self._cancel_partial_worker()
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

    @staticmethod
    def _bubble_identity(bubble: TextBubble | VoiceBubble) -> dict[str, Any]:
        if isinstance(bubble, TextBubble):
            return {"type": "text", "text": bubble.text}
        return {
            "type": "voice",
            "text": bubble.text,
            "speech": bubble.speech.model_dump(mode="json"),
            "voice_id": bubble.voice_id,
            "language": bubble.language,
        }

    def _start_partial_worker(self, version: TurnVersion) -> None:
        self._partial_queue = asyncio.Queue(maxsize=4)
        self._partial_futures.clear()
        self._partial_worker_done = asyncio.Event()
        self._partial_worker_error = None
        self._partial_worker = asyncio.create_task(
            self._run_partial_worker(self._partial_queue, version), name="call.reply-delivery"
        )
        self.actor.account.track(self._partial_worker)

    async def _run_partial_worker(
        self, queue: asyncio.Queue[tuple[int, dict[str, Any], asyncio.Future[None]] | None], version: TurnVersion
    ) -> None:
        try:
            while True:
                item = await queue.get()
                if item is None:
                    return
                index, bubble, future = item
                try:
                    await self._deliver_partial_now(index, bubble, version)
                except asyncio.CancelledError:
                    if not future.done():
                        future.cancel()
                    raise
                except Exception as exc:
                    if not future.done():
                        future.set_exception(exc)
                    self._partial_worker_error = exc
                    return
                else:
                    if not future.done():
                        future.set_result(None)
        finally:
            if self._partial_worker_done is not None:
                self._partial_worker_done.set()
            while True:
                try:
                    pending = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if pending is not None and not pending[2].done():
                    if self._partial_worker_error is not None:
                        pending[2].set_exception(self._partial_worker_error)
                    else:
                        pending[2].cancel()

    async def enqueue_partial(self, index: int, bubble: dict[str, Any]) -> asyncio.Future[None]:
        turn = self._turn
        queue = self._partial_queue
        if turn is None or queue is None or not self.session.current(turn.version):
            raise asyncio.CancelledError
        done = self._partial_worker_done
        if done is None or done.is_set():
            raise self._partial_worker_error or asyncio.CancelledError
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._partial_futures[index] = future
        put_task = asyncio.create_task(queue.put((index, bubble, future)))
        done_task = asyncio.create_task(done.wait())
        finished, _ = await asyncio.wait({put_task, done_task}, return_when=asyncio.FIRST_COMPLETED)
        if done_task in finished and not put_task.done():
            put_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await put_task
            raise self._partial_worker_error or asyncio.CancelledError
        done_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await done_task
        return future

    async def _finish_partial_worker(self) -> None:
        queue, worker = self._partial_queue, self._partial_worker
        if queue is None or worker is None:
            return
        if self._partial_worker_done is None or not self._partial_worker_done.is_set():
            await queue.put(None)
        await wait_for_task(worker)
        self._partial_queue = None
        self._partial_worker = None
        self._partial_worker_done = None
        errors = [
            future.exception() for future in self._partial_futures.values() if future.done() and not future.cancelled()
        ]
        self._partial_futures.clear()
        for error in errors:
            if error is not None and not isinstance(error, ProviderCapabilityError):
                raise error

    async def _cancel_partial_worker(self) -> None:
        worker = self._partial_worker
        self._partial_worker = None
        self._partial_queue = None
        self._partial_worker_error = asyncio.CancelledError()
        self._partial_futures.clear()
        if worker is not None:
            await cancel_and_wait([worker])
        self._partial_worker_done = None

    async def _deliver_partial_now(self, index: int, bubble: dict[str, Any], version: TurnVersion) -> None:
        """交付已经按终态规则标准化且保持顺序的文字或语音气泡。"""
        if bubble.get("type") == "text":
            validated: TextBubble | VoiceBubble = TextBubble.model_validate(bubble)
        elif bubble.get("type") == "voice":
            validated = VoiceBubble.model_validate(bubble)
        else:
            raise ValueError("Only normalized dialogue bubbles can be delivered incrementally")
        if not self.session.current(version):
            raise asyncio.CancelledError
        existing = self._partial_delivered.get(index)
        if existing is not None and self._bubble_identity(existing) == self._bubble_identity(validated):
            return
        if isinstance(validated, VoiceBubble):
            part = CompanionReply(bubbles=[validated])
            try:
                await self._deliver_stream_voice(
                    part,
                    validated,
                    version,
                    on_played=lambda: self._partial_delivered.__setitem__(index, validated),
                )
            except ProviderCapabilityError:
                # Leave non-streaming providers to terminal delivery, where the
                # regular full-asset path preserves its exact fallback.
                raise
            except _PartialAudioError:
                if validated.audio is not None:
                    await discard_reply_audio(part)
                # Do not let the LLM continue toward a terminal retry after
                # audio has already crossed the transport boundary.
                raise asyncio.CancelledError
            if validated.audio is not None:
                bubble["audio"] = validated.audio.model_dump(mode="json")
            self._partial_delivered[index] = validated
            await self.session.emit("transcript.assistant", {"text": validated.text}, version=version)
            return
        await self.session.deliver_part(index, validated.model_dump(mode="json"), version)
        self._partial_delivered[index] = validated
        await self.session.emit("transcript.assistant", {"text": validated.text}, version=version)

    async def deliver_partial(self, index: int, bubble: dict[str, Any]) -> None:
        turn = self._turn
        if turn is None:
            raise asyncio.CancelledError
        await self._deliver_partial_now(index, bubble, turn.version)

    def partial_reply(self) -> tuple[CompanionReply, str] | None:
        """Return ordered, already confirmed bubbles for interruption recovery."""
        if not self._partial_delivered:
            return None
        indices = sorted(self._partial_delivered)
        if indices != list(range(len(indices))):
            return None
        bubbles = [self._partial_delivered[index] for index in indices]
        reply = CompanionReply(bubbles=bubbles)
        return reply, companion_reply_source(reply)

    def clear_partial_reply(self) -> None:
        self._partial_delivered.clear()

    def mark_persisted(self, indices: list[int]) -> None:
        for index in indices:
            self._partial_delivered.pop(index, None)

    async def _deliver_stream_voice(
        self,
        part: CompanionReply,
        bubble: VoiceBubble,
        version: TurnVersion,
        on_played: Callable[[], None] | None = None,
    ) -> None:
        sent = False
        last_sequence: int | None = None
        audio_parts: list[bytes] = []
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
                if not chunk.audio:
                    if chunk.final:
                        continue
                    raise ValueError("Streaming TTS returned an empty non-terminal PCM chunk")
                if not chunk.mime.startswith("audio/pcm") or len(chunk.audio) % 2:
                    raise ValueError("Streaming TTS returned invalid PCM")
                audio_parts.append(chunk.audio)
                for offset in range(0, len(chunk.audio), 9600):
                    last_sequence = await self.session.send_audio(chunk.audio[offset : offset + 9600], version)
                    sent = True
            if last_sequence is None:
                raise ProviderCapabilityError("Streaming TTS returned no audio")
            # ``send_audio`` owns the call-wide sequence.  Waiting on the last
            # returned sequence prevents a later bubble from acknowledging an
            # earlier local counter.
            await self.session.wait_played(last_sequence, version)
            if on_played is not None:
                on_played()
            # Playback is the communication fact. Asset persistence is best
            # effort after that fact and must not erase a confirmed utterance.
            try:
                save_task = asyncio.create_task(
                    save_stream_reply_audio(self.actor.user_id, part, b"".join(audio_parts), sample_rate=SAMPLE_RATE)
                )
                bubble.audio = await wait_for_task(save_task)
            except asyncio.CancelledError:
                raise
            except Exception:
                bubble.audio = None
                logger.warning("Streamed voice asset persistence failed", extra={"user_id": self.actor.user_id})
        except asyncio.CancelledError:
            raise
        except ProviderCapabilityError as exc:
            # A provider may discover an unsupported mode after opening the
            # stream.  Once bytes crossed the transport boundary, replaying via
            # the full TTS path would make the user hear the same bubble twice.
            if sent:
                raise _PartialAudioError("Streaming TTS became unavailable after audio was sent") from exc
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
        last_sequence: int | None = None
        sent = False
        try:
            for offset in range(0, len(pcm), 9600):
                last_sequence = await self.session.send_audio(pcm[offset : offset + 9600], version)
                sent = True
            if last_sequence is None:
                raise RuntimeError("Voice asset is empty")
            await self.session.wait_played(last_sequence, version)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if sent:
                raise _PartialAudioError("Full voice playback was not confirmed") from exc
            raise

    async def _deliver_text_fallback(self, reply: CompanionReply, index: int, version: TurnVersion) -> None:
        if not self.session.current(version):
            raise asyncio.CancelledError
        await self.session.emit("error", {"message": "语音暂时不可用，已显示文字", "fatal": False}, version=version)
        bubble = reply.bubbles[index]
        payload = (
            {"type": "text", "text": bubble.text}
            if isinstance(bubble, VoiceBubble)
            else client_reply_bubbles(reply)[index]
        )
        await self.session.deliver_part(index, payload, version)

    async def deliver(self, reply: CompanionReply) -> None:
        await self._finish_partial_worker()
        self.confirmed_indices = []
        turn = self._turn
        if turn is None or not self.session.current(turn.version):
            raise asyncio.CancelledError
        partial_delivered = dict(self._partial_delivered)
        for index, bubble in enumerate(reply.bubbles):
            if not self.session.current(turn.version):
                raise asyncio.CancelledError
            delivered = partial_delivered.pop(index, None)
            if delivered is not None and self._bubble_identity(delivered) == self._bubble_identity(bubble):
                if isinstance(bubble, VoiceBubble) and isinstance(delivered, VoiceBubble):
                    bubble.audio = delivered.audio
                self.confirmed_indices.append(index)
                continue
            if isinstance(bubble, VoiceBubble):
                # 原始音色和演绎冻结在已验证气泡中，复用普通语音合成及资产保护。
                part = reply.model_copy(update={"bubbles": [bubble]})
                existing_audio = bubble.audio
                stream_succeeded = False
                try:
                    await self._deliver_stream_voice(
                        part,
                        bubble,
                        turn.version,
                        on_played=lambda: self._partial_delivered.__setitem__(index, bubble),
                    )
                    stream_succeeded = True
                except _PartialAudioError:
                    if existing_audio is None:
                        await discard_reply_audio(part)
                    raise
                except ProviderCapabilityError:
                    try:
                        await self._deliver_asset_voice(part, bubble, turn.version)
                    except asyncio.CancelledError:
                        if existing_audio is None:
                            await discard_reply_audio(part)
                        raise
                    except _PartialAudioError:
                        if existing_audio is None:
                            await discard_reply_audio(part)
                        raise
                    except Exception:
                        if existing_audio is None:
                            await discard_reply_audio(part)
                        reply.bubbles[index] = TextBubble(type="text", text=bubble.text)
                        await self._deliver_text_fallback(reply, index, turn.version)
                        self._partial_delivered[index] = reply.bubbles[index]
                        self.confirmed_indices.append(index)
                        await self.session.emit("transcript.assistant", {"text": bubble.text}, version=turn.version)
                    else:
                        # 完整语音播放确认后才发布台词。被打断的半句没有可靠字词时间戳，不当成已说完。
                        bubble.audio = part.bubbles[0].audio
                        self._partial_delivered[index] = bubble
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
                    reply.bubbles[index] = TextBubble(type="text", text=bubble.text)
                    await self._deliver_text_fallback(reply, index, turn.version)
                    self._partial_delivered[index] = reply.bubbles[index]
                    self.confirmed_indices.append(index)
                    await self.session.emit("transcript.assistant", {"text": bubble.text}, version=turn.version)
                if stream_succeeded:
                    # Only audio that reached the transport and passed the
                    # playback receipt is part of the confirmed history.
                    self.confirmed_indices.append(index)
                    self._partial_delivered[index] = bubble
                    await self.session.emit("transcript.assistant", {"text": bubble.text}, version=turn.version)
            else:
                await self.session.deliver_part(index, client_reply_bubbles(reply)[index], turn.version)
                self.confirmed_indices.append(index)
                self._partial_delivered[index] = bubble
                if isinstance(bubble, TextBubble):
                    await self.session.emit("transcript.assistant", {"text": bubble.text}, version=turn.version)
        self._partial_delivered.clear()

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
