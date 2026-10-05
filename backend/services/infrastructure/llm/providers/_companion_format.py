from typing import Any

from openai import APIStatusError, AsyncOpenAI
from openai.types.chat import ChatCompletion
from openai.types.responses import Response


class ChatSchemaResponsesClient:
    """为一次无工具格式修正兼容 Chat Completions 与 Responses。"""

    def __init__(self, client: AsyncOpenAI) -> None:
        self._client = client
        self.base_url = client.base_url
        self.responses = self

    @staticmethod
    def _unsupported_format(error: APIStatusError) -> bool:
        if error.status_code not in {400, 422}:
            return False
        body = error.body
        if isinstance(body, dict):
            body = body.get("error", body)
        format_keys = ("response_format", "json_schema", "json_object", "text.format")
        if (
            isinstance(body, dict)
            and (parameter := body.get("param"))
            and not any(key in str(parameter).lower() for key in format_keys)
        ):
            return False
        message = str(body).lower()
        if any(marker in message for marker in ("content_policy", "content_filter", "moderation", "safety")):
            return False
        return any(key in message for key in format_keys) and any(
            marker in message
            for marker in (
                "not supported",
                "unsupported",
                "unknown parameter",
                "unrecognized parameter",
                "invalid schema",
                "invalid_json_schema",
            )
        )

    @staticmethod
    def _with_idempotency_key(request: dict[str, Any], endpoint: str, format_type: str) -> dict[str, Any]:
        headers = request.get("extra_headers", {})
        if key := headers.get("Idempotency-Key"):
            return {**request, "extra_headers": {**headers, "Idempotency-Key": f"{key}:{endpoint}:{format_type}"}}
        return request

    async def _chat_completion(self, request: dict[str, Any]) -> ChatCompletion:
        # 仅在接口明确拒绝格式参数、尚未产生模型结果时降级；成功返回始终只消费一次。
        while True:
            try:
                format_type = request.get("response_format", {}).get("type", "plain")
                return await self._client.chat.completions.create(
                    **self._with_idempotency_key(request, "chat", format_type),
                )
            except APIStatusError as error:
                if not self._unsupported_format(error) or "response_format" not in request:
                    raise
                if request["response_format"]["type"] == "json_schema":
                    request = {**request, "response_format": {"type": "json_object"}}
                else:
                    request = {key: value for key, value in request.items() if key != "response_format"}

    async def _responses_completion(self, request: dict[str, Any]) -> Response:
        while True:
            try:
                format_type = request.get("text", {}).get("format", {}).get("type", "plain")
                return await self._client.responses.create(
                    **self._with_idempotency_key(request, "responses", format_type),
                )
            except APIStatusError as error:
                if not self._unsupported_format(error) or "text" not in request:
                    raise
                if request["text"]["format"]["type"] == "json_schema":
                    request = {**request, "text": {"format": {"type": "json_object"}}}
                else:
                    request = {key: value for key, value in request.items() if key != "text"}

    async def create(self, **kwargs: Any) -> Response:
        items = kwargs["input"]
        if kwargs.get("stream") or kwargs.get("tools") or len(items) != 1 or items[0].get("role") != "user":
            raise ValueError("Schema repair requires one data message without tools or streaming")
        parts = items[0]["content"]
        if not isinstance(parts, list) or any(part.get("type") != "input_text" for part in parts):
            raise ValueError("Schema repair accepts text data only")
        format_spec = kwargs["text"]["format"]
        request = {
            "model": kwargs["model"],
            "messages": [
                {"role": "system", "content": kwargs["instructions"]},
                {"role": "user", "content": "\n".join(part["text"] for part in parts)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {key: format_spec[key] for key in ("name", "strict", "schema")},
            },
            "store": False,
        }
        for key in ("temperature", "extra_headers"):
            if key in kwargs:
                request[key] = kwargs[key]
        if reasoning := kwargs.get("reasoning"):
            request["reasoning_effort"] = reasoning["effort"]
        if "max_output_tokens" in kwargs:
            request["max_tokens"] = kwargs["max_output_tokens"]
        try:
            completion = await self._chat_completion(request)
        except APIStatusError as error:
            if error.status_code not in {404, 405} or "model" in str(error.body).lower():
                raise
            return await self._responses_completion(kwargs)
        if len(completion.choices) != 1:
            raise ValueError("Schema repair requires one completion choice")
        choice = completion.choices[0]
        message = choice.message
        reason = (
            "content_filter"
            if choice.finish_reason == "content_filter" or message.refusal
            else "max_output_tokens"
            if choice.finish_reason == "length"
            else None
        )
        if reason is None and choice.finish_reason not in {"stop", "tool_calls", "function_call"}:
            raise ValueError("Schema repair did not reach a known completion state")
        output: list[dict] = []
        if reasoning_text := getattr(message, "reasoning_content", None):
            output.append(
                {
                    "id": completion.id + "-reasoning",
                    "type": "reasoning",
                    "summary": [],
                    "content": [{"type": "reasoning_text", "text": reasoning_text}],
                },
            )
        if message.content:
            output.append(
                {
                    "id": completion.id + "-text",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": message.content, "annotations": []}],
                },
            )
        for call in message.tool_calls or []:
            if call.type != "function":
                raise ValueError("Schema repair returned an unsupported tool call")
            output.append(
                {
                    "id": call.id,
                    "call_id": call.id,
                    "type": "function_call",
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            )
        if message.function_call is not None:
            output.append(
                {
                    "call_id": completion.id + "-call",
                    "type": "function_call",
                    "name": message.function_call.name,
                    "arguments": message.function_call.arguments,
                },
            )
        usage = completion.usage
        return Response.model_validate(
            {
                "id": completion.id,
                "created_at": completion.created,
                "model": completion.model,
                "object": "response",
                "status": "incomplete" if reason else "completed",
                "incomplete_details": {"reason": reason} if reason else None,
                "parallel_tool_calls": False,
                "tool_choice": "none",
                "tools": [],
                "output": output,
                "usage": {
                    "input_tokens": usage.prompt_tokens,
                    "output_tokens": usage.completion_tokens,
                    "total_tokens": usage.total_tokens,
                    "input_tokens_details": {
                        "cached_tokens": getattr(usage.prompt_tokens_details, "cached_tokens", 0) or 0,
                        "cache_write_tokens": getattr(usage.prompt_tokens_details, "cache_write_tokens", 0) or 0,
                    },
                    "output_tokens_details": {
                        "reasoning_tokens": getattr(usage.completion_tokens_details, "reasoning_tokens", 0) or 0,
                    },
                }
                if usage
                else None,
            },
        )
