"""通话输入、已有聊天受理与播放回执的协调；无独立人格或历史。"""

import asyncio
import io
import wave
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from features.conversation.http_contracts import PromptSubmitRequest
from features.conversation.replies import CompanionReply, VoiceBubble
from features.conversation.reply_audio import client_reply_bubbles, discard_reply_audio, prepare_reply_audio
from features.realtime_voice.contracts import MAX_INPUT_BYTES, SAMPLE_RATE, TurnVersion
from features.realtime_voice.session import CallSession
from infrastructure.ai.stt_service import transcribe_audio
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


class RealtimeVoicePipeline:
    def __init__(self, session: CallSession, actor: BusinessActor) -> None:
        self.session = session
        self.actor = actor
        self.confirmed_indices: list[int] = []
        self._base = UserEventPublisher(actor.user_id)
        self._operations = chat_operations(actor, event_publisher=self, reply_delivery=self)
        self._input: bytearray | None = None
        self._finish_task: asyncio.Task[None] | None = None
        self._turn: CallTurn | None = None

    async def begin_input(self, version: TurnVersion) -> None:
        if not await self.session.begin_input(version):
            return
        await self.cancel_current()
        self._input = bytearray()

    def discard_input(self) -> None:
        self._input = None

    def receive_audio(self, pcm: bytes) -> None:
        if self._input is None or self.session.muted:
            return
        if len(self._input) + len(pcm) > MAX_INPUT_BYTES:
            self._input = None
            raise ValueError("单次发言超过 60 秒，请缩短后重试")
        self._input.extend(pcm)

    def finish_input(self, version: TurnVersion) -> None:
        if not self.session.current(version) or self._input is None:
            return
        pcm, self._input = bytes(self._input), None
        if len(pcm) < SAMPLE_RATE // 5:  # 少于 100ms 的声音不能形成有效发言。
            return
        self._finish_task = asyncio.create_task(self._recognize(pcm, version), name="call.transcribe")
        self.actor.account.track(self._finish_task)

    async def _recognize(self, pcm: bytes, version: TurnVersion) -> None:
        try:
            await self.session.set_state("thinking", version)
            text = (await transcribe_audio(self.actor.user_id, pcm_wav(pcm), "audio/wav")).strip()
            if not self.session.current(version) or self.session.muted:
                return
            if not text:
                await self.session.set_state("listening", version)
                return
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

    async def cancel_current(self) -> None:
        self._input = None
        await cancel_and_wait([self._finish_task])
        self._finish_task = None
        turn, self._turn = self._turn, None
        if turn is None:
            return
        await self._operations.cancel({"session_id": self.session.session_id, "request_id": turn.request_id})
        runtime = self.actor.account.runtime_sessions.get(self.session.session_id)
        # cancel API only requests cancellation; the next turn must await tool/history settlement.
        if runtime is not None and runtime.chat_task is not None:
            await wait_for_task(asyncio.gather(runtime.chat_task, return_exceptions=True))
        if runtime is not None and runtime.settlement_task is not None:
            await wait_for_task(asyncio.gather(runtime.settlement_task, return_exceptions=True))

    async def deliver(self, reply: CompanionReply) -> None:
        self.confirmed_indices = []
        turn = self._turn
        if turn is None or not self.session.current(turn.version):
            raise asyncio.CancelledError
        for index, bubble in enumerate(reply.bubbles):
            if not self.session.current(turn.version):
                raise asyncio.CancelledError
            if isinstance(bubble, VoiceBubble):
                # 原始音色和演绎冻结在已验证气泡中，复用普通语音合成及资产保护。
                part = reply.model_copy(update={"bubbles": [bubble]})
                await prepare_reply_audio(self.actor.user_id, part)
                if bubble.audio is None:
                    raise RuntimeError("Call voice synthesis failed")
                try:
                    pcm = await run_blocking(asset_pcm, self.actor.user_id, bubble.audio.url)
                    await self.session.set_state("speaking", turn.version)
                    sequence = 0
                    for offset in range(0, len(pcm), 9600):
                        sequence = await self.session.send_audio(pcm[offset : offset + 9600], turn.version)
                    await self.session.wait_played(sequence, turn.version)
                    # 完整语音播放确认后才发布台词。被打断的半句没有可靠字词时间戳，不当成已说完。
                    bubble.audio = part.bubbles[0].audio
                    self.confirmed_indices.append(index)
                    await self.session.emit("transcript.assistant", {"text": bubble.text}, version=turn.version)
                except BaseException:
                    await discard_reply_audio(part)
                    raise
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
            await self.cancel_current()
        finally:
            await self.session.close()
