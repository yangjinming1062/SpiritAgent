"""admin resource operations; transaction and failure semantics belong to the owning capability."""

from infrastructure.queries import get_or_404
from infrastructure.request_errors import RequestError
from infrastructure.text import apply_partial, utc_now
from runtime.channels import terminate_remote_sessions, terminate_user_gateway
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from features.accounts.models import LoginRecord, User, lock_user_row
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


async def list_users(db: AsyncSession) -> UserListResponse:
    users = (await db.execute(select(User).order_by(User.id))).scalars().all()
    return UserListResponse(items=[UserResponse.model_validate(user) for user in users])


async def create_user(payload: UserCreate, db: AsyncSession) -> UserResponse:
    if (await db.execute(select(User).where(User.username == payload.username))).scalar_one_or_none():
        raise RequestError(status_code=status.HTTP_400_BAD_REQUEST, message="用户名已存在。")
    raw_token = generate_activation_token()
    code = encode_activation_code(payload.base_url, raw_token)
    user = User(
        username=payload.username,
        activation_code=code,
        activation_token_hash=hash_activation_token(raw_token),
        nightly_activity_enabled=payload.nightly_activity_enabled,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return UserResponse.model_validate(user)


async def update_user(user_id: int, payload: UserUpdate, db: AsyncSession) -> UserResponse:
    await lock_user_row(db, user_id)
    user = await get_or_404(db, User, id=user_id, message="用户不存在。")
    if payload.base_url:
        _, raw_token = decode_activation_code(user.activation_code)
        user.activation_code = encode_activation_code(payload.base_url, raw_token)
    apply_partial(user, payload, exclude={"base_url"})
    if not user.is_active:
        await revoke_remote_grants(db, user_id)
        await db.execute(
            update(LoginRecord)
            .where(LoginRecord.user_id == user_id, LoginRecord.is_active.is_(True))
            .values(is_active=False, logout_at=utc_now()),
        )
    await db.commit()
    if not user.is_active:
        await terminate_remote_sessions(user_id)
        await terminate_user_gateway(user_id)
    return UserResponse.model_validate(user)


async def regenerate_activation(user_id: int, payload: RegenerateActivationRequest, db: AsyncSession) -> UserResponse:
    await lock_user_row(db, user_id)
    user = await get_or_404(db, User, id=user_id, message="用户不存在。")
    previous_url, _ = decode_activation_code(user.activation_code)
    raw_token = generate_activation_token()
    user.activation_token_hash = hash_activation_token(raw_token)
    user.activation_code = encode_activation_code(payload.base_url or previous_url, raw_token)
    await revoke_remote_grants(db, user_id)
    await db.commit()
    await terminate_remote_sessions(user_id)
    return UserResponse.model_validate(user)
