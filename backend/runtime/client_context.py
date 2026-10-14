from pydantic import BaseModel, Field


class ChatRequestClientContext(BaseModel):
    # 原文进系统提示词与 JWT，须设上限防止无界增长
    environment_hints: str | None = Field(default=None, max_length=2048)
    platform_hints: str | None = Field(default=None, max_length=2048)
