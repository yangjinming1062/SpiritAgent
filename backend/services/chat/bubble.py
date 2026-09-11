"""连续助手气泡的流式切分器：将 markdown 分隔线 ``---`` 转换为 BubbleEvent 让发射器插入 bubble.break 帧。"""

from dataclasses import dataclass

# 优先匹配最长分隔符，避免 ``\n\n---\n\n`` 被嵌套的 ``\n---\n`` 抢先命中。
_SEPARATORS: tuple[str, ...] = ("\n\n---\n\n", "\n---\n")
# 各分隔符的全部真前缀（降序），用于在跨 chunk 切分时暂留尾部，防止分隔符被当成文本输出。
_PARTIAL_PREFIXES: tuple[str, ...] = tuple(
    sorted({sep[:i] for sep in _SEPARATORS for i in range(1, len(sep))}, key=len, reverse=True),
)
# 流结束时需要丢弃的含连字符的前缀（不完整分隔符）。
_INCOMPLETE_DASH_PREFIXES: tuple[str, ...] = tuple(
    sorted({p for p in _PARTIAL_PREFIXES if "-" in p}, key=len, reverse=True),
)


@dataclass(frozen=True)
class BubbleEvent:
    """助手气泡流的最小单元：文本片段或气泡边界。"""

    is_break: bool
    text: str = ""


class BubbleSplitter:
    """缓冲文本流并在 ``---`` 行处切分为多个气泡。"""

    def __init__(self, *, split_paragraphs: bool = False) -> None:
        self._buf = ""
        self._separators = (*_SEPARATORS, "\n\n", "\r\n\r\n") if split_paragraphs else _SEPARATORS
        self._prefixes = tuple(
            sorted({sep[:i] for sep in self._separators for i in range(1, len(sep))}, key=len, reverse=True),
        )

    def feed(self, text: str) -> list[BubbleEvent]:
        if not text:
            return []
        self._buf += text
        return self._drain()

    def flush(self) -> list[BubbleEvent]:
        """流结束时丢弃尾部残留的分隔符/不完整连字符前缀再输出残余文本，避免尾部 ``---`` 暴露为 break 或字面文本。"""
        while True:
            stripped = False
            for sep in self._separators:
                if self._buf.endswith(sep):
                    self._buf = self._buf[: -len(sep)]
                    stripped = True
            if not stripped:
                break
        for prefix in _INCOMPLETE_DASH_PREFIXES:
            if self._buf.endswith(prefix):
                self._buf = self._buf[: -len(prefix)]
                break
        out = [BubbleEvent(is_break=False, text=self._buf)] if self._buf else []
        self._buf = ""
        return out

    def _drain(self) -> list[BubbleEvent]:
        events: list[BubbleEvent] = []
        while True:
            idx = -1
            sep_len = 0
            for sep in self._separators:
                i = self._buf.find(sep)
                if i != -1 and (idx == -1 or i < idx):
                    idx = i
                    sep_len = len(sep)
            if idx == -1:
                break
            # 空行也可能是较长 --- 分隔符的开头，等后续字节消歧。
            if self._buf[idx:] in self._prefixes:
                break
            before = self._buf[:idx]
            if before:
                events.append(BubbleEvent(is_break=False, text=before))
            events.append(BubbleEvent(is_break=True))
            self._buf = self._buf[idx + sep_len :]

        # 仅输出已确定不是分隔符前缀的部分，暂留可能跨 chunk 演变为分隔符的后缀。
        for prefix in self._prefixes:
            if self._buf.endswith(prefix):
                emit = self._buf[: -len(prefix)]
                if emit:
                    events.append(BubbleEvent(is_break=False, text=emit))
                self._buf = self._buf[-len(prefix) :]
                return events
        if self._buf:
            events.append(BubbleEvent(is_break=False, text=self._buf))
            self._buf = ""
        return events
