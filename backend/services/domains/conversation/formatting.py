from components import safe_json_loads
from modules.conversation import Message

_ROLE_LABELS: dict[str, str] = {"user": "用户", "assistant": "伙伴"}


def message_text(m: Message) -> str:
    """从 Message 行抽取纯文本；``content_type == "multimodal_v1"`` 行只返回 text part，避免 JSON 数组泄到 prompt 里。"""
    raw = (m.content or "").strip()

    if not raw or getattr(m, "content_type", "text") != "multimodal_v1":
        return raw

    parsed = safe_json_loads(raw, default=None)

    if not isinstance(parsed, list):
        return raw

    return "\n".join(
        p.get("text", "") for p in parsed if isinstance(p, dict) and p.get("type") in {"input_text", "text"}
    ).strip()


def format_messages_compact(msgs: list[Message], *, char_cap: int | None = None) -> str:
    """把消息列表格式化为「角色: 文本」紧凑文本，供 LLM 提示复用。"""
    return "\n".join(
        f"{_ROLE_LABELS.get(m.role, m.role)}: {text[:char_cap]}" for m in msgs if (text := message_text(m))
    )
