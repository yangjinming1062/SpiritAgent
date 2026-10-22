import secrets
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials
from features.accounts.deps import (
    account_request_scope,
    authenticate_remote_session,
    get_current_admin_token,
    get_current_remote_session,
    get_current_session,
    get_current_user,
    get_optional_current_session,
    session_from_payload,
)
from features.accounts.gateway import GatewayIdentity
from features.accounts.security import BEARER_SCHEME, decode_bearer_token
from features.accounts.snapshots import AccountIdentity, LoginIdentity, RemoteDeviceIdentity
from infrastructure.database import DbSession
from runtime.business_actor import BusinessActor
from runtime.chat_accounts import acquire_account_runtime, release_account_runtime
from runtime.client_context import ChatRequestClientContext
from runtime.desktop.connection import MANAGER
from sqlalchemy.ext.asyncio import AsyncSession

CurrentAdmin = Annotated[str, Depends(get_current_admin_token)]
CurrentSession = Annotated[tuple[AccountIdentity, LoginIdentity], Depends(get_current_session)]
CurrentUser = Annotated[AccountIdentity, Depends(get_current_user)]
CurrentRemoteSession = Annotated[tuple[AccountIdentity, RemoteDeviceIdentity], Depends(get_current_remote_session)]
OptionalSession = Annotated[
    tuple[AccountIdentity, LoginIdentity | RemoteDeviceIdentity] | None,
    Depends(get_optional_current_session),
]


async def get_request_identity(
    request: Request,
    db: AsyncSession,
    credentials: HTTPAuthorizationCredentials | None,
    *,
    content_only: bool = True,
    stream_id: str | None = None,
) -> GatewayIdentity:
    if credentials is not None:
        # ctx 与会话鉴权共用同一份已验签 payload，不二次解码；exp 只在首次验签时校验，吊销由 DB 会话状态承担。
        payload = decode_bearer_token(credentials)
        user, identity = await session_from_payload(payload, db)
        raw_context = payload.get("ctx")
        return GatewayIdentity(
            user_id=user.id,
            login_record_id=identity.id,
            client_context=ChatRequestClientContext.model_validate(raw_context) if raw_context else None,
            stream_id=stream_id,
        )
    user, identity = await authenticate_remote_session(request, db, content_only=content_only)
    return GatewayIdentity(
        user_id=user.id,
        device_id=identity.id,
        client_context=ChatRequestClientContext(platform_hints="SpiritAgentRemote mobile browser"),
        stream_id=stream_id,
    )


async def get_business_actor(
    request: Request,
    db: DbSession,
    credentials: HTTPAuthorizationCredentials | None = Depends(BEARER_SCHEME),
) -> AsyncIterator[BusinessActor]:
    identity = await get_request_identity(request, db, credentials)
    origin_kind = "remote" if identity.device_id is not None else "desktop"
    origin_id = str(identity.device_id if identity.device_id is not None else identity.login_record_id)
    async with account_request_scope(identity.user_id):
        lease_id = f"http:{secrets.token_hex(16)}"
        account = acquire_account_runtime(identity.user_id, lease_id)
        try:
            execution_context = (
                identity.client_context
                if origin_kind == "desktop"
                else MANAGER.desktop_client_context(identity.user_id)
            )
            yield BusinessActor(
                identity.user_id,
                origin_kind,
                origin_id,
                account,
                identity.client_context,
                execution_context,
            )
        finally:
            release_account_runtime(identity.user_id, lease_id)


CurrentActor = Annotated[BusinessActor, Depends(get_business_actor)]
