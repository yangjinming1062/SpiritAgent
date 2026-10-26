"""结构化输出的静态决策契约；运行时和工程门禁共用，不调用模型。"""

from typing import Any


class LlmSchemaContractError(TypeError):
    """程序定义与消费者不一致，不能通过模型修复或供应商重试解决。"""


def validate_preserved_choices(
    schema: dict[str, Any],
    fields: tuple[str, ...],
    *,
    schema_name: str,
) -> dict[str, list[Any]]:
    choices: dict[str, list[Any]] = {}
    for name in fields:
        field = schema.get("properties", {}).get(name, {})
        values = field.get("enum") if isinstance(field, dict) else None
        valid = isinstance(field, dict) and ("enum" not in field or isinstance(values, list) and bool(values))
        if valid and "const" in field:
            constant = field["const"]
            valid = values is None or any(type(value) is type(constant) and value == constant for value in values)
            values = [constant]
        elif valid and values is None and field.get("type") == "boolean":
            values = [False, True]
        if valid and isinstance(values, list):
            if field.get("type") == "boolean":
                valid = all(type(value) is bool for value in values)
            if "default" in field:
                valid = valid and any(
                    type(value) is type(field["default"]) and value == field["default"] for value in values
                )
        if not valid or not isinstance(values, list) or not values:
            shape = ", ".join(sorted(field)) if isinstance(field, dict) and field else "missing"
            raise LlmSchemaContractError(
                f"{schema_name}: preserved field {name!r} needs consistent root const, enum or boolean choices; schema keys: {shape}"
            )
        choices[name] = values
    return choices
