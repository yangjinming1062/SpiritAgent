from typing import Annotated

from fastapi import Depends

from modules.remote import RemoteSession

from .deps import (
    authenticate_remote_session,
    get_current_admin_token,
    get_current_remote_session,
    get_current_session,
    get_current_user,
    get_optional_current_session,
    is_remote_session_active,
)
from .models import LoginRecord, User, UserModelConfig, lock_user_row
from .schemas import (
    ActivateRequest,
    AdminLoginRequest,
    AdminTokenResponse,
    ChatRequestClientContext,
    RefreshRequest,
    TokenResponse,
    UserBackupImportFailure,
    UserBackupImportResponse,
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
    consume_ws_ticket,
    create_access_token,
    create_admin_token,
    create_ws_ticket,
    decode_access_token,
    decode_activation_code,
    encode_activation_code,
    generate_activation_token,
    hash_activation_token,
)

CurrentAdmin = Annotated[str, Depends(get_current_admin_token)]
CurrentSession = Annotated[tuple[User, LoginRecord], Depends(get_current_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentRemoteSession = Annotated[tuple[User, RemoteSession], Depends(get_current_remote_session)]
OptionalSession = Annotated[tuple[User, LoginRecord | RemoteSession] | None, Depends(get_optional_current_session)]

__all__ = [
    "ActivateRequest",
    "AdminLoginRequest",
    "AdminTokenResponse",
    "ChatRequestClientContext",
    "CurrentAdmin",
    "CurrentSession",
    "CurrentRemoteSession",
    "CurrentUser",
    "LoginRecord",
    "OptionalSession",
    "RefreshRequest",
    "TokenResponse",
    "User",
    "UserBackupImportFailure",
    "UserBackupImportResponse",
    "UserCreate",
    "UserInfo",
    "UserListResponse",
    "UserModelConfig",
    "UserModelConfigListItem",
    "UserModelConfigListResponse",
    "UserModelConfigRequest",
    "UserResponse",
    "UserUpdate",
    "authenticate_remote_session",
    "consume_ws_ticket",
    "create_access_token",
    "create_admin_token",
    "create_ws_ticket",
    "decode_access_token",
    "decode_activation_code",
    "encode_activation_code",
    "generate_activation_token",
    "get_current_admin_token",
    "get_current_session",
    "hash_activation_token",
    "is_remote_session_active",
    "lock_user_row",
]
