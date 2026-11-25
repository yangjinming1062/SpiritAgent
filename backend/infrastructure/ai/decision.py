"""System One 决策接入。每个问题只消费通过校验且分差足够的候选。"""

import asyncio
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from time import monotonic
from typing import Annotated, Literal

import httpx
from pydantic import BaseModel, Field, JsonValue

from infrastructure.async_io import cancel_and_wait
from infrastructure.config import SETTINGS
from infrastructure.logger import get_logger
from infrastructure.network import read_capped_response, trusted_service_async_client
from infrastructure.redact import redact_sensitive_data
from infrastructure.text import approx_text_tokens

logger = get_logger(__name__)
_MAX_INPUT_TOKENS = 1600
_MAX_OPTIONS = 16
_MAX_RESPONSE_BYTES = 16 * 1024
_SUGGESTION_WAIT_SECONDS = 2.0
_TIMEOUT = httpx.Timeout(None, connect=2.0, pool=2.0)
_in_flight: asyncio.Task[dict[str, str]] | None = None


@dataclass(frozen=True, slots=True)
class DecisionOption:
    key: str
    description: str


@dataclass(frozen=True, slots=True)
class DecisionQuestion:
    instructions: str
    options: Sequence[DecisionOption]
    min_probability: float = 0.75
    min_margin: float = 0.15


class _Answer(BaseModel):
    type: Literal["choice"]
    probabilities: dict[str, Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]]
    choice: str | None = None


class _Response(BaseModel):
    answers: dict[str, _Answer]


def decision_enabled() -> bool:
    return bool(SETTINGS.decision_base_url)


async def cancel_decision_request() -> None:
    """停机或替换服务设置时，取消并收尾当前请求。"""
    await cancel_and_wait((_in_flight,))


def _validated_choice(answer: _Answer, question: DecisionQuestion) -> str | None:
    keys = {option.key for option in question.options}
    if answer.probabilities.keys() != keys or not math.isclose(sum(answer.probabilities.values()), 1, abs_tol=0.01):
        raise ValueError("Invalid decision probabilities")
    best, runner_up = sorted(answer.probabilities, key=answer.probabilities.__getitem__, reverse=True)[:2]
    probability = answer.probabilities[best]
    if answer.choice is not None and answer.choice != best:
        raise ValueError("Decision label disagrees with probabilities")
    if probability < question.min_probability or probability - answer.probabilities[runner_up] < question.min_margin:
        return None
    return best


def _validated_choices(body: bytes, questions: Mapping[str, DecisionQuestion]) -> dict[str, str]:
    answers = _Response.model_validate_json(body).answers
    if answers.keys() != questions.keys():
        raise ValueError("Unexpected decision answer IDs")
    return {
        name: choice
        for name, question in questions.items()
        if (choice := _validated_choice(answers[name], question)) is not None
    }


def _payload(state: JsonValue, questions: Mapping[str, DecisionQuestion]) -> dict[str, JsonValue]:
    return {
        "state": {
            "messages": [{"role": "user", "content": json.dumps(redact_sensitive_data(state), ensure_ascii=False)}]
        },
        "questions": {
            name: {
                "type": "choice",
                "instructions": question.instructions,
                "criteria": redact_sensitive_data({option.key: option.description for option in question.options}),
            }
            for name, question in questions.items()
        },
    }


def _fits_input(payload: dict[str, JsonValue], max_tokens: int = _MAX_INPUT_TOKENS) -> bool:
    return approx_text_tokens(json.dumps(payload, ensure_ascii=False)) <= min(max_tokens, _MAX_INPUT_TOKENS)


def decision_input_fits(
    state: JsonValue, *, questions: Mapping[str, DecisionQuestion], max_tokens: int = _MAX_INPUT_TOKENS
) -> bool:
    """用实际请求格式计量输入，供长资料分批。"""
    return _fits_input(_payload(state, questions), max_tokens)


async def choose_decision(
    task: str,
    *,
    state: JsonValue,
    instructions: str,
    options: Sequence[DecisionOption],
    min_probability: float = 0.75,
    min_margin: float = 0.15,
) -> str | None:
    answers = await choose_decisions(
        task,
        state=state,
        questions={"decision": DecisionQuestion(instructions, options, min_probability, min_margin)},
    )
    return answers.get("decision")


async def choose_decisions(
    task: str,
    *,
    state: JsonValue,
    questions: Mapping[str, DecisionQuestion],
    wait_seconds: float = _SUGGESTION_WAIT_SECONDS,
) -> dict[str, str]:
    """可选判断不排队，调用方可在推理仍在途时按原流程继续。"""
    global _in_flight
    base_url = SETTINGS.decision_base_url
    if not base_url or (_in_flight is not None and not _in_flight.done()):
        return {}
    questions = dict(questions)
    if not questions or any(
        not 2 <= len({option.key for option in question.options}) == len(question.options) <= _MAX_OPTIONS
        for question in questions.values()
    ):
        return {}
    payload = _payload(state, questions)
    if not _fits_input(payload):
        logger.info("Decision model input exceeds budget", extra={"decision_task": task})
        return {}

    headers = {"Authorization": f"Bearer {SETTINGS.decision_api_key}"} if SETTINGS.decision_api_key else {}
    request = asyncio.create_task(
        _request_decision(task, base_url, headers, payload, questions),
        name=f"decision.{task}",
    )
    _in_flight = request
    request.add_done_callback(_decision_finished)
    try:
        completed, _ = await asyncio.wait((request,), timeout=wait_seconds)
    except asyncio.CancelledError:
        await cancel_and_wait((request,))
        raise
    if not completed:
        logger.info("Decision suggestion unavailable within caller window", extra={"decision_task": task})
        return {}
    if request.cancelled() or request.exception() is not None:
        return {}
    return request.result()


def _decision_finished(request: asyncio.Task[dict[str, str]]) -> None:
    global _in_flight
    if _in_flight is request:
        _in_flight = None
    if not request.cancelled() and (error := request.exception()) is not None:
        logger.error("Decision model task failed", extra={"error_type": type(error).__name__})


async def _request_decision(
    task: str,
    base_url: str,
    headers: dict[str, str],
    payload: dict[str, JsonValue],
    questions: Mapping[str, DecisionQuestion],
) -> dict[str, str]:
    started = monotonic()
    try:
        async with trusted_service_async_client(base_url, timeout=_TIMEOUT, trust_env=False, headers=headers) as client:
            endpoint = "systemone" if client.base_url.raw_path.endswith(b"/v1/") else "v1/systemone"
            async with client.stream("POST", endpoint, json=payload) as response:
                response.raise_for_status()
                body = await read_capped_response(
                    response, max_bytes=_MAX_RESPONSE_BYTES, too_large=ValueError("Decision response exceeds limit")
                )
        choices = _validated_choices(body, questions)
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Decision model rejected request", extra={"decision_task": task, "status_code": exc.response.status_code}
        )
        return {}
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
        logger.warning("Decision model unavailable", extra={"decision_task": task, "error_type": type(exc).__name__})
        return {}
    logger.info(
        "Decision model request completed",
        extra={
            "decision_task": task,
            "accepted": bool(choices),
            "duration_ms": round((monotonic() - started) * 1000),
        },
    )
    return choices
