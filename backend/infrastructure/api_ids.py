"""数据库标识在 Python 中使用整数，在 JSON 契约中使用十进制字符串。"""

from typing import Annotated

from pydantic import BeforeValidator, PlainSerializer, WithJsonSchema

POSTGRES_BIGINT_MAX = (1 << 63) - 1


def parse_resource_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ValueError("资源标识必须是正整数")
    if isinstance(value, str) and (
        not 1 <= len(value) <= 19 or not value.isascii() or not value.isdecimal() or value.startswith("0")
    ):
        raise ValueError("资源标识必须是规范十进制字符串")
    result = int(value)
    if result <= 0 or result > POSTGRES_BIGINT_MAX:
        raise ValueError("资源标识超出数据库范围")
    return result


ResourceId = Annotated[
    int,
    BeforeValidator(parse_resource_id),
    PlainSerializer(str, return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "pattern": "^[1-9][0-9]*$", "minLength": 1, "maxLength": 19}),
]


def parse_message_boundary(value: object) -> int:
    return 0 if value == "0" or type(value) is int and value == 0 else parse_resource_id(value)


MessageBoundary = Annotated[
    int,
    BeforeValidator(parse_message_boundary),
    PlainSerializer(str, return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "pattern": "^(0|[1-9][0-9]*)$", "minLength": 1, "maxLength": 19}),
]
