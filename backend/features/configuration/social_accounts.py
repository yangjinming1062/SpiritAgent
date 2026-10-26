"""账户资讯来源与可恢复登录；平台凭证仅在后端与采集服务之间流转。"""

import asyncio
from contextlib import suppress
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

from infrastructure.database import session_scope
from infrastructure.logger import get_logger
from infrastructure.request_errors import RequestError
from infrastructure.text import utc_now
from infrastructure.web.social_content import SocialSourceError
from infrastructure.web.social_login import SocialLoginQrCode, SocialLoginStatus, SocialLoginVerification
from infrastructure.web.social_platforms import SOCIAL_PLATFORMS, get_social_platform
from infrastructure.web.social_sources import (
    create_social_login_client,
    parse_social_credentials,
    validate_social_credentials,
)
from runtime.lifecycle import RuntimeParticipant
from runtime.task_supervisor import TASK_SUPERVISOR
from runtime.user_maintenance import is_user_in_maintenance, track_user_task
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.locking import lock_account
from features.accounts.queries import lock_active_user_ids, user_is_active

from .models import UserSocialAccount
from .social_schemas import SocialAccountList, SocialAccountView, SocialCredentialSnapshot

logger = get_logger(__name__)
_OWNER = "configuration.social_logins"
_workers: dict[tuple[int, str], tuple[str, asyncio.Task[None]]] = {}
_TERMINAL_LOGIN_STATES = {"logged_in", "expired"}


def _require_platform(platform: str, *, login: bool = False) -> None:
    try:
        definition = get_social_platform(platform)
    except ValueError:
        raise RequestError(404, "不支持此资讯来源。") from None
    if login and definition.auth_kind != "qr":
        raise RequestError(409, "此资讯来源不支持扫码登录。")


def _login_status(row: UserSocialAccount) -> SocialLoginStatus | None:
    if not row.login_session_id or not row.login_state or not row.login_expires_at:
        return None
    return SocialLoginStatus.model_validate(
        {
            "session_id": row.login_session_id,
            "state": row.login_state,
            "expires_at": row.login_expires_at,
            "verification_error": row.login_verification_error,
        }
    )


def _view(platform: str, row: UserSocialAccount | None) -> SocialAccountView:
    return SocialAccountView.model_validate(
        {
            "platform": platform,
            "auth_kind": get_social_platform(platform).auth_kind,
            "configured": row is not None and bool(row.credentials),
            "state": row.state if row else "unconfigured",
            "login": _login_status(row) if row else None,
        }
    )


async def _row(db: AsyncSession, user_id: int, platform: str) -> UserSocialAccount | None:
    return await db.scalar(
        select(UserSocialAccount)
        .where(UserSocialAccount.user_id == user_id, UserSocialAccount.platform == platform)
        .execution_options(populate_existing=True)
    )


async def _locked_row(db: AsyncSession, user_id: int, platform: str) -> UserSocialAccount:
    if not await lock_active_user_ids(db, (user_id,)) or is_user_in_maintenance(user_id):
        raise RequestError(409, "账户暂不可用，请稍后重试。")
    row = await _row(db, user_id, platform)
    if row is None:
        row = UserSocialAccount(user_id=user_id, platform=platform)
        db.add(row)
        await db.flush()
    return row


async def list_social_accounts(db: AsyncSession, user_id: int) -> SocialAccountList:
    rows = (await db.scalars(select(UserSocialAccount).where(UserSocialAccount.user_id == user_id))).all()
    by_platform = {row.platform: row for row in rows}
    return SocialAccountList(accounts=[_view(platform, by_platform.get(platform)) for platform in SOCIAL_PLATFORMS])


async def load_social_credentials(db: AsyncSession, user_id: int) -> dict[str, SocialCredentialSnapshot]:
    rows = await db.scalars(
        select(UserSocialAccount).where(UserSocialAccount.user_id == user_id, UserSocialAccount.state == "configured")
    )
    return {
        row.platform: SocialCredentialSnapshot(row.revision, deepcopy(row.credentials))
        for row in rows
        if row.credentials
    }


async def credential_is_current(db: AsyncSession, user_id: int, platform: str, revision: str) -> bool:
    return bool(
        await db.scalar(
            select(UserSocialAccount.id).where(
                UserSocialAccount.user_id == user_id,
                UserSocialAccount.platform == platform,
                UserSocialAccount.revision == revision,
                UserSocialAccount.state == "configured",
                UserSocialAccount.credentials.is_not(None),
            )
        )
    )


async def mark_login_required(db: AsyncSession, user_id: int, platform: str, revision: str) -> None:
    await db.execute(
        update(UserSocialAccount)
        .where(
            UserSocialAccount.user_id == user_id,
            UserSocialAccount.platform == platform,
            UserSocialAccount.revision == revision,
        )
        .values(state="login_required")
    )


def _reset_login(row: UserSocialAccount) -> None:
    row.login_revision = None
    row.login_session_id = None
    row.login_state = None
    row.login_expires_at = None
    row.login_verification_error = None


def _pending_login(row: UserSocialAccount | None) -> tuple[str, str] | None:
    if row and row.login_revision and row.login_session_id:
        return _login_owner(row), row.login_session_id
    return None


def _login_owner(row: UserSocialAccount) -> str:
    return f"{row.user_id}_{row.source_key}"


async def _cancel_remote(platform: str, pending: tuple[str, str] | None) -> None:
    if pending is None:
        return
    try:
        await create_social_login_client(platform).cancel_login(*pending)
    except (SocialSourceError, ValueError) as exc:
        # 本地修订已撤销，服务端会话仍有固定到期时间；失败不能恢复旧凭证。
        reason = exc.reason if isinstance(exc, SocialSourceError) else "invalid_request"
        logger.warning("social login cleanup failed", extra={"platform": platform, "reason": reason})


async def _stop_worker(user_id: int, platform: str, pending: tuple[str, str] | None) -> None:
    worker = _workers.get((user_id, platform))
    if worker is not None and pending is not None and worker[0] == pending[1]:
        task = worker[1]
        if task is asyncio.current_task():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def put_social_credentials(db: AsyncSession, user_id: int, platform: str, raw: str) -> SocialAccountView:
    _require_platform(platform)
    try:
        credentials = parse_social_credentials(platform, raw)
    except ValueError:
        raise RequestError(422, "凭证格式无效，请检查是否包含所需的登录 Cookie。") from None
    row = await _locked_row(db, user_id, platform)
    pending = _pending_login(row)
    row.credentials = credentials
    row.revision = str(uuid4())
    row.state = "configured"
    _reset_login(row)
    await db.commit()
    result = _view(platform, row)
    await _stop_worker(user_id, platform, pending)
    await _cancel_remote(platform, pending)
    return result


async def clear_social_account(db: AsyncSession, user_id: int, platform: str) -> SocialAccountView:
    _require_platform(platform)
    row = await _locked_row(db, user_id, platform)
    pending = _pending_login(row)
    row.credentials = None
    row.revision = str(uuid4())
    row.state = "unconfigured"
    _reset_login(row)
    await db.commit()
    result = _view(platform, row)
    await _stop_worker(user_id, platform, pending)
    await _cancel_remote(platform, pending)
    return result


def _request_failure(error: SocialSourceError) -> RequestError:
    known = {
        "session_expired": (410, "登录会话已到期，请重新扫码。"),
        "session_conflict": (409, "登录会话已结束或被替换，请重新扫码。"),
        "login_page_check_failed": (502, "登录页面状态检查失败，请稍后重试。"),
        "login_code_input_failed": (502, "验证码输入失败，请检查登录状态后重试。"),
        "login_confirm_button_failed": (502, "验证码确认失败，请先检查登录状态。"),
        "login_cookie_save_failed": (502, "登录凭证读取失败，请重新扫码。"),
        "rate_limited": (429, "登录服务繁忙，请稍后重试。"),
    }
    status, message = known.get(error.reason, (503, "资讯来源暂不可用，请稍后重试。"))
    return RequestError(status, message, code=error.reason)


async def start_social_login(db: AsyncSession, user_id: int, platform: str) -> SocialLoginQrCode:
    _require_platform(platform, login=True)
    row = await _locked_row(db, user_id, platform)
    previous = _pending_login(row)
    _reset_login(row)
    revision = str(uuid4())
    owner = _login_owner(row)
    row.login_revision = revision
    await db.commit()
    await _stop_worker(user_id, platform, previous)
    await _cancel_remote(platform, previous)
    try:
        result = await create_social_login_client(platform).qrcode(owner)
    except SocialSourceError as exc:
        raise _request_failure(exc) from None
    row = await _locked_row(db, user_id, platform)
    if row.login_revision != revision:
        await db.commit()
        await _cancel_remote(platform, (owner, result.session_id))
        raise RequestError(409, "登录会话已被替换，请重新扫码。")
    row.login_session_id = result.session_id
    row.login_expires_at = result.expires_at
    row.login_state = "awaiting_scan"
    await db.commit()
    _start_worker(user_id, platform, result.session_id)
    return result


async def _login_binding(db: AsyncSession, user_id: int, platform: str, session_id: str) -> UserSocialAccount:
    _require_platform(platform, login=True)
    row = await _row(db, user_id, platform)
    if row is None or row.login_session_id != session_id or row.login_revision is None:
        raise RequestError(409, "登录会话已结束或不属于当前账户。")
    return row


async def get_social_login(
    db: AsyncSession, user_id: int, platform: str, session_id: str, *, code: str | None = None
) -> SocialLoginStatus:
    row = await _login_binding(db, user_id, platform, session_id)
    return await _refresh_login(db, row, code=code)


async def _refresh_login(db: AsyncSession, row: UserSocialAccount, *, code: str | None = None) -> SocialLoginStatus:
    user_id, platform = row.user_id, row.platform
    revision = row.login_revision
    owner = _login_owner(row)
    saved = _login_status(row)
    await db.commit()
    if revision is None or saved is None:
        raise RequestError(409, "登录会话已结束，请重新扫码。")
    session_id = saved.session_id
    if saved.state in _TERMINAL_LOGIN_STATES:
        return saved
    client = create_social_login_client(platform)
    try:
        result = (
            await client.submit_verification(owner, SocialLoginVerification(session_id=session_id, code=code))
            if code is not None
            else await client.login_session(owner, session_id)
        )
    except SocialSourceError as exc:
        if exc.reason in {"session_expired", "session_conflict"}:
            await lock_account(db, user_id)
            current = await _row(db, user_id, platform)
            if current is not None and current.login_revision == revision and current.login_session_id == session_id:
                if current.login_state == "logged_in":
                    completed = _login_status(current)
                    await db.commit()
                    if completed is not None:
                        return completed
                else:
                    current.login_state = "expired"
            await db.commit()
        raise _request_failure(exc) from None
    if result.session_id != session_id:
        raise RequestError(502, "登录服务返回的会话状态无效。")
    row = await _locked_row(db, user_id, platform)
    if row.login_revision != revision or row.login_session_id != session_id:
        await db.commit()
        raise RequestError(409, "登录会话已被替换，请重新扫码。")
    if row.login_state == "logged_in":
        completed = _login_status(row)
        await db.commit()
        if completed is None:
            raise RequestError(409, "登录会话已结束，请重新扫码。")
        return completed
    if result.state == "logged_in":
        try:
            credentials = validate_social_credentials(platform, result.credentials)
        except ValueError:
            raise RequestError(502, "登录服务没有返回有效的账户凭证。") from None
        row.credentials = credentials
        row.revision = str(uuid4())
        row.state = "configured"
    row.login_state = result.state
    row.login_verification_error = result.verification_error
    await db.commit()
    public = SocialLoginStatus.model_validate(result.model_dump(exclude={"credentials"}))
    if result.state == "logged_in":
        await _cancel_remote(platform, (owner, session_id))
    return public


async def cancel_social_login(db: AsyncSession, user_id: int, platform: str, session_id: str) -> SocialAccountView:
    await lock_account(db, user_id)
    row = await _login_binding(db, user_id, platform, session_id)
    pending = _pending_login(row)
    _reset_login(row)
    await db.commit()
    result = _view(platform, row)
    await _stop_worker(user_id, platform, pending)
    await _cancel_remote(platform, pending)
    return result


async def _poll_login(user_id: int, platform: str, session_id: str) -> None:
    try:
        while not is_user_in_maintenance(user_id):
            async with session_scope() as db:
                row = await _row(db, user_id, platform)
                if (
                    row is None
                    or row.login_session_id != session_id
                    or row.login_expires_at is None
                    or row.login_state in _TERMINAL_LOGIN_STATES
                    or not await user_is_active(db, user_id)
                ):
                    return
                if utc_now() >= row.login_expires_at + timedelta(minutes=4):
                    await db.execute(
                        update(UserSocialAccount)
                        .where(
                            UserSocialAccount.id == row.id,
                            UserSocialAccount.login_revision == row.login_revision,
                            UserSocialAccount.login_session_id == session_id,
                            UserSocialAccount.login_state.not_in(_TERMINAL_LOGIN_STATES),
                        )
                        .values(login_state="expired")
                    )
                    await db.commit()
                    return
                try:
                    result = await _refresh_login(db, row)
                except RequestError as exc:
                    if exc.status_code in {404, 409, 410, 422}:
                        return
                else:
                    if result.state in _TERMINAL_LOGIN_STATES:
                        return
            await asyncio.sleep(2)
    finally:
        if is_user_in_maintenance(user_id):
            async with session_scope() as db:
                current = await _row(db, user_id, platform)
                if current is not None and current.login_session_id == session_id:
                    pending = _pending_login(current)
                    _reset_login(current)
                    await db.commit()
                    await _cancel_remote(platform, pending)
        key = (user_id, platform)
        worker = _workers.get(key)
        if worker is not None and worker[1] is asyncio.current_task():
            _workers.pop(key, None)


def _start_worker(user_id: int, platform: str, session_id: str) -> None:
    key = user_id, platform
    if is_user_in_maintenance(user_id):
        return
    previous = _workers.get(key)
    if previous is not None:
        if previous[0] == session_id and not previous[1].done():
            return
        previous[1].cancel()
    task = asyncio.create_task(_poll_login(user_id, platform, session_id), name=f"social-login:{platform}:{user_id}")
    _workers[key] = (session_id, task)
    track_user_task(user_id, task, owner=_OWNER)


async def resume_user_social_logins(user_id: int | None = None) -> None:
    async with session_scope() as db:
        query = select(UserSocialAccount).where(
            UserSocialAccount.login_session_id.is_not(None),
            UserSocialAccount.login_state.not_in(_TERMINAL_LOGIN_STATES),
        )
        if user_id is not None:
            query = query.where(UserSocialAccount.user_id == user_id)
        rows = (await db.scalars(query)).all()
        for row in rows:
            if row.login_session_id and row.platform in SOCIAL_PLATFORMS:
                _start_worker(row.user_id, row.platform, row.login_session_id)


async def drain_social_logins() -> None:
    await TASK_SUPERVISOR.drain(owner=_OWNER)


def runtime_participants() -> tuple[RuntimeParticipant, ...]:
    return (
        RuntimeParticipant(
            _OWNER, recover=resume_user_social_logins, resume_user=resume_user_social_logins, drain=drain_social_logins
        ),
    )
