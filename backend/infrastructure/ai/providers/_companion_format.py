from collections.abc import Awaitable, Callable
from typing import Any, Literal

from openai import APIStatusError, AsyncOpenAI
from openai.types.chat import ChatCompletion
from openai.types.responses import Response, ResponseUsage
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails

from infrastructure.ai.usage import read_llm_usage


class ChatSchemaResponsesClient:
    """为一次无工具格式修正兼容 Chat Completions 与 Responses。"""

    def __init__(
        self,
        client: AsyncOpenAI,
        *,
        before_submit: Callable[[], Awaitable[None]] | None = None,
        token_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens",
        chat_extra_body: dict[str, Any] | None = None,
    ) -> None:
        self._client = client
        self._before_submit = before_submit
        self._token_parameter = token_parameter
        self._chat_extra_body = dict(chat_extra_body or {})
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

    async def _complete_with_format_fallback[T](
        self,
        request: dict[str, Any],
        create: Callable[..., Awaitable[T]],
        *,
        endpoint: Literal["chat", "responses"],
    ) -> T:
        # 仅在接口明确拒绝格式参数、尚未产生模型结果时降级；成功返回始终只消费一次。
        format_key = "response_format" if endpoint == "chat" else "text"
        while True:
            try:
                if self._before_submit is not None:
                    await self._before_submit()
                format_spec = (
                    request.get("response_format", {})
                    if endpoint == "chat"
                    else request.get("text", {}).get("format", {})
                )
                return await create(**self._with_idempotency_key(request, endpoint, format_spec.get("type", "plain")))
            except APIStatusError as error:
                if not self._unsupported_format(error) or format_key not in request:
                    raise
                format_spec = request["response_format"] if endpoint == "chat" else request["text"]["format"]
                if format_spec["type"] == "json_schema":
                    fallback = {"type": "json_object"}
                    request = {**request, format_key: fallback if endpoint == "chat" else {"format": fallback}}
                else:
                    request = {key: value for key, value in request.items() if key != format_key}

    async def _chat_completion(self, request: dict[str, Any]) -> ChatCompletion:
        return await self._complete_with_format_fallback(
            request,
            self._client.chat.completions.create,
            endpoint="chat",
        )

    async def _responses_completion(self, request: dict[str, Any]) -> Response:
        return await self._complete_with_format_fallback(request, self._client.responses.create, endpoint="responses")

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
        if self._chat_extra_body:
            request["extra_body"] = dict(self._chat_extra_body)
        for key in ("temperature", "extra_headers", "timeout"):
            if key in kwargs:
                request[key] = kwargs[key]
        if reasoning := kwargs.get("reasoning"):
            request["reasoning_effort"] = reasoning["effort"]
        if "max_output_tokens" in kwargs:
            request[self._token_parameter] = kwargs["max_output_tokens"]
        try:
            completion = await self._chat_completion(request)
        except APIStatusError as error:
            if error.status_code not in {404, 405} or "model" in str(error.body).lower():
                raise
            return await self._responses_completion(kwargs)
        return chat_completion_response(completion)


def chat_completion_response(completion: ChatCompletion) -> Response:
    if len(completion.choices) != 1:
        raise ValueError("Chat request requires one completion choice")
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
        raise ValueError("Chat request did not reach a known completion state")
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
    parsed_usage = read_llm_usage(usage)
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
            # SDK 声明详情为必填整数，但兼容供应商可能省略；不能用零冒充未提供的数据。
            "usage": ResponseUsage.model_construct(
                input_tokens=usage.prompt_tokens,
                output_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
                input_tokens_details=InputTokensDetails.model_construct(
                    cached_tokens=parsed_usage.cached_input_tokens,
                    cache_write_tokens=parsed_usage.cache_write_tokens,
                ),
                output_tokens_details=OutputTokensDetails.model_construct(
                    reasoning_tokens=parsed_usage.reasoning_tokens,
                ),
            )
            if usage
            else None,
        },
    )
