"""简单操作的公共响应类型。"""

from pydantic import BaseModel


class MessageResponse(BaseModel):
    message: str
