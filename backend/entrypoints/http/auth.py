import secrets
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from features.accounts.deps import (
    authenticate_remote_session,
    get_current_admin_token,
    get_current_remote_session,
    get_current_session,
    get_current_user,
    get_optional_current_session,
)
from features.accounts.security import BEARER_SCHEME, decode_bearer_token
from features.accounts.snapshots import AccountIdentity, LoginIdentity, RemoteDeviceIdentity
from infrastructure.database import DbSession
from runtime.business_actor import BusinessActor
from runtime.chat_accounts import acquire_account_runtime, release_account_runtime
from runtime.client_context import ChatRequestClientContext
from runtime.desktop.connection import MANAGER
from runtime.user_maintenance import begin_user_request, end_user_request

CurrentAdmin = Annotated[str, Depends(get_current_admin_token)]
CurrentSession = Annotated[tuple[AccountIdentity, LoginIdentity], Depends(get_current_session)]
CurrentUser = Annotated[AccountIdentity, Depends(get_current_user)]
CurrentRemoteSession = Annotated[tuple[AccountIdentity, RemoteDeviceIdentity], Depends(get_current_remote_session)]
OptionalSession = Annotated[
    tuple[AccountIdentity, LoginIdentity | RemoteDeviceIdentity] | None,
    Depends(get_optional_current_session),
]


async def get_business_actor(
    request: Request,
    db: DbSession,
    credentials: HTTPAuthorizationCredentials | None = Depends(BEARER_SCHEME),
) -> AsyncIterator[BusinessActor]:
    if credentials is not None:
        user, login = await get_current_session(credentials, db)
        raw_context = decode_bearer_token(credentials).get("ctx")
        context = ChatRequestClientContext.model_validate(raw_context) if raw_context else None
        origin_kind, origin_id = "desktop", str(login.id)
    else:
        user, device = await authenticate_remote_session(request, db, content_only=True)
        context = ChatRequestClientContext(platform_hints="SpiritAgentRemote mobile browser")
        origin_kind, origin_id = "remote", str(device.id)
    if not await begin_user_request(user.id):
        raise HTTPException(status_code=503, detail="用户数据正在维护，请稍后重试。")
    lease_id = f"http:{secrets.token_hex(16)}"
    account = acquire_account_runtime(user.id, lease_id)
    try:
        execution_context = context if origin_kind == "desktop" else MANAGER.desktop_client_context(user.id)
        yield BusinessActor(user.id, origin_kind, origin_id, account, context, execution_context)
    finally:
        release_account_runtime(user.id, lease_id)
        await end_user_request(user.id)


CurrentActor = Annotated[BusinessActor, Depends(get_business_actor)]
