import time
from dataclasses import dataclass
from typing import Any

DEFAULT_REPLAY_BUFFER_CAPACITY = 500
DEFAULT_REPLAY_BUFFER_TTL_SECONDS = 60.0


@dataclass(slots=True)
class BufferedFrame:
    seq: int
    timestamp: float
    frame: dict[str, Any]
    sent: bool = False


class ReplayBuffer:
    """JSON-RPC 帧的滑动窗口重放缓冲区：单调递增 seq 用于断线 < 30s 内的续接，dict 插入顺序保证迭代顺序即发送顺序，无需额外 _seq_order 列表。"""

    def __init__(
        self,
        capacity: int = DEFAULT_REPLAY_BUFFER_CAPACITY,
        ttl_seconds: float = DEFAULT_REPLAY_BUFFER_TTL_SECONDS,
    ) -> None:
        self.capacity = capacity
        self.ttl_seconds = ttl_seconds
        self._buffer: dict[int, BufferedFrame] = {}
        self._current_seq: int = 0
        self._max_sent_seq: int = 0

    @property
    def current_seq(self) -> int:
        return self._current_seq

    @property
    def min_seq(self) -> int:
        return next(iter(self._buffer.values())).seq if self._buffer else self._current_seq

    @property
    def max_seq(self) -> int:
        return self._current_seq

    def __len__(self) -> int:
        return len(self._buffer)

    def get_unsent(self) -> list[BufferedFrame]:
        """返回缓冲区中尚未发送的所有帧。"""
        return [f for f in self._buffer.values() if not f.sent]

    def mark_sent_through(self, max_seq: int) -> None:
        """将 seq <= max_seq 的所有缓冲帧标记为已发送（不删除，ack 是唯一的删除路径，让 30s 重放窗口内未确认帧可用于重连补帧；维护 max_sent_seq watermark 跳过已发帧，单调遍历首个超界即 break）。"""
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
        """为帧分配单调递增的 seq、打戳并写入缓冲区。"""
        self._current_seq += 1
        seq = self._current_seq

        # 深拷贝并注入 seq 到帧
        stamped_frame = dict(frame)
        if stamped_frame.get("method") == "event" and isinstance(stamped_frame.get("params"), dict):
            params = dict(stamped_frame["params"])
            params["seq"] = seq
            stamped_frame["params"] = params
        else:
            stamped_frame["seq"] = seq

        now = time.monotonic()
        self._buffer[seq] = BufferedFrame(seq=seq, timestamp=now, frame=stamped_frame, sent=False)
        self._prune(now)
        return seq, stamped_frame

    def ack(self, ack_seq: int) -> int:
        """裁剪 seq <= ack_seq 的所有帧，返回被裁剪的数量。"""
        if not self._buffer:
            return 0
        count = 0
        while self._buffer:
            oldest_key = next(iter(self._buffer))
            if oldest_key <= ack_seq:
                del self._buffer[oldest_key]
                count += 1
            else:
                break
        return count

    def can_replay(self, last_seq: int) -> bool:
        """检查 last_seq 之后的所有帧是否仍保留在缓冲区中。"""
        if last_seq <= 0:
            return False

        # 客户端 seq 超过服务端（服务端重启/seq 重置）→ 不同步，强制完整重载
        if last_seq > self._current_seq:
            return False

        # 客户端已与服务端对齐（双方均大于 0 且相等）→ 合法，无须重放
        if last_seq == self._current_seq:
            return True

        # 缓冲区空但 last_seq < current_seq，说明帧已被裁剪
        if not self._buffer:
            return False

        # 缓冲区中最旧的可用帧
        oldest_seq = next(iter(self._buffer.values())).seq
        # last_seq 紧邻或在缓冲区范围内即可重放
        return (last_seq + 1) >= oldest_seq

    def replay_since(self, last_seq: int) -> list[dict[str, Any]] | None:
        """返回 seq > last_seq 的所有帧；缓冲区溢出/过期时返回 None（调用方需走完整状态同步兜底）。"""
        now = time.monotonic()
        self._prune(now)

        if not self.can_replay(last_seq):
            return None

        return [f.frame for f in self._buffer.values() if f.seq > last_seq]

    def _prune(self, now: float) -> None:
        """裁剪超过 ttl 或超出 capacity 的帧。"""
        cutoff = now - self.ttl_seconds
        # TTL：从 dict 头部丢弃过期帧
        while self._buffer:
            oldest_key = next(iter(self._buffer))
            if self._buffer[oldest_key].timestamp < cutoff:
                del self._buffer[oldest_key]
            else:
                break

        # 容量：按插入顺序保留最新 capacity 个
        while len(self._buffer) > self.capacity:
            oldest_key = next(iter(self._buffer))
            del self._buffer[oldest_key]

    def clear(self) -> None:
        """重置缓冲区和 seq 计数器。"""
        self._buffer.clear()
        self._current_seq = 0
        self._max_sent_seq = 0
