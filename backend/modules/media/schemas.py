from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class SpeechSegment(BaseModel):
    """句内标记的落点：text 串按序拼回本泡台词，tag/pause 作用于所属段起始处。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=0, max_length=4000)
    # tag 是单个音频/语气变化标记；含逗号等分隔符即为多条指令混入，应拆开或写进 direction。
    tag: str | None = Field(default=None, max_length=80, pattern=r"^[^\[\]()<>，,、;；\r\n]+$")
    pause: float | None = Field(default=None, ge=0.01, le=99.99, multiple_of=0.01)


class SpeechDirection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1, max_length=500)
    scene: str = Field(min_length=1, max_length=500)
    guidance: str = Field(min_length=1, max_length=1500)


class MiMoSpeechStyle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["mimo"]
    model: str = Field(min_length=1, max_length=100)
    styles: list[Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[^\[\]()<>\r\n]+$")]] = Field(
        default_factory=list,
        max_length=4,
    )
    direction: SpeechDirection
    segments: list[SpeechSegment] = Field(default_factory=list, max_length=12)


class MiniMaxSpeechStyle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["minimax"]
    model: str = Field(min_length=1, max_length=100)
    emotion: (
        Literal["happy", "sad", "angry", "fearful", "disgusted", "surprised", "calm", "fluent", "whisper"] | None
    ) = None
    speed: float = Field(default=1, ge=0.5, le=2)
    segments: list[SpeechSegment] = Field(default_factory=list, max_length=12)


SpeechStyle = Annotated[MiMoSpeechStyle | MiniMaxSpeechStyle, Field(discriminator="provider")]
SPEECH_STYLE_ADAPTER = TypeAdapter(SpeechStyle)


class ChatVideoUploadResponse(BaseModel):
    url: str


class SpeechToTextResponse(BaseModel):
    text: str
