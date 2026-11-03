"""制作请求冻结的视觉身份版本与内部视觉观察。"""

from typing import Literal

from infrastructure.api_ids import ResourceId
from pydantic import BaseModel, ConfigDict, Field

CharacterReferenceRole = Literal["portrait", "fullbody"]


class VisualIdentitySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # 分组恢复未导入身份时保留脱离当前身份的快照，禁止旧任务冒用目标账户形象。
    avatar_id: ResourceId | None
    revision: int = Field(ge=1)


class VisualObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(
        max_length=1000, description="只概括直接可见的身体外观，不含服装、发型、妆饰或年龄等人物设定推断"
    )
    body_structure: str = Field(
        max_length=1000, description="记录实际可见的身体结构与材质；未看到不证明不存在，不补全被遮挡部位"
    )
    activity_conditions: str = Field(
        max_length=1000, description="只记图像支持的支撑、接触和操作部位条件；实际运动或负重能力未知时不推断"
    )
    uncertainties: list[str] = Field(
        max_length=12, description="影响身体结构或活动理解的未知项；无需猜测的人物年龄和背景不列入"
    )
