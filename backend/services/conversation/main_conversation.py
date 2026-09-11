from modules.conversation import Conversation
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .presets import resolve_preset_meta

# ``SPECIAL_KIND`` 对应系统预设对话：每用户固定 5 条（companion/developer/product_manager/copywriter/language_teacher），system_preset_id 标注具体预设。原来的 ``MAIN_KIND = 'main'`` 单条主会话已合并入 ``special + system_preset_id='companion'``。
SPECIAL_KIND = "special"
# 外部 IM 渠道桥接的会话：统一一种 kind，每渠道一条专属对话由 channel_bindings.conversation_id 唯一外键锚定（services/channels/conversation.py 工厂）；prompt.submit 拒写，桌面端只读旁观。
IM_KIND = "im"
# ``kind`` 列的 SQL server_default 值。
STANDARD_KIND = "standard"

# UI-only 子类型：渲染端展示但排除出 LLM 上下文；与 SPECIAL_KIND 同处一处保证所有会话读取者一致。status_proactive 故意不在此集合——它是用户可回应的真实轮次。
UI_ONLY_SUBTYPES: frozenset[str] = frozenset({"hint", "status_interaction", "status_reaction"})

# 后台视频任务完成后的送达行：媒体列携带可播放 URL，渲染端显示为媒体卡。故意不在 UI_ONLY_SUBTYPES——主会话工具帧被摘要行替代后，这行是 LLM 回答「视频好了吗」的结果来源。
MEDIA_STATUS_SUBTYPE: str = "status_media"


async def get_special_conversation(db: AsyncSession, user_id: int, preset_id: str) -> Conversation | None:
    """获取用户某一系统预设的特殊对话；preset_id ∈ {companion, developer, product_manager, copywriter, language_teacher}。"""
    return (
        await db.execute(
            select(Conversation).where(
                Conversation.user_id == user_id,
                Conversation.kind == SPECIAL_KIND,
                Conversation.system_preset_id == preset_id,
            ),
        )
    ).scalar_one_or_none()


async def get_or_create_special_conversation(db: AsyncSession, user_id: int, preset_id: str) -> Conversation:
    """获取系统预设对话，首次访问时创建；并发竞态由 ``uq_conversations_user_preset`` 部分唯一索引兜底，败者回滚重读到胜者行。"""
    conv = await get_special_conversation(db, user_id, preset_id)
    if conv is not None:
        return conv

    preset = resolve_preset_meta(preset_id)
    conv = Conversation(
        user_id=user_id,
        kind=SPECIAL_KIND,
        system_preset_id=preset_id,
        title=preset.name,
        is_deletable=False,
        is_renamable=False,
    )
    try:
        async with db.begin_nested():
            db.add(conv)
            await db.flush()
    except IntegrityError:
        existing = await get_special_conversation(db, user_id, preset_id)
        if existing is not None:
            return existing
        raise
    await db.commit()
    await db.refresh(conv)
    return conv
