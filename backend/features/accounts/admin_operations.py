from infrastructure.request_errors import RequestError
from infrastructure.text import apply_partial
from runtime.channels import terminate_remote_sessions, terminate_user_gateway
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from features.accounts.models import User
from features.accounts.remote.security import revoke_remote_grants
from features.accounts.schemas import (
    RegenerateActivationRequest,
    UserCreate,
    UserListResponse,
    UserResponse,
    UserUpdate,
)
from features.accounts.security import (
    decode_activation_code,
    encode_activation_code,
    generate_activation_token,
    hash_activation_token,
)
from features.accounts.session_operations import revoke_active_logins


async def list_users(db: AsyncSession) -> UserListResponse:
    users = (await db.execute(select(User).order_by(User.id))).scalars().all()
    return UserListResponse(items=[UserResponse.model_validate(user) for user in users])


async def create_user(payload: UserCreate, db: AsyncSession) -> UserResponse:
    raw_token = generate_activation_token()
    user = await db.scalar(
        insert(User)
        .values(
            username=payload.username,
            activation_code=encode_activation_code(payload.base_url, raw_token),
            activation_token_hash=hash_activation_token(raw_token),
            nightly_activity_enabled=payload.nightly_activity_enabled,
        )
        .on_conflict_do_nothing(index_elements=[User.username])
        .returning(User),
    )
    if user is None:
        raise RequestError(status_code=status.HTTP_400_BAD_REQUEST, message="用户名已存在。")
    await db.commit()
    return UserResponse.model_validate(user)


async def _locked_user_or_404(db: AsyncSession, user_id: int) -> User:
    user = await db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise RequestError(status_code=status.HTTP_404_NOT_FOUND, message="用户不存在。")
    return user


async def update_user(user_id: int, payload: UserUpdate, db: AsyncSession) -> UserResponse:
    user = await _locked_user_or_404(db, user_id)
    if payload.base_url:
        _, raw_token = decode_activation_code(user.activation_code)
        user.activation_code = encode_activation_code(payload.base_url, raw_token)
    apply_partial(user, payload, exclude={"base_url"})
    if not user.is_active:
        await revoke_remote_grants(db, user_id)
        await revoke_active_logins(db, user_id)
    await db.commit()
    if not user.is_active:
        await terminate_remote_sessions(user_id)
        await terminate_user_gateway(user_id)
    return UserResponse.model_validate(user)


async def regenerate_activation(user_id: int, payload: RegenerateActivationRequest, db: AsyncSession) -> UserResponse:
    user = await _locked_user_or_404(db, user_id)
    previous_url, _ = decode_activation_code(user.activation_code)
    raw_token = generate_activation_token()
    user.activation_token_hash = hash_activation_token(raw_token)
    user.activation_code = encode_activation_code(payload.base_url or previous_url, raw_token)
    await revoke_remote_grants(db, user_id)
    await db.commit()
    await terminate_remote_sessions(user_id)
    return UserResponse.model_validate(user)
