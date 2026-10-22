"""本人出镜的冻结造型计划：桌景/衣柜着装与形象上下文取齐后交调用方拼装。"""

from infrastructure.database import SESSION_LOCAL

from features.appearance.visual_identity import SelfVisualPlan, apply_outfit_override, load_self_visual_context

from .queries import desktop_current_styling_reference


async def load_self_visual_plan(
    user_id: int,
    *,
    outfit_override: str | None = None,
    use_outfit_reference: bool = True,
) -> SelfVisualPlan:
    """styling 查询与形象加载分属两个短会话；override 非空即关着装参考；缺图等错误原样上抛。"""
    override = (outfit_override or "").strip()
    use_outfit_reference = use_outfit_reference and not override
    async with SESSION_LOCAL() as db:
        styling = await desktop_current_styling_reference(db, user_id) if use_outfit_reference else None
    context = await load_self_visual_context(
        user_id,
        use_outfit_reference=use_outfit_reference,
        current_styling=styling,
    )
    return apply_outfit_override(context, override)
