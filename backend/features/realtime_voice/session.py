"""连接身份、播放代次与有界发送/播放确认；不调用模型。"""

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import asdict

from infrastructure.async_io import cancel_and_wait

from .contracts import MAX_AUDIO_FRAME_BYTES, SAMPLE_RATE, CallState, EventPayload, TurnVersion


class CallAlreadyActiveError(RuntimeError):
    pass


class CallSession:
    def __init__(
        self, user_id: int, session_id: str, send: Callable[[EventPayload, bytes | None], Awaitable[None]]
    ) -> None:
        self.user_id = user_id
        self.session_id = session_id
        self.call_id = secrets.token_urlsafe(18)
        self.version = TurnVersion(0, 0)
        self.state: CallState = "connecting"
        self.muted = False
        self.closed = asyncio.Event()
        self._send = send
        self._send_lock = asyncio.Lock()
        self._played = asyncio.Condition()
        self._sent_sequence = 0
        self._played_sequence = 0
        self._playback_confirmation: tuple[int, Callable[[], None]] | None = None
        self._part_waiter: tuple[int, asyncio.Future[None], Callable[[], None]] | None = None

    def current(self, version: TurnVersion) -> bool:
        return not self.closed.is_set() and version == self.version

    async def emit(
        self,
        kind: str,
        payload: EventPayload | None = None,
        *,
        version: TurnVersion | None = None,
        audio: bytes | None = None,
    ) -> bool:
        version = version or self.version
        async with self._send_lock:
            if not self.current(version):
                return False
            event = {
                "type": kind,
                "call_id": self.call_id,
                "session_id": self.session_id,
                "state": self.state,
                "muted": self.muted,
                **asdict(version),
                "payload": payload or {},
            }
            # Transport sends are finite operations, independent from provider generation waits.
            async with asyncio.timeout(10):
                await self._send(event, audio)
        return True

    async def set_state(self, state: CallState, version: TurnVersion | None = None) -> None:
        if version is not None and not self.current(version):
            return
        self.state = state
        await self.emit("state", version=version)

    async def begin_input(self, version: TurnVersion) -> bool:
        if self.closed.is_set() or self.muted:
            return False
        if version == self.version:
            return False
        if version != TurnVersion(self.version.turn_seq + 1, self.version.playback_epoch + 1):
            raise ValueError("Invalid call generation")
        self.version = version
        self._sent_sequence = self._played_sequence = 0
        self._playback_confirmation = None
        if self._part_waiter is not None and not self._part_waiter[1].done():
            self._part_waiter[1].cancel()
        async with self._played:
            self._played.notify_all()
        await self.set_state("listening", version)
        return True

    async def send_audio(self, pcm: bytes, version: TurnVersion) -> int:
        if not pcm or len(pcm) % 2 or len(pcm) > MAX_AUDIO_FRAME_BYTES:
            raise ValueError("Invalid PCM frame")
        if not self.current(version):
            raise asyncio.CancelledError
        self._sent_sequence += 1
        sequence = self._sent_sequence
        await self.emit(
            "audio.chunk",
            {
                "sequence": sequence,
                "sample_rate": SAMPLE_RATE,
                "channels": 1,
                "codec": "pcm_s16le",
                "byte_length": len(pcm),
            },
            version=version,
            audio=pcm,
        )
        # At most one second of audio can wait in transport/client queues.
        if sequence > 5:
            await self.wait_played(sequence - 5, version)
        return sequence

    async def acknowledge_audio(self, sequence: int, version: TurnVersion) -> None:
        if not self.current(version):
            return
        if sequence > self._sent_sequence:
            raise ValueError("Playback acknowledgement exceeds sent audio")
        async with self._played:
            self._played_sequence = max(self._played_sequence, sequence)
            pending = self._playback_confirmation
            if pending is not None and self._played_sequence >= pending[0]:
                self._playback_confirmation = None
                pending[1]()
            self._played.notify_all()

    async def wait_played(
        self, sequence: int, version: TurnVersion, on_played: Callable[[], None] | None = None
    ) -> None:
        async with asyncio.timeout(15), self._played:
            if not self.current(version):
                raise asyncio.CancelledError
            if self._played_sequence >= sequence:
                if on_played is not None:
                    on_played()
                return
            # 在消费回执时登记交付，避免紧随其后的输入或停止帧取消等待者后丢掉已交付事实。
            confirmation = (sequence, on_played) if on_played is not None else None
            if confirmation is not None:
                self._playback_confirmation = confirmation
            try:
                await self._played.wait_for(lambda: not self.current(version) or self._played_sequence >= sequence)
                if not self.current(version):
                    raise asyncio.CancelledError
            finally:
                if confirmation is not None and self._playback_confirmation is confirmation:
                    self._playback_confirmation = None

    async def deliver_part(
        self, part_id: int, bubble: EventPayload, version: TurnVersion, on_received: Callable[[], None]
    ) -> None:
        future = asyncio.get_running_loop().create_future()
        self._part_waiter = pending = (part_id, future, on_received)
        try:
            if not await self.emit("reply.part", {"part_id": part_id, "bubble": bubble}, version=version):
                raise asyncio.CancelledError
            async with asyncio.timeout(15):
                await future
        finally:
            if self._part_waiter is pending:
                self._part_waiter = None

    def acknowledge_part(self, part_id: int, version: TurnVersion) -> None:
        pending = self._part_waiter
        if self.current(version) and pending is not None and pending[0] == part_id and not pending[1].done():
            pending[2]()
            pending[1].set_result(None)

    async def close(self) -> None:
        self.closed.set()
        self.state = "ended"
        self._playback_confirmation = None
        if self._part_waiter is not None and not self._part_waiter[1].done():
            self._part_waiter[1].cancel()
        async with self._played:
            self._played.notify_all()


class CallManager:
    """同账户单通话；生命周期取消连接所有者并等待其收尾。"""

    def __init__(self) -> None:
        self._owners: dict[int, tuple[CallSession, asyncio.Task]] = {}

    def add(self, session: CallSession) -> None:
        if session.user_id in self._owners:
            raise CallAlreadyActiveError("该账户已有通话")
        owner = asyncio.current_task()
        if owner is None:
            raise RuntimeError("Call owner is missing")
        self._owners[session.user_id] = (session, owner)

    def remove(self, session: CallSession) -> None:
        current = self._owners.get(session.user_id)
        if current is not None and current[0] is session:
            self._owners.pop(session.user_id)

    async def end_all(self, user_id: int | None = None) -> None:
        await cancel_and_wait(
            owner for uid, (_, owner) in tuple(self._owners.items()) if user_id is None or uid == user_id
        )


CALL_MANAGER = CallManager()
