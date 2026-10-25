"""社交平台登录的公开状态与内部凭证交付契约。"""

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue

_SESSION_ID_PATTERN = r"^[A-Za-z0-9_-]{16,96}$"

LoginState = Literal["awaiting_scan", "sms_required", "verifying", "verification_required", "logged_in", "expired"]


class SocialLoginStatus(BaseModel):
    session_id: str = Field(pattern=_SESSION_ID_PATTERN)
    state: LoginState
    expires_at: datetime
    verification_error: Literal["invalid_code"] | None = None


class SocialLoginQrCode(BaseModel):
    image: str
    expires_at: datetime
    session_id: str = Field(pattern=_SESSION_ID_PATTERN)


class SocialLoginVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(pattern=_SESSION_ID_PATTERN)
    code: str = Field(pattern=r"^[0-9]{6}$", repr=False)


class SocialLoginResult(SocialLoginStatus):
    """仅供凭证保存使用，不能作为公开 HTTP 响应。"""

    credentials: dict[str, JsonValue] | None = Field(default=None, repr=False)


class SocialLoginClient(Protocol):
    async def qrcode(self, owner: str) -> SocialLoginQrCode: ...

    async def login_session(self, owner: str, session_id: str) -> SocialLoginResult: ...

    async def submit_verification(
        self,
        owner: str,
        payload: SocialLoginVerification,
    ) -> SocialLoginResult: ...

    async def cancel_login(self, owner: str, session_id: str) -> None: ...
