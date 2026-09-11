from datetime import timedelta

from common import get_router
from components import SETTINGS, DbSession, utc_now
from modules.auth import CurrentUser, LoginRecord
from modules.conversation import Conversation
from modules.system import StatusResponse
from services.ws import MANAGER
from sqlalchemy import func, select

router = get_router()


@router.get("", response_model=StatusResponse)
async def status(user: CurrentUser, db: DbSession) -> StatusResponse:
    login_count = (
        await db.execute(
            select(func.count())
            .select_from(LoginRecord)
            .where(LoginRecord.user_id == user.id, LoginRecord.is_active.is_(True)),
        )
    ).scalar_one()

    window_start = utc_now() - timedelta(minutes=SETTINGS.chat_active_window_minutes)
    chat_count = (
        await db.execute(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.user_id == user.id, Conversation.updated_at >= window_start),
        )
    ).scalar_one()

    connection_state = "connected" if MANAGER.active_connections.get(user.id) is not None else "disconnected"

    return StatusResponse(login_count=login_count, chat_count=chat_count, connection_state=connection_state)
