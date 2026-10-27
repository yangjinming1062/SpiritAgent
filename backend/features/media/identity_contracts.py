"""制作请求冻结的可见身份资料；不包含外观持久模型。"""

from typing import Annotated, Literal

from infrastructure.api_ids import ResourceId
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

FeatureText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]
CharacterReferenceRole = Literal["portrait", "fullbody"]


class PortraitFeatures(BaseModel):
    model_config = ConfigDict(extra="forbid")

    head_shape: FeatureText = Field(
        default="",
        description="有视觉依据、改变姿态与视角后仍成立的头脸基本形状；不含发型、表情、透视或遮挡造成的轮廓，不能确认留空",
    )
    facial_features: FeatureText = Field(
        default="",
        description="有视觉依据的眼、鼻、口等固有形状、相对比例与虹膜颜色，不预设部位齐全；排除表情、透视、妆容和接触形变，耳、角等归 head_identifiers，不能确认留空",
    )
    facial_surface: FeatureText = Field(
        default="",
        description="天然面部材质与明确固有颜色；稳定标记归 head_identifiers，排除红晕、高光、唇妆和阴影，不确定留空",
    )
    head_identifiers: FeatureText = Field(
        default="",
        description="自身边界清楚可辨的耳、角等固有结构与能区别于妆饰的稳定标记；不凭遮挡后局部轮廓补全形状，不含头发和饰品，不能确认留空",
    )


class BodyFeatures(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body_shape: FeatureText = Field(
        default="",
        description="头部以外可直接确认的基本体型；不含年龄、性别、姿态、透视、挤压或衣物塑形造成的轮廓，不能确认留空",
    )
    proportions: FeatureText = Field(
        default="",
        description="可可靠对照的身体部位固有比例；不以近大远小、屈伸或透视缩短作依据，不估计遮挡结构或真实身高体重，不能确认留空",
    )
    limbs_and_appendages: FeatureText = Field(
        default="",
        description="实际显露且可确认的肢体及固有附属结构形状、连接和特征；局部显露不证明完整数量，不含姿势、动作、持物、穿鞋或赤足状态，不推测遮挡部位，不能确认留空",
    )
    body_surface: FeatureText = Field(
        default="",
        description="头部以外可确认的天然皮肤、毛皮、鳞片等材质、固有颜色与稳定标记；不含面部特征、衣料、光照、妆饰或临时压痕，不能确认留空",
    )


class CharacterFeatures(PortraitFeatures, BodyFeatures):
    pass


class CharacterOverrides(BaseModel):
    """None 恢复自动值，空字符串是用户明确清空。"""

    model_config = ConfigDict(extra="forbid")

    head_shape: FeatureText | None = None
    facial_features: FeatureText | None = None
    facial_surface: FeatureText | None = None
    head_identifiers: FeatureText | None = None
    body_shape: FeatureText | None = None
    proportions: FeatureText | None = None
    limbs_and_appendages: FeatureText | None = None
    body_surface: FeatureText | None = None


class CharacterCardSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    avatar_id: ResourceId | None
    revision: int
    features: CharacterFeatures
    overrides: CharacterOverrides
