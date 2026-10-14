from pydantic import BaseModel


class SpeechToTextResponse(BaseModel):
    text: str
