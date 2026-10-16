"""MiMo 音视频 Chat Completions 请求与统一 Responses 消费契约的转换。"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal, Self

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion
from openai.types.completion_usage import CompletionUsage
from openai.types.responses import Response, ResponseOutputItem

from infrastructure.ai.providers._companion_format import chat_completion_response


@dataclass(frozen=True)
class AudioResponseEvent:
    type: Literal[
        "response.created",
        "response.output_text.delta",
        "response.reasoning_text.delta",
        "response.function_call_arguments.delta",
        "response.refusal.delta",
        "response.output_item.done",
        "response.completed",
        "response.incomplete",
    ]
    delta: str | None = None
    item: ResponseOutputItem | None = None
    response: Response | None = None


def _parts(parts: list[dict]) -> list[dict]:
    converted = []
    for part in parts:
        kind = part.get("type")
        if kind in {"input_text", "output_text"}:
            converted.append({"type": "text", "text": part.get("text", "")})
        elif kind == "input_image":
            converted.append({"type": "image_url", "image_url": {"url": part["image_url"]}})
        elif kind == "input_video":
            converted.append({"type": "video_url", "video_url": {"url": part["video_url"]}})
        elif kind == "input_audio":
            converted.append(
                {
                    "type": "input_audio",
                    "input_audio": {
                        "data": part.get("data") or part.get("audio_url"),
                        "format": part.get("format", "wav"),
                    },
                },
            )
        else:
            raise ValueError("音频调用包含未支持的消息部件")
    return converted


def audio_chat_request(kwargs: dict[str, Any]) -> dict[str, Any]:
    messages: list[dict] = []
    if kwargs.get("instructions"):
        messages.append({"role": "system", "content": kwargs["instructions"]})
    inputs = kwargs.get("input")
    if isinstance(inputs, str):
        inputs = [{"role": "user", "content": inputs}]
    reasoning = ""
    for item in inputs or []:
        kind = item.get("type")
        if kind == "reasoning":
            reasoning += "".join(
                part.get("text", "")
                for key in ("content", "summary")
                for part in item.get(key, [])
                if isinstance(part, dict)
            )
        elif kind == "function_call":
            call = {
                "id": item["call_id"],
                "type": "function",
                "function": {"name": item["name"], "arguments": item["arguments"]},
            }
            if messages and messages[-1].get("tool_calls"):
                messages[-1]["tool_calls"].append(call)
            else:
                messages.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [call],
                        **({"reasoning_content": reasoning} if reasoning else {}),
                    },
                )
            reasoning = ""
        elif kind == "function_call_output":
            output = item.get("output", "")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": item["call_id"],
                    "content": _parts(output)
                    if isinstance(output, list)
                    else output
                    if isinstance(output, str)
                    else json.dumps(output, ensure_ascii=False),
                },
            )
        else:
            content = item.get("content", "")
            parts = _parts(content) if isinstance(content, list) else content
            if item.get("role") == "assistant" and isinstance(parts, list):
                parts = "\n".join(part["text"] for part in parts if part["type"] == "text")
            messages.append({"role": item.get("role", "user"), "content": parts})
    request: dict[str, Any] = {
        "model": kwargs["model"],
        "messages": messages,
        "max_completion_tokens": kwargs["max_output_tokens"],
        "stream": bool(kwargs.get("stream")),
        "store": False,
    }
    for key in ("temperature", "timeout", "extra_headers"):
        if key in kwargs:
            request[key] = kwargs[key]
    if request.get("extra_headers", {}).get("Idempotency-Key"):
        request["extra_headers"] = {
            **request["extra_headers"],
            "Idempotency-Key": request["extra_headers"]["Idempotency-Key"] + ":audio-chat",
        }
    if options := kwargs.get("reasoning"):
        request["reasoning_effort"] = options["effort"]
    if tools := kwargs.get("tools"):
        request["tools"] = [
            {"type": "function", "function": {key: value for key, value in tool.items() if key != "type"}}
            for tool in tools
        ]
    if kwargs.get("tool_choice") is not None:
        request["tool_choice"] = kwargs["tool_choice"]
    if text_format := kwargs.get("text", {}).get("format"):
        request["response_format"] = (
            {"type": "json_schema", "json_schema": {key: value for key, value in text_format.items() if key != "type"}}
            if text_format["type"] == "json_schema"
            else {"type": text_format["type"]}
        )
    if request["stream"]:
        request["stream_options"] = {"include_usage": True}
    return request


class AudioChatStream:
    def __init__(self, stream: Any, model: str) -> None:
        self._stream = stream
        self._model = model
        self._iterator = self._events()
        self.usage: CompletionUsage | None = None

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> AudioResponseEvent:
        return await self._iterator.__anext__()

    async def close(self) -> None:
        await self._iterator.aclose()

    async def _events(self) -> AsyncIterator[AudioResponseEvent]:
        text = ""
        reasoning = ""
        refusal = ""
        calls: dict[int, dict] = {}
        response_id = ""
        created = 0
        usage = None
        finish = None
        first = True
        try:
            async for chunk in self._stream:
                response_id = chunk.id or response_id
                created = chunk.created or created
                usage = chunk.usage or usage
                self.usage = usage
                if first:
                    first = False
                    yield AudioResponseEvent("response.created")
                for choice in chunk.choices:
                    if choice.index != 0:
                        raise ValueError("音频请求返回了多个响应")
                    delta = choice.delta
                    if delta.content:
                        text += delta.content
                        yield AudioResponseEvent("response.output_text.delta", delta=delta.content)
                    if value := getattr(delta, "reasoning_content", None):
                        reasoning += value
                        yield AudioResponseEvent("response.reasoning_text.delta", delta=value)
                    if delta.refusal:
                        refusal += delta.refusal
                        yield AudioResponseEvent("response.refusal.delta", delta=delta.refusal)
                    for call in delta.tool_calls or []:
                        current = calls.setdefault(
                            call.index,
                            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                        )
                        if call.id:
                            current["id"] = call.id
                        if call.function:
                            current["function"]["name"] += call.function.name or ""
                            current["function"]["arguments"] += call.function.arguments or ""
                        yield AudioResponseEvent(
                            "response.function_call_arguments.delta",
                            delta=call.function.arguments if call.function else "",
                        )
                    finish = choice.finish_reason or finish
            if finish is None:
                raise RuntimeError("音频流在完成状态之前结束")
            completion = ChatCompletion.model_validate(
                {
                    "id": response_id,
                    "created": created,
                    "model": self._model,
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": finish,
                            "message": {
                                "role": "assistant",
                                "content": text or None,
                                "reasoning_content": reasoning,
                                "refusal": refusal or None,
                                "tool_calls": [calls[index] for index in sorted(calls)] or None,
                            },
                        },
                    ],
                    "usage": usage.model_dump() if usage is not None else None,
                },
            )
            response = chat_completion_response(completion)
            for item in response.output:
                yield AudioResponseEvent("response.output_item.done", item=item)
            yield AudioResponseEvent(
                "response.completed" if response.status == "completed" else "response.incomplete",
                response=response,
            )
        finally:
            await self._stream.close()


class AudioChatResponsesClient:
    def __init__(self, client: AsyncOpenAI) -> None:
        self._client = client
        self.base_url = client.base_url
        self.responses = self

    async def create(self, **kwargs: Any) -> Response | AudioChatStream:
        result = await self._client.chat.completions.create(**audio_chat_request(kwargs))
        if kwargs.get("stream"):
            return AudioChatStream(result, kwargs["model"])
        return chat_completion_response(result)
