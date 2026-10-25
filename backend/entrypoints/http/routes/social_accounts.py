from typing import Annotated

import features.configuration.social_accounts as social_configuration
from fastapi import Path, Response
from features.configuration.social_schemas import (
    SocialAccountList,
    SocialAccountView,
    SocialCredentialsRequest,
    SocialVerificationRequest,
)
from infrastructure.database import DbSession
from infrastructure.web.social_login import SocialLoginQrCode, SocialLoginStatus
from infrastructure.web.social_platforms import SocialPlatform

from entrypoints.http.auth import CurrentUser
from entrypoints.http.routing import get_router

router = get_router(prefix="/api/social-accounts")
LoginSessionId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{16,96}$")]


@router.get("", response_model=SocialAccountList)
async def list_social_accounts(user: CurrentUser, db: DbSession, response: Response) -> SocialAccountList:
    response.headers["Cache-Control"] = "no-store"
    return await social_configuration.list_social_accounts(db, user.id)


@router.put("/{platform}/credentials", response_model=SocialAccountView)
async def put_social_credentials(
    platform: SocialPlatform, body: SocialCredentialsRequest, user: CurrentUser, db: DbSession, response: Response
) -> SocialAccountView:
    response.headers["Cache-Control"] = "no-store"
    return await social_configuration.put_social_credentials(db, user.id, platform, body.credentials)


@router.delete("/{platform}", response_model=SocialAccountView)
async def clear_social_account(
    platform: SocialPlatform, user: CurrentUser, db: DbSession, response: Response
) -> SocialAccountView:
    response.headers["Cache-Control"] = "no-store"
    return await social_configuration.clear_social_account(db, user.id, platform)


@router.post("/{platform}/login", response_model=SocialLoginQrCode)
async def start_social_login(
    platform: SocialPlatform, user: CurrentUser, db: DbSession, response: Response
) -> SocialLoginQrCode:
    response.headers["Cache-Control"] = "no-store"
    return await social_configuration.start_social_login(db, user.id, platform)


@router.get("/{platform}/login/{session_id}", response_model=SocialLoginStatus)
async def get_social_login(
    platform: SocialPlatform, session_id: LoginSessionId, user: CurrentUser, db: DbSession, response: Response
) -> SocialLoginStatus:
    response.headers["Cache-Control"] = "no-store"
    return await social_configuration.get_social_login(db, user.id, platform, session_id)


@router.post("/{platform}/login/{session_id}/verification", response_model=SocialLoginStatus)
async def verify_social_login(
    platform: SocialPlatform,
    session_id: LoginSessionId,
    body: SocialVerificationRequest,
    user: CurrentUser,
    db: DbSession,
    response: Response,
) -> SocialLoginStatus:
    response.headers["Cache-Control"] = "no-store"
    return await social_configuration.get_social_login(db, user.id, platform, session_id, code=body.code)


@router.delete("/{platform}/login/{session_id}", response_model=SocialAccountView)
async def cancel_social_login(
    platform: SocialPlatform, session_id: LoginSessionId, user: CurrentUser, db: DbSession, response: Response
) -> SocialAccountView:
    response.headers["Cache-Control"] = "no-store"
    return await social_configuration.cancel_social_login(db, user.id, platform, session_id)
