from typing import Annotated

from fastapi import Depends

from .deps import (
    get_current_admin_token,
    get_current_login_record,
    get_current_session,
    get_current_user,
    get_optional_current_session,
)
from .models import LoginRecord, User, UserModelConfig
from .schemas import (
    ActivateRequest,
    AdminLoginRequest,
    AdminTokenResponse,
    ChatRequestClientContext,
    RefreshRequest,
    TokenResponse,
    UserCreate,
    UserInfo,
    UserListResponse,
    UserModelConfigListItem,
    UserModelConfigListResponse,
    UserModelConfigRequest,
    UserResponse,
    UserUpdate,
)
from .security import (
    create_access_token,
    create_admin_token,
    decode_access_token,
    decode_activation_code,
    encode_activation_code,
    generate_activation_token,
    hash_activation_token,
)

CurrentAdmin = Annotated[str, Depends(get_current_admin_token)]
CurrentSession = Annotated[tuple[User, LoginRecord], Depends(get_current_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentLogin = Annotated[LoginRecord, Depends(get_current_login_record)]
OptionalSession = Annotated[tuple[User, LoginRecord] | None, Depends(get_optional_current_session)]

__all__ = [
    "ActivateRequest",
    "AdminLoginRequest",
    "AdminTokenResponse",
    "ChatRequestClientContext",
    "CurrentAdmin",
    "CurrentLogin",
    "CurrentSession",
    "CurrentUser",
    "LoginRecord",
    "OptionalSession",
    "RefreshRequest",
    "TokenResponse",
    "User",
    "UserCreate",
    "UserInfo",
    "UserListResponse",
    "UserModelConfig",
    "UserModelConfigListItem",
    "UserModelConfigListResponse",
    "UserModelConfigRequest",
    "UserResponse",
    "UserUpdate",
    "create_access_token",
    "create_admin_token",
    "decode_access_token",
    "decode_activation_code",
    "encode_activation_code",
    "generate_activation_token",
    "get_current_admin_token",
    "get_current_login_record",
    "get_current_session",
    "get_current_user",
    "get_optional_current_session",
    "hash_activation_token",
]
