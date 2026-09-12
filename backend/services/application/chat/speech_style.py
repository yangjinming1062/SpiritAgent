from modules.media import SPEECH_STYLE_ADAPTER, SpeechStyle
from pydantic import ValidationError

from services.infrastructure.llm import speech_style_matches


class SpeechStyleParser:
    """只缓冲保留的开头标记；跨增量与截断的标签都不得成为可见正文。"""

    def __init__(self, provider: str, model: str) -> None:
        self._provider = provider
        self._model = model
        self.style: SpeechStyle | None = None
        self._buffer = ""
        self._done = False
        self._discarding = False

    def feed(self, text: str) -> str:
        if self._done:
            return text
        self._buffer += text
        candidate = self._buffer.lstrip()
        opening, closing = "<speech_style>", "</speech_style>"
        if not self._discarding and opening.startswith(candidate):
            return ""
        if not self._discarding and not candidate.startswith(opening):
            self._done = True
            self._buffer = ""
            return candidate
        end = candidate.find(closing)
        if end < 0:
            if len(candidate) > 16384:
                self._discarding = True
                self._buffer = candidate[-len(closing) :]
            return ""
        if not self._discarding:
            try:
                style = SPEECH_STYLE_ADAPTER.validate_json(candidate[len(opening) : end])
                if speech_style_matches(style, self._provider, self._model):
                    self.style = style
            except ValidationError:
                pass
        self._done = True
        self._buffer = ""
        return candidate[end + len(closing) :].lstrip()
