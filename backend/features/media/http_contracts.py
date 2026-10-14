"""媒体请求与产物快照；文件和音频响应由 HTTP 入口构造。"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict


class TtsRequest(BaseModel):
    text: str = ""
    voice: str = ""
    language: str = ""


class MediaFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: Path
    media_type: str


class SpeechAudio(BaseModel):
    model_config = ConfigDict(frozen=True)

    audio: bytes
    mime: str
    voice: str


class ProviderOperationError(Exception):
    def __init__(self, error: Exception, operation: str) -> None:
        super().__init__(operation)
        self.error = error
        self.operation = operation
