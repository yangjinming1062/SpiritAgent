from typing import Any

from components import get_logger, safe_json_loads, session_scope

from services.contracts.memory import MemoryScope, MemorySource
from services.domains.memory import list_memories
from services.infrastructure.llm import (
    ServiceType,
    build_responses_kwargs,
    call_with_retry,
    client_for_config,
    resolve_context_tokens,
)
from services.infrastructure.tool_runtime import REGISTRY

from .native_memory import NativeMemory

logger = get_logger(__name__)

_BACKGROUND_REVIEW_PROMPT = """You are a background memory review agent. Review the preceding conversation
and extract durable facts worth remembering across sessions.

Two kinds of memories — pick by how the fact 'shows up' in conversation:

  - kind='auto_inject' (rare): for background context that shapes EVERY exchange
    (rapport, communication style, mood pattern, interaction rhythm, relationship
    signal). Upserts into ONE of these fixed slots (one row each, second write
    overwrites):
      auto_inject:communication_style
      auto_inject:rapport_state
      auto_inject:interaction_pattern
      auto_inject:mood_pattern
      auto_inject:relationship_signal
    Hard cap: 500 chars per row.

  - kind='recall' (default): for on-demand facts you'd retrieve in a specific
    scenario (likes, dislikes, taboos, environment, tool quirks, small user
    facts). Appended to a pool you must call memory_recall to query.
    Pick exactly ONE closed-set tag from:
      user_preference, likes, dislikes, key_constraints, other, tool_quirk, environment
    Note: 'key_constraints' goes to recall — not auto_inject — because taboos
    only matter in matching scenarios, not every turn.

ANTI-PATTERNS — DO NOT EXTRACT OR DUPLICATE:
1. Global system directives & default language: do NOT save 'User speaks Chinese / prefers Chinese' or basic conversation language — the system handles default language globally.
2. Companion's own persona: do NOT save the companion's name, appearance, personality, or species.
3. Pre-configured user profile: do NOT duplicate user onboarding profile facts (name, age bucket, gender, onboarding hobbies).
4. Ephemeral session state: do NOT save task progress, commit hashes, issue numbers, or temporary TODOs.

Pass `kind` explicitly on every memory_retain call. Be concise. If nothing
important, return empty.
DO NOT reply with conversational text or chat. Only invoke tools if necessary.
"""

_WORK_REVIEW_PROMPT = """Review the supplied conversation for durable facts useful to this professional preset.
Use memory_retain with kind='recall' for technical preferences, environment constraints, tool experience,
and long-term work habits. Choose one tag: user_preference, likes, dislikes, key_constraints, other,
tool_quirk, environment. Use kind='auto_inject' only for auto_inject:communication_style (max 500 chars).
Do not evaluate companion mood, intimacy, or relationship state. Do not store temporary progress,
commit hashes, issue numbers, default system settings, duplicate facts, or invented facts.
Only invoke memory_retain if necessary; otherwise return empty. Do not reply conversationally.
"""

_TRUNCATE_SUFFIX = "\n...(truncated)"
_TOOL_OUTPUT_CAP = 1000


def _trim_tool_output(item: dict) -> dict:
    output = item.get("output", "")
    if isinstance(output, str) and len(output) > _TOOL_OUTPUT_CAP:
        return {**item, "output": output[:_TOOL_OUTPUT_CAP] + _TRUNCATE_SUFFIX}
    return item


async def run_background_memory_review(
    scope: MemoryScope,
    llm_config: dict,
    context_snapshot: dict[str, Any],
    *,
    source: MemorySource,
) -> None:
    """fire-and-forget：复盘会话并把值得长期记住的事存下来。"""
    # 工具输出可能很大，截断以保持 review 调用便宜。
    items = [
        _trim_tool_output(item) if item.get("type") == "function_call_output" else item
        for item in context_snapshot["input"]
    ]

    api_key = llm_config.get("api_key")
    base_url = llm_config.get("base_url")
    model_name = llm_config.get("model_name")
    if not api_key or not base_url or not model_name:
        return

    client = client_for_config(llm_config)

    try:
        # 不绑 session：下面的 review LLM 调用直连无 DB；每个 memory_retain 自己开短 session。
        async with session_scope() as db:
            slots = await list_memories(db, scope, kind="auto_inject")
        native_memory = NativeMemory(
            scope,
            source=source,
            slot_versions={row["context"]: (row["id"], row["content_version"]) for row in slots},
        )
        schemas = [REGISTRY.get_schema(scope.user_id, "memory_retain")]

        provider_name = llm_config.get("provider_name", "")
        if not provider_name:
            # resolver 会回落到全局默认；告警以避免配置错的链静默使用 1M 上下文。
            logger.warning("background_review: empty provider_name", extra={"user_id": scope.user_id})
        context_length = resolve_context_tokens(provider_name, ServiceType.llm)
        request = build_responses_kwargs(
            model=model_name,
            instructions=_BACKGROUND_REVIEW_PROMPT if scope.system_preset_id == "companion" else _WORK_REVIEW_PROMPT,
            input_items=items,
            tools=schemas,
            max_output_tokens=500,
        )
        response = await call_with_retry(client, context_length=context_length, **request)
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", None) != "function_call":
                continue
            if getattr(item, "name", "") != "memory_retain":
                continue
            args = safe_json_loads(getattr(item, "arguments", "") or "{}")
            if args and isinstance(args, dict):
                logger.info("Background review extracting memory", extra={"func_args": args})
                await native_memory.execute_tool(item.name, args)
        # 没工具调用说明 review 没找到要记的事。
    except Exception as exc:
        logger.warning("Background memory review failed", extra={"error": str(exc)})
