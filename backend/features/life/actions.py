"""可执行生活步骤的输入与永久阅读成果，不包含能力实现。"""

from datetime import datetime
from typing import Annotated, Literal

from infrastructure.api_ids import ResourceId
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter


class ActionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class VisualAction(ActionInput):
    capability: Literal["visual.generate"]
    outfit_id: ResourceId = Field(gt=0, description="outfits 中已有造型的 id")
    scene_id: ResourceId = Field(gt=0, description="scenes 中已有环境的 id")
    action_id: ResourceId = Field(gt=0, description="actions 中已通过评审的动作 id")
    media_kind: Literal["image", "video"] = Field(default="image", description="本次要制作的成品类型")
    requirements: str = Field(min_length=1, max_length=4000, description="具体画面与用途，须与所选造型、环境和动作相容")


class SceneAction(ActionInput):
    capability: Literal["scene.create"]
    notes: str = Field(min_length=1, max_length=4000, description="需要制作的环境内容和用途，不把伙伴画入环境素材")


class OutfitAction(ActionInput):
    capability: Literal["outfit.create"]
    description: str = Field(min_length=1, max_length=4000, description="要添置的服装与造型要求，保留伙伴身份")


class DesignAction(ActionInput):
    capability: Literal["action.design"]
    name: str = Field(min_length=1, max_length=64, description="动作的简短名称")
    motion_description: str = Field(
        min_length=10, max_length=600, description="身体动作、过程和必要支撑，不表示已经执行"
    )
    reason: str = Field(min_length=1, max_length=400, description="当前生活为什么需要这种动作")
    use_when: list[str] = Field(default_factory=list, max_length=8, description="适合使用该动作的具体情境")
    avoid_when: list[str] = Field(default_factory=list, max_length=8, description="不宜使用该动作的情境")
    duration_seconds: int = Field(default=4, ge=1, le=15)
    clip_kind: Literal["once", "loop"] = "once"


class ReadingAction(ActionInput):
    capability: Literal["content.read"]
    purpose: str = Field(
        min_length=1,
        max_length=2000,
        description=(
            "伙伴现在为什么想读、希望弄清什么，以及与当前事项或已有认识的关系；没有关联时不编造。 / "
            "Why the companion wants to read now, what to understand, and how it relates to a current pursuit or "
            "prior understanding; do not invent a connection."
        ),
    )
    query: str = Field(default="", max_length=500, description="未指定 urls 且仍有阅读余量时搜索一次的关键词")
    urls: list[str] = Field(
        default_factory=list,
        max_length=3,
        description="公开网页原文地址；已有资讯先读取，再按剩余名额读取网页，总计最多三份",
    )
    topic_ids: list[ResourceId] = Field(
        default_factory=list, max_length=3, description="topics 中已保存资讯的 id；不是已经阅读的证明"
    )


LifeAction = Annotated[
    VisualAction | SceneAction | OutfitAction | DesignAction | ReadingAction, Field(discriminator="capability")
]
LIFE_ACTION = TypeAdapter(LifeAction)
CREATION_CAPABILITIES = frozenset({"visual.generate", "scene.create", "outfit.create", "action.design"})


class ReadingSource(ActionInput):
    url: str = Field(pattern=r"^https?://", max_length=2048)
    title: str
    retrieved_at: datetime
    content: str = Field(min_length=1, max_length=50000)
    content_kind: Literal["extracted_text", "summary"] = "extracted_text"
    excerpted: bool = False


class ReadingResult(ActionInput):
    sources: list[ReadingSource] = Field(min_length=1, max_length=3)
    notes: str = Field(min_length=1, max_length=12000)


class ReadingSourceView(ActionInput):
    url: str
    title: str
    retrieved_at: datetime
    content_kind: Literal["extracted_text", "summary"]
    excerpted: bool


class ReadingResultView(ActionInput):
    sources: list[ReadingSourceView]
    notes: str


class NextAction(ActionInput):
    description: str = Field(
        min_length=1, max_length=1000, description="向用户说明这一步要做什么，表达为计划而非既成成果"
    )
    action: LifeAction
    not_before: AwareDatetime | None = Field(
        default=None, description="最早执行时刻，须包含时区；省略表示当前条件允许即可执行"
    )
