"""描述整理结果的通用展示结构：客户端只按 label/value 呈现，不解析业务字段。"""

from pydantic import BaseModel, ConfigDict, Field


class DescribedDetail(BaseModel):
    """一条面向用户的整理结果；label 由服务端按用户语言生成。"""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=24)
    value: str = Field(min_length=1, max_length=600)
