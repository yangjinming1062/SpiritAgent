import time
from dataclasses import dataclass
from typing import Any

_CAPACITY = 500
_TTL_SECONDS = 60.0


@dataclass(slots=True)
class BufferedFrame:
    seq: int
    timestamp: float
    frame: dict[str, Any]
    sent: bool = False


class ReplayBuffer:
    """JSON-RPC 事件帧的滑动窗口重放缓冲：单调递增 seq 供短时断线续接，dict 插入顺序即发送顺序。"""

    def __init__(self) -> None:
        self._buffer: dict[int, BufferedFrame] = {}
        self._current_seq: int = 0
        self._max_sent_seq: int = 0

    @property
    def max_seq(self) -> int:
        return self._current_seq

    def get_unsent(self, after_seq: int = 0) -> list[BufferedFrame]:
        return [f for f in self._buffer.values() if not f.sent and f.seq > after_seq]

    def is_sent(self, seq: int) -> bool:
        entry = self._buffer.get(seq)
        return entry is not None and entry.sent

    def mark_sent_through(self, max_seq: int) -> None:
        """将 seq <= max_seq 的帧标记为已发送（不删除：ack 是唯一删除路径，重放窗口内未确认帧仍可补发）；max_sent_seq 水位让已发帧跳过遍历。"""
        target_seq = min(max_seq, self._current_seq)
        if target_seq <= self._max_sent_seq:
            return
        for f in self._buffer.values():
            if f.seq > target_seq:
                break
            if f.seq > self._max_sent_seq:
                f.sent = True
        self._max_sent_seq = target_seq

    def append(self, frame: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """为事件帧分配单调递增的 seq（写入 ``params.seq``）并入缓冲。"""
        self._current_seq += 1
        seq = self._current_seq
        stamped_frame = {**frame, "params": {**frame["params"], "seq": seq}}
        now = time.monotonic()
        self._buffer[seq] = BufferedFrame(seq=seq, timestamp=now, frame=stamped_frame)
        self._prune(now)
        return seq, stamped_frame

    def ack(self, ack_seq: int) -> int:
        """裁剪 seq <= ack_seq 的所有帧，返回被裁剪的数量。"""
        count = 0
        while self._buffer:
            oldest_key = next(iter(self._buffer))
            if oldest_key > ack_seq:
                break
            del self._buffer[oldest_key]
            count += 1
        return count

    def replay_since(self, last_seq: int) -> list[BufferedFrame] | None:
        """返回 seq > last_seq 的所有帧；last_seq 之后已有帧被裁剪或序号失同步时返回 None，调用方走完整状态同步。"""
        self._prune(time.monotonic())
        # 客户端 seq 超过服务端（网关重建 / 服务重启后 seq 重置）→ 失同步
        if last_seq <= 0 or last_seq > self._current_seq:
            return None
        if last_seq == self._current_seq:
            return []
        if not self._buffer or next(iter(self._buffer)) > last_seq + 1:
            return None
        return [f for f in self._buffer.values() if f.seq > last_seq]

    def _prune(self, now: float) -> None:
        """裁剪超过 TTL 或超出容量的帧。"""
        cutoff = now - _TTL_SECONDS
        while self._buffer:
            oldest_key = next(iter(self._buffer))
            if self._buffer[oldest_key].timestamp >= cutoff and len(self._buffer) <= _CAPACITY:
                break
            del self._buffer[oldest_key]
