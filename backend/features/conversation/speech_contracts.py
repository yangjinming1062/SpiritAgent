from pydantic import BaseModel


class ChatVideoUploadResponse(BaseModel):
    url: str


class ChatAudioUploadResponse(BaseModel):
    url: str
    mime_type: str
    duration: float
