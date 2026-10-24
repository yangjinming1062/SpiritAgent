"""伙伴呈现视图：把实际呈现快照与可播影像投影为本轮工具资格和提示词资料。

伙伴的当前可见形象以影像库的实际呈现快照为准：窗口读取静态造型与环境资料，桌面读取成品
画面描述。可播影像不冒充已制作完成的呈现事实。
"""

from dataclasses import dataclass, field

from features.visuals.queries import prompt_presentation_snapshot, visual_play_catalog
from features.visuals.schemas import ActualPresentationSnapshot, VisualPlayOption
from infrastructure.text import resolve_language

from workflows.companion_action_prompts import ACTION_VIEW_TEXTS

# 影像工具基线：制作与登记不要求已有成品，只有点播要求存在可播影像。
_ALWAYS_AVAILABLE = frozenset(
    {
        "action_search",
        "action_design",
        "action_inspect",
        "visual_search",
        "visual_inspect",
        "visual_generate",
        "outfit_list",
    },
)
_PLAY_TOOL = "visual_play"


@dataclass(frozen=True)
class ActionViewSnapshot:
    """本轮冻结的呈现与可播影像快照；执行时再校验目标仍有效。"""

    presentation: ActualPresentationSnapshot
    items: tuple[VisualPlayOption, ...] = ()
    available_tool_names: frozenset[str] = field(default_factory=frozenset)

    def to_prompt_block(self, *, language: str = "zh") -> str:
        texts = ACTION_VIEW_TEXTS[resolve_language(language)]
        lines: list[str] = []
        if not self.presentation.ready:
            lines.append(texts["not_presenting"])
        elif self.presentation.visual_description:
            lines.append(texts["presenting_image"])
        elif self.presentation.outfit_description:
            lines.append(texts["presenting_static"])
        else:
            lines.append(texts["presenting_unknown"])
        if self.items:
            names = texts["playable_separator"].join(
                texts["playable_item"].format(
                    name=item.name,
                    clip_kind=item.clip_kind,
                    visual_id=item.visual_id,
                )
                for item in self.items
            )
            lines.append(texts["playable"].format(names=names))
        else:
            lines.append(texts["no_playable"])
        return "\n".join(lines)


async def build_action_view(
    user_id: int,
    *,
    trigger: str = "chat",
) -> ActionViewSnapshot:
    """读取实际呈现快照与可播影像；空库也可用制作工具开始第一次组合。"""

    presentation_snapshot = await prompt_presentation_snapshot(user_id)
    items = tuple(await visual_play_catalog(user_id, trigger=trigger))
    names = set(_ALWAYS_AVAILABLE)
    if items:
        names.add(_PLAY_TOOL)
    return ActionViewSnapshot(
        presentation=presentation_snapshot,
        items=items,
        available_tool_names=frozenset(names),
    )
