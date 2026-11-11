"""独立通话通道：JSON 控制与 PCM16 二进制帧。"""

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SAMPLE_RATE = 24_000
MAX_AUDIO_FRAME_BYTES = 9_600
MAX_INPUT_BYTES = SAMPLE_RATE * 2 * 60


@dataclass(frozen=True, slots=True)
class TurnVersion:
    turn_seq: int
    playback_epoch: int


class VoiceControl(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["input.start", "input.end", "mute", "stop", "ping", "audio.played", "part.received"]
    turn_seq: int = Field(default=0, ge=0, le=2**31 - 1)
    playback_epoch: int = Field(default=0, ge=0, le=2**31 - 1)
    muted: bool = False
    sequence: int = Field(default=0, ge=0, le=2**31 - 1)
    part_id: int = Field(default=0, ge=0, le=1024)

    @property
    def version(self) -> TurnVersion:
        return TurnVersion(self.turn_seq, self.playback_epoch)


CallState = Literal["connecting", "listening", "thinking", "speaking", "ended"]
EventPayload = dict[str, Any]
