"""维护边界暂停尚未提交的模型调用；已有进度保持可恢复。"""

from components import SESSION_LOCAL, is_user_in_maintenance
from modules.channels import ChannelPeer, ChannelTurnSource

from services.infrastructure.llm import LlmCallBlockedError


class GenerationWorkPaused(LlmCallBlockedError):
    """维护退出后由任务所有者重新读取持久进度。"""

    def __init__(self) -> None:
        super().__init__("账户正在维护，未继续制作")


class GenerationAuthorizationRevoked(LlmCallBlockedError):
    """原对端撤权后不再开始新的付费制作。"""

    def __init__(self) -> None:
        super().__init__("原通道授权已撤销，未继续制作或投递")


def require_new_generation_call(user_id: int) -> None:
    if is_user_in_maintenance(user_id):
        raise GenerationWorkPaused


async def require_video_generation_call(user_id: int, channel_source: ChannelTurnSource | None) -> None:
    require_new_generation_call(user_id)
    if channel_source is not None:
        async with SESSION_LOCAL() as db:
            if not await ChannelPeer.authorizes(db, user_id, channel_source):
                raise GenerationAuthorizationRevoked
    require_new_generation_call(user_id)
