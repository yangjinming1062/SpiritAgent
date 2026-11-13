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
from features.conversation.replies import CompanionReply, LinkBubble, MediaBubble, TextBubble, VoiceBubble
from features.conversation.reply_audio import (
    client_reply_bubbles,
)
from features.media.speech import stream_synthesize_speech, synthesize_speech
from features.realtime_voice.contracts import MAX_INPUT_BYTES, SAMPLE_RATE, TurnVersion
from features.realtime_voice.session import CallSession
from infrastructure.ai.providers.base import ProviderCapabilityError
from infrastructure.ai.stt_service import stream_transcribe_audio, transcribe_audio
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.async_io import cancel_and_wait, run_blocking, wait_for_task
from infrastructure.logger import get_logger
from infrastructure.video_processing.ffmpeg import ffmpeg_stdout
from runtime.business_actor import BusinessActor

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


@dataclass(frozen=True)
class CallTurn:
    request_id: str
    version: TurnVersion


class _PartialAudioError(RuntimeError):
    """流式 TTS 已经发出部分声音，不能再回退到另一份音频。"""


class CallInputOverflowError(ValueError):
    """实时识别输入队列溢出；当前发言必须作废并重新开始。"""


class RealtimeVoicePipeline:
    def __init__(self, session: CallSession, actor: BusinessActor, record_id: int) -> None:
        self.session = session
        self.actor = actor
        self.confirmed_indices: list[int] = []
        self._confirmed_turn_id: str | None = None
        self._operations = chat_operations(
            actor, event_publisher=self, reply_delivery=self, call_record_id=record_id, call_owner=session
        )
        self._input: bytearray | None = None
        self._input_queue: asyncio.Queue[bytes | None] | None = None
        self._input_finished = False
        self._finish_task: asyncio.Task[None] | None = None
        self._cancel_task: asyncio.Task[None] | None = None
        self._turn: CallTurn | None = None
        # 保存已确认气泡的原始位置及音频，供终态持久化和中断恢复共用。
        self._partial_delivered: dict[int, TextBubble | VoiceBubble | MediaBubble | LinkBubble] = {}
        # 已经开始播放、尚未确认的气泡；中断时按整句与已确认气泡一起进入通话记录。
        self._partial_started: dict[int, TextBubble | VoiceBubble | MediaBubble | LinkBubble] = {}
        self._terminal_reply: CompanionReply | None = None
        self._partial_queue: asyncio.Queue[tuple[int, dict[str, Any], asyncio.Future[None]]] | None = None
        self._partial_worker: asyncio.Task[None] | None = None
        self._partial_worker_error: Exception | None = None
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
                try:
                    if previous is not None:
                        with contextlib.suppress(Exception):
                            await wait_for_task(previous)
                finally:
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
        self._input_error = None
        if self._turn is not None:
            return
        finish_task = self._finish_task
        self._finish_task = None
        version = self.session.version
        await cancel_and_wait([finish_task])
        if self.session.current(version) and self.session.state == "thinking":
            await self.session.set_state("listening", version)

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
        await asyncio.shield(task)
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
            turn = CallTurn(str(uuid4()), version)
            self._turn = turn
            self._start_partial_worker(turn)
            # 受理任务沿用账户所有权；取消调用方仍先等待账本确认，再定向取消原请求。
            submitted = asyncio.create_task(
                self._operations.submit(
                    self.session.session_id,
                    PromptSubmitRequest(request_id=turn.request_id, text=text, response_preference="voice"),
                )
            )
            await wait_for_task(submitted)
            await self.session.emit("transcript.final", {"text": text}, version=version)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.stop_partial_delivery()
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
        try:
            await cancel_and_wait([finish_task])
            if turn is not None:
                await self._operations.cancel_and_settle(self.session.session_id, turn.request_id)
        finally:
            await self.stop_partial_delivery()

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

    def _start_partial_worker(self, turn: CallTurn) -> None:
        # The confirmation ledger belongs to the submitted turn. Reset it
        # before the new worker can fail, so persistence cannot observe indices
        # left by the previous turn.
        self.confirmed_indices = []
        self._confirmed_turn_id = turn.request_id
        self._partial_delivered.clear()
        self._partial_started.clear()
        self._terminal_reply = None
        queue: asyncio.Queue[tuple[int, dict[str, Any], asyncio.Future[None]]] = asyncio.Queue(maxsize=4)
        self._partial_queue = queue
        self._partial_worker_error = None
        self._partial_worker = asyncio.create_task(
            self._run_partial_worker(queue, turn.version), name="call.reply-delivery"
        )
        self._partial_worker.add_done_callback(lambda _: self._settle_partial_queue(queue))
        self.actor.account.track(self._partial_worker)

    def _settle_partial_queue(self, queue: asyncio.Queue[tuple[int, dict[str, Any], asyncio.Future[None]]]) -> None:
        queue.shutdown()
        while True:
            try:
                _, _, receipt = queue.get_nowait()
            except asyncio.QueueShutDown:
                return
            if not receipt.done():
                if self._partial_worker_error is not None:
                    receipt.set_exception(self._partial_worker_error)
                else:
                    receipt.cancel()

    async def _run_partial_worker(
        self, queue: asyncio.Queue[tuple[int, dict[str, Any], asyncio.Future[None]]], version: TurnVersion
    ) -> None:
        while True:
            try:
                index, bubble, receipt = await queue.get()
            except asyncio.QueueShutDown:
                return
            try:
                await self._deliver_partial_now(index, bubble, version)
            except asyncio.CancelledError:
                queue.shutdown()
                if not receipt.done():
                    receipt.cancel()
                raise
            except Exception as exc:
                self._partial_worker_error = exc
                queue.shutdown()
                if not receipt.done():
                    receipt.set_exception(exc)
                return
            else:
                if not receipt.done():
                    receipt.set_result(None)

    async def enqueue_partial(self, index: int, bubble: dict[str, Any]) -> asyncio.Future[None]:
        turn = self._turn
        queue = self._partial_queue
        if turn is None or queue is None or not self.session.current(turn.version):
            raise asyncio.CancelledError
        receipt = asyncio.get_running_loop().create_future()
        # 回合可能在模型流中断时放弃等待回执，异常仍由交付收尾报告。
        receipt.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        try:
            await queue.put((index, bubble, receipt))
        except asyncio.QueueShutDown:
            raise self._partial_worker_error or asyncio.CancelledError from None
        return receipt

    async def _finish_partial_worker(self) -> None:
        queue, worker = self._partial_queue, self._partial_worker
        if queue is None or worker is None:
            return
        queue.shutdown()
        await worker
        self._partial_queue = None
        self._partial_worker = None
        if self._partial_worker_error is not None and not isinstance(
            self._partial_worker_error, ProviderCapabilityError
        ):
            raise self._partial_worker_error

    async def stop_partial_delivery(self) -> None:
        worker = self._partial_worker
        if self._partial_queue is not None:
            self._partial_queue.shutdown()
        if worker is not None:
            await cancel_and_wait([worker])
        if self._partial_worker is worker:
            self._partial_worker = None
            self._partial_queue = None

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

        def confirm() -> None:
            self._partial_delivered[index] = validated
            self._partial_started.pop(index, None)

        def started() -> None:
            self._partial_started[index] = validated

        if isinstance(validated, VoiceBubble):
            await self._deliver_voice(index, validated, version, confirm, started)
        else:
            await self.session.deliver_part(index, validated.model_dump(mode="json"), version, confirm)
        await self.session.emit("transcript.assistant", {"text": validated.text}, version=version)

    def partial_reply(self, request_id: str) -> tuple[CompanionReply, str] | None:
        """中断恢复时返回已确认或已经开始播放的气泡，保持原始顺序。"""
        bubbles_by_index = {**self._partial_started, **self._partial_delivered}
        if request_id != self._confirmed_turn_id or not bubbles_by_index:
            return None
        # Earlier confirmed bubbles may already have been persisted and
        # removed. Remaining indices retain their original identity and order.
        bubbles = [bubbles_by_index[index] for index in sorted(bubbles_by_index)]
        if self._terminal_reply is None:
            reply = CompanionReply(bubbles=bubbles)
        else:
            reply = self._terminal_reply.model_copy(update={"bubbles": bubbles})
        return reply, companion_reply_source(reply)

    def clear_partial_reply(self, request_id: str) -> None:
        if request_id == self._confirmed_turn_id:
            self.mark_persisted([*self._partial_delivered, *self._partial_started])

    def mark_persisted(self, indices: list[int]) -> None:
        persisted = set(indices)
        self.confirmed_indices = [index for index in self.confirmed_indices if index not in persisted]
        for index in indices:
            self._partial_delivered.pop(index, None)
            self._partial_started.pop(index, None)

    def delivered_indices_for(self, request_id: str | None) -> list[int]:
        """返回本次请求已确认或已经开始播放的气泡位置。"""
        if request_id != self._confirmed_turn_id:
            return []
        return sorted({*self._partial_delivered, *self.confirmed_indices, *self._partial_started})

    async def _deliver_stream_voice(
        self,
        bubble: VoiceBubble,
        version: TurnVersion,
        on_played: Callable[[], None],
        on_started: Callable[[], None],
    ) -> None:
        sent = False
        last_sequence: int | None = None
        try:
            await self.session.set_state("speaking", version)
            chunks = stream_synthesize_speech(
                self.actor.user_id,
                bubble.text,
                voice=bubble.voice_id,
                language=bubble.language,
                speech_style=bubble.speech,
                task=AiTaskKey.companion_voice,
                preserve_performance=True,
            )
            async with contextlib.aclosing(chunks):
                async for chunk in chunks:
                    if not chunk.audio:
                        if chunk.final:
                            continue
                        raise ValueError("Streaming TTS returned an empty non-terminal PCM chunk")
                    if not chunk.mime.startswith("audio/pcm") or len(chunk.audio) % 2:
                        raise ValueError("Streaming TTS returned invalid PCM")
                    for offset in range(0, len(chunk.audio), 9600):
                        last_sequence = await self.session.send_audio(chunk.audio[offset : offset + 9600], version)
                        if not sent:
                            on_started()
                        sent = True
            if last_sequence is None:
                raise ProviderCapabilityError("Streaming TTS returned no audio")
            # ``send_audio`` owns the call-wide sequence.  Waiting on the last
            # returned sequence prevents a later bubble from acknowledging an
            # earlier local counter.
            await self.session.wait_played(last_sequence, version, on_played)
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

    async def _deliver_full_voice(
        self,
        bubble: VoiceBubble,
        version: TurnVersion,
        on_played: Callable[[], None],
        on_started: Callable[[], None],
    ) -> None:
        audio = await synthesize_speech(
            self.actor.user_id,
            bubble.text,
            bubble.voice_id,
            bubble.language,
            bubble.speech,
            task=AiTaskKey.companion_voice,
            preserve_performance=True,
        )
        pcm = await run_blocking(
            ffmpeg_stdout,
            [
                "-i",
                "pipe:0",
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
            input_bytes=audio.audio,
            timeout=30,
        )
        if not pcm or len(pcm) >= SAMPLE_RATE * 2 * 300:
            raise ValueError("Voice output is empty or exceeds the playback budget")
        await self.session.set_state("speaking", version)
        last_sequence: int | None = None
        sent = False
        try:
            for offset in range(0, len(pcm), 9600):
                last_sequence = await self.session.send_audio(pcm[offset : offset + 9600], version)
                if not sent:
                    on_started()
                sent = True
            if last_sequence is None:
                raise RuntimeError("Voice output is empty")
            await self.session.wait_played(last_sequence, version, on_played)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if sent:
                raise _PartialAudioError("Full voice playback was not confirmed") from exc
            raise

    async def _deliver_voice(
        self,
        index: int,
        bubble: VoiceBubble,
        version: TurnVersion,
        confirm: Callable[[], None],
        on_started: Callable[[], None],
    ) -> None:
        try:
            try:
                await self._deliver_stream_voice(bubble, version, on_played=confirm, on_started=on_started)
            except ProviderCapabilityError:
                await self._deliver_full_voice(bubble, version, confirm, on_started)
        except Exception:
            if not self.session.current(version):
                raise asyncio.CancelledError
            logger.info("Call voice delivery fell back to text", extra={"user_id": self.actor.user_id}, exc_info=True)
            await self.session.emit("error", {"message": "语音暂时不可用，请查看文字", "fatal": False}, version=version)
            await self.session.deliver_part(index, {"type": "text", "text": bubble.text}, version, confirm)

    async def deliver(self, reply: CompanionReply) -> None:
        turn = self._turn
        if turn is None or not self.session.current(turn.version):
            raise asyncio.CancelledError
        await self._finish_partial_worker()
        self._terminal_reply = reply
        for index, bubble in enumerate(reply.bubbles):
            if not self.session.current(turn.version):
                raise asyncio.CancelledError
            delivered = self._partial_delivered.get(index)
            if delivered is not None:
                if delivered.model_dump(exclude={"audio"}) != bubble.model_dump(exclude={"audio"}):
                    raise RuntimeError("Final reply differs from an already delivered bubble")
                self.confirmed_indices.append(index)
                continue

            def confirm(
                index: int = index, bubble: TextBubble | VoiceBubble | MediaBubble | LinkBubble = bubble
            ) -> None:
                self._partial_delivered[index] = bubble
                self._partial_started.pop(index, None)
                self.confirmed_indices.append(index)

            def started(
                index: int = index, bubble: TextBubble | VoiceBubble | MediaBubble | LinkBubble = bubble
            ) -> None:
                self._partial_started[index] = bubble

            if isinstance(bubble, VoiceBubble):
                await self._deliver_voice(index, bubble, turn.version, confirm, started)
            else:
                await self.session.deliver_part(index, client_reply_bubbles(reply)[index], turn.version, confirm)
            if isinstance(bubble, (TextBubble, VoiceBubble)):
                await self.session.emit("transcript.assistant", {"text": bubble.text}, version=turn.version)

    async def push_event(self, event_type: str, payload: Any = None, session_id: str | None = None) -> None:
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

    async def close(self) -> None:
        await self.session.close()
        try:
            await self._wait_previous_cancel()
        finally:
            await self.cancel_current()
