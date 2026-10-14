"""制作请求冻结的可见身份资料；不包含外观持久模型。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

FeatureText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]


class PortraitFeatures(BaseModel):
    model_config = ConfigDict(extra="forbid")

    head_shape: FeatureText = Field(default="", description="可见头部与脸部轮廓，不含发型或表情")
    facial_features: FeatureText = Field(default="", description="实际五官形状、相对位置与可确认的虹膜颜色，不含妆容")
    facial_surface: FeatureText = Field(
        default="",
        description="天然面部材质与明确固有颜色；排除红晕、高光、唇妆和阴影，不确定留空",
    )
    head_identifiers: FeatureText = Field(
        default="",
        description="耳、角等固有辨识结构或明确稳定标记；不含发丝、发色、发量、发型和饰品",
    )


class BodyFeatures(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body_shape: FeatureText = Field(default="", description="可见整体体型，不含年龄、性别或衣服造成的轮廓")
    proportions: FeatureText = Field(default="", description="可见部位的相对比例；不估计被衣物遮挡的结构或真实身高体重")
    limbs_and_appendages: FeatureText = Field(
        default="",
        description="实际可见肢体及固有附属结构，不含姿势、穿鞋或赤足状态，不推测遮挡部位",
    )
    body_surface: FeatureText = Field(
        default="",
        description="可见天然皮肤、毛皮、鳞片等材质与明确稳定标记；不含衣料、光照或妆容",
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

    avatar_id: int
    revision: int
    features: CharacterFeatures
    overrides: CharacterOverrides
