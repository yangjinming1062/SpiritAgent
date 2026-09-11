"""伴侣资产生成中 LLM 输出校验器的共用辅助函数。"""

from typing import Any


def parse_tags(raw: Any) -> list[str]:
    """将 LLM 输出的 tags 字段归一化为字符串列表，过滤空值。"""
    if not isinstance(raw, list):
        return []
    return [str(t).strip() for t in raw if str(t).strip()]
