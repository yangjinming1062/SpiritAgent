"""维护边界暂停尚未提交的模型调用；已有进度保持可恢复。"""

from components import is_user_in_maintenance

from services.infrastructure.llm import LlmCallBlockedError


class GenerationWorkPaused(LlmCallBlockedError):
    """维护退出后由任务所有者重新读取持久进度。"""

    def __init__(self) -> None:
        super().__init__("账户正在维护，未继续制作")


def require_new_generation_call(user_id: int) -> None:
    if is_user_in_maintenance(user_id):
        raise GenerationWorkPaused
