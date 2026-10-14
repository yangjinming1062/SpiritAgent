"""流式助手气泡切分。结构化回复用 JSON 表达气泡边界；文本流只按 Markdown ``---`` 分隔线拆泡（代码围栏内的 ``---`` 是代码内容，不拆），空行和段落保留为正文。"""

import re
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
_LEADING_SEPARATORS: tuple[str, ...] = ("---\r\n\r\n", "---\n\n", "---\r\n", "---\n")
_FENCE_LINE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


@dataclass(frozen=True)
class BubbleEvent:
    """助手气泡流的最小单元：文本片段或气泡边界。"""

    is_break: bool
    text: str = ""


@dataclass
class _FenceState:
    """Markdown 代码围栏进度：围栏内的 ``---`` 是代码内容，拆开会切断代码块。"""

    marker: str = ""  # 当前围栏的起始标记（如 "```"）；空串表示不在围栏内
    line: str = ""  # 已收到但尚未结束的当前行

    def copy(self) -> "_FenceState":
        return _FenceState(self.marker, self.line)

    def consume(self, text: str) -> None:
        *lines, self.line = (self.line + text).split("\n")
        for line in lines:
            self._apply(line.removesuffix("\r"))

    def _apply(self, line: str) -> None:
        if (match := _FENCE_LINE_RE.match(line)) is None:
            return
        run, rest = match.groups()
        if not self.marker:
            # 反引号围栏的信息串不能含反引号，否则是行内代码。
            if run[0] != "`" or "`" not in rest:
                self.marker = run
        elif run[0] == self.marker[0] and len(run) >= len(self.marker) and not rest.strip():
            self.marker = ""


class BubbleSplitter:
    """缓冲文本流并在代码围栏之外的 ``---`` 行处切分为多个气泡。"""

    def __init__(self) -> None:
        self._buf = ""
        self._at_start = True
        # 反映缓冲区之前已输出文本的围栏状态。
        self._fence = _FenceState()

    def feed(self, text: str) -> list[BubbleEvent]:
        if not text:
            return []
        self._buf += text
        return self._drain()

    def flush(self) -> list[BubbleEvent]:
        """流结束时丢弃尾部残留的分隔符/不完整连字符前缀再输出残余文本，避免尾部 ``---`` 暴露为 break 或字面文本。"""
        if self._at_start and self._buf.strip() == "---":
            self._buf = ""
        while True:
            stripped = False
            for sep in _SEPARATORS:
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
        if self._at_start:
            for separator in _LEADING_SEPARATORS:
                if self._buf.startswith(separator):
                    self._buf = self._buf[len(separator) :]
                    self._at_start = False
                    break
            else:
                if any(separator.startswith(self._buf) for separator in _LEADING_SEPARATORS):
                    return events
                self._at_start = False
        while (found := self._next_separator()) is not None:
            idx, sep_len = found
            # 空行也可能是较长 --- 分隔符的开头，等后续字节消歧。
            if self._buf[idx:] in _PARTIAL_PREFIXES:
                break
            before = self._buf[:idx]
            if before:
                events.append(BubbleEvent(is_break=False, text=before))
            events.append(BubbleEvent(is_break=True))
            self._fence.consume(before + "\n")
            self._buf = self._buf[idx + sep_len :]

        # 仅输出已确定不是分隔符前缀的部分，暂留可能跨 chunk 演变为分隔符的后缀。
        for prefix in _PARTIAL_PREFIXES:
            if self._buf.endswith(prefix):
                emit = self._buf[: -len(prefix)]
                if emit:
                    events.append(BubbleEvent(is_break=False, text=emit))
                    self._fence.consume(emit)
                self._buf = self._buf[-len(prefix) :]
                return events
        if self._buf:
            events.append(BubbleEvent(is_break=False, text=self._buf))
            self._fence.consume(self._buf)
            self._buf = ""
        return events

    def _next_separator(self) -> tuple[int, int] | None:
        """缓冲区内第一个位于代码围栏之外的分隔符的位置与长度。"""
        fence = self._fence.copy()
        scanned = start = 0
        while True:
            idx = sep_len = -1
            for sep in _SEPARATORS:
                i = self._buf.find(sep, start)
                if i != -1 and (idx == -1 or i < idx):
                    idx, sep_len = i, len(sep)
            if idx == -1:
                return None
            # 分隔符的首个换行结束上一行，围栏状态要算到该行结束之后。
            fence.consume(self._buf[scanned : idx + 1])
            scanned = idx + 1
            if not fence.marker:
                return idx, sep_len
            start = idx + 1
