from pydantic import BaseModel, Field


class VoiceEntry(BaseModel):
    id: str
    provider: str
    label: str
    gender: str
    language: str = ""
    tags: list[str] = Field(default_factory=list)
    description: str = ""
