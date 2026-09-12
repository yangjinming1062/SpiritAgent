from typing import Any

from components import approx_text_tokens

# Responses API: DB↔input-item conversion, token estimation, kwargs assembly.

INPUT_IMAGE_TOKEN_ESTIMATE: int = 800
# 持久层只存 URL 无时长，只能平坦估算；实测视频理解 ~350 token/秒（MiniMax M3），欠估由
# 1M 上下文与每请求 2 个内联上限（VIDEO_INLINE_MAX_PER_REQUEST）兜底，不会撑爆窗口。
INPUT_VIDEO_TOKEN_ESTIMATE: int = 2000


def copy_responses_context(ctx: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy a context dict (``{instructions, input}``) so mutations don't leak."""
    return {"instructions": ctx["instructions"], "input": [dict(item) for item in ctx["input"]]}


def approx_responses_tokens(instructions: str, input_items: Any) -> int:
    return approx_text_tokens(instructions or "") + _value_tokens(input_items)


def _value_tokens(value: Any) -> int:
    if isinstance(value, dict):
        if value.get("type") == "input_image":
            return INPUT_IMAGE_TOKEN_ESTIMATE
        if value.get("type") == "input_video":
            return INPUT_VIDEO_TOKEN_ESTIMATE
        return sum(_value_tokens(item) for item in value.values())
    if isinstance(value, str):
        return approx_text_tokens(value)
    if isinstance(value, list):
        return sum(_value_tokens(item) for item in value)
    return approx_text_tokens(str(value)) if value is not None else 0


def _input_text(text: Any) -> dict[str, Any]:
    return {"type": "input_text", "text": str(text or "")}


def _input_part(part: Any) -> dict[str, Any] | None:
    if isinstance(part, str):
        return _input_text(part)
    if not isinstance(part, dict):
        return None
    if part.get("type") == "input_text":
        return _input_text(part.get("text"))
    if part.get("type") == "input_image":
        image = part.get("image_url")
        return {"type": "input_image", "image_url": str(image)} if image else None
    if part.get("type") == "input_video":
        video = part.get("video_url")
        return {"type": "input_video", "video_url": str(video)} if video else None
    return part


def message_to_response_items(message: dict[str, Any]) -> list[dict[str, Any]]:
    role = message.get("role")
    content = message.get("content")
    if role == "tool":
        call_id = str(message.get("tool_call_id") or message.get("call_id") or "")
        return [{"type": "function_call_output", "call_id": call_id, "output": content if content is not None else ""}]

    if content is None:
        return []
    parts = content if isinstance(content, list) else ([content] if content else [])
    normalized = [normalized for part in parts if (normalized := _input_part(part)) is not None]
    if role == "assistant":
        output = [
            {"type": "output_text", "text": part["text"]} for part in normalized if part.get("type") == "input_text"
        ]
        return [{"role": "assistant", "content": output}] if output else []
    if role == "user":
        return [{"role": "user", "content": normalized}] if normalized else []
    return []


def tool_schema_for_responses(schema: dict[str, Any]) -> dict[str, Any]:
    converted = {
        "type": "function",
        "name": schema.get("name"),
        "parameters": schema.get("parameters") or {"type": "object", "properties": {}},
    }
    if description := schema.get("description"):
        converted["description"] = description
    return converted


def build_responses_kwargs(
    *,
    model: str,
    instructions: str,
    input_items: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    stream: bool = False,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
    reasoning: dict[str, Any] | None = None,
    text: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble kwargs for ``client.responses.create(**kwargs)``。"""
    request: dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "input": input_items,
        "stream": stream,
        "store": False,
    }
    if tools:
        request["tools"] = [tool_schema_for_responses(tool) for tool in tools]
    for key, value in (
        ("temperature", temperature),
        ("max_output_tokens", max_output_tokens),
        ("reasoning", reasoning),
        ("text", text),
    ):
        if value is not None:
            request[key] = value
    return request
