"""账户级管理操作；删除在维护边界内停稳运行时后先删文件再删行。"""

from features.accounts.queries import delete_account_record, require_account
from features.conversation.identifiers import conversation_ids
from features.media.video_queue.service import anonymize_user_video_queue
from infrastructure.assets.asset_store import delete_user_assets
from infrastructure.async_io import run_blocking
from infrastructure.attachments import gc_session as attachments_gc_session
from infrastructure.temp_files import purge_user as purge_user_temp_files
from runtime.responses import MessageResponse
from sqlalchemy.ext.asyncio import AsyncSession

from workflows.maintenance.user import user_maintenance


def _purge_user_files(user_id: int, session_ids: list[str]) -> None:
    """删除该用户的资产目录、各会话附件与名下临时文件。"""
    delete_user_assets(user_id)
    for session_id in session_ids:
        attachments_gc_session(session_id)
    purge_user_temp_files(user_id)


async def delete_user(user_id: int, db: AsyncSession) -> MessageResponse:
    """被遗忘权：在维护边界内停稳该用户的运行时，先删文件再删行（外键级联清理其余数据）；文件先于行删除，任一步失败都保留用户行，管理员重试即可继续清理。"""
    await require_account(db, user_id)
    # 维护边界可能等待在途付费任务数分钟，文件删除也可能较慢；等待期间不持有数据库事务，进入边界后重新加载用户。
    await db.rollback()
    async with user_maintenance(user_id):
        await require_account(db, user_id)
        session_ids = [str(conv_id) for conv_id in await conversation_ids(db, user_id)]
        await db.rollback()
        await anonymize_user_video_queue(user_id)
        await run_blocking(_purge_user_files, user_id, session_ids)
        await delete_account_record(db, user_id)
        await db.commit()
    return MessageResponse(message="用户已删除。")
