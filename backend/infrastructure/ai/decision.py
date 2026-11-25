"""System One 决策接入。每个问题只消费通过校验且分差足够的候选。"""

import asyncio
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import zip_longest
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
DECISION_OPTION_LIMIT = 64
_MAX_RESPONSE_BYTES = 16 * 1024
_SUGGESTION_WAIT_SECONDS = 4.0
_TIMEOUT = httpx.Timeout(None, connect=2.0, pool=2.0)
_in_flight: asyncio.Task[dict[str, tuple[str, ...]]] | None = None
_service_generation = 0


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
    abstain_key: str | None = None


class _Answer(BaseModel):
    type: Literal["choice"]
    probabilities: dict[str, Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]]
    choice: str | None = None


class _Response(BaseModel):
    answers: dict[str, _Answer]


def decision_enabled() -> bool:
    return bool(SETTINGS.decision_base_url)


def decision_service_revision() -> int:
    return _service_generation


async def cancel_decision_request() -> None:
    """停机或替换服务设置时，取消并收尾当前请求。"""
    global _service_generation
    _service_generation += 1
    await cancel_and_wait((_in_flight,))


def _validated_ranking(answer: _Answer, question: DecisionQuestion) -> tuple[str, ...] | None:
    keys = [option.key for option in question.options]
    probabilities = answer.probabilities
    if probabilities.keys() != set(keys) or not math.isclose(math.fsum(probabilities.values()), 1, abs_tol=0.01):
        raise ValueError("Invalid decision probabilities")
    if question.abstain_key is not None and question.abstain_key not in probabilities:
        raise ValueError("Unknown decision abstention option")
    ranking = tuple(sorted(keys, key=probabilities.__getitem__, reverse=True))
    best, runner_up = ranking[:2]
    probability = probabilities[best]
    if answer.choice is not None and probabilities.get(answer.choice) != probability:
        raise ValueError("Decision label disagrees with probabilities")
    if probability < question.min_probability or probability - probabilities[runner_up] < question.min_margin:
        return None
    if question.abstain_key is None:
        return ranking
    cutoff = probabilities[question.abstain_key]
    return tuple(key for key in ranking if probabilities[key] > cutoff)


def _validated_rankings(body: bytes, questions: Mapping[str, DecisionQuestion]) -> dict[str, tuple[str, ...]]:
    answers = _Response.model_validate_json(body).answers
    if answers.keys() != questions.keys():
        raise ValueError("Unexpected decision answer IDs")
    return {
        name: ranking
        for name, question in questions.items()
        if (ranking := _validated_ranking(answers[name], question)) is not None
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


def fit_decision_state(
    state: Mapping[str, JsonValue],
    groups: Mapping[str, Sequence[JsonValue]],
    *,
    questions: Mapping[str, DecisionQuestion],
) -> dict[str, JsonValue]:
    """按实际请求预算交替装入各组资料，组内按调用方顺序优先。"""
    selected: dict[str, list[JsonValue]] = {name: [] for name in groups}
    payload: dict[str, JsonValue] = {**state, **selected}
    missing = object()
    for items in zip_longest(*groups.values(), fillvalue=missing):
        for name, item in zip(groups, items, strict=True):
            if item is missing:
                continue
            selected[name].append(item)
            if not decision_input_fits(payload, questions=questions):
                selected[name].pop()
    return payload


async def choose_decision(
    task: str,
    *,
    state: JsonValue,
    instructions: str,
    options: Sequence[DecisionOption],
    min_probability: float = 0.75,
    min_margin: float = 0.15,
    groups: Mapping[str, Sequence[JsonValue]] | None = None,
) -> str | None:
    questions = {"decision": DecisionQuestion(instructions, options, min_probability, min_margin)}
    if groups:
        if not decision_enabled():
            return None
        if not isinstance(state, dict):
            raise TypeError("Grouped decision input requires an object state")
        state = fit_decision_state(state, groups, questions=questions)
    answers = await choose_decisions(
        task,
        state=state,
        questions=questions,
    )
    return answers.get("decision")


async def choose_decisions(
    task: str,
    *,
    state: JsonValue,
    questions: Mapping[str, DecisionQuestion],
    wait_seconds: float = _SUGGESTION_WAIT_SECONDS,
) -> dict[str, str]:
    rankings = await _choose_rankings(task, state=state, questions=questions, wait_seconds=wait_seconds)
    return {name: ranking[0] for name, ranking in rankings.items() if ranking}


async def rank_decision_options(
    task: str,
    *,
    state: JsonValue,
    instructions: str,
    options: Sequence[DecisionOption],
    min_probability: float = 0.75,
    min_margin: float = 0.15,
    abstain_key: str | None = None,
    wait_seconds: float = _SUGGESTION_WAIT_SECONDS,
) -> tuple[str, ...] | None:
    """首选达到置信门槛时按分数排序，同分保留输入顺序。

    指定弃选项时只返回严格高于弃选项的候选。空元组表示成功判断为弃选，None 表示未达到门槛或服务无结果。
    """
    rankings = await _choose_rankings(
        task,
        state=state,
        questions={"decision": DecisionQuestion(instructions, options, min_probability, min_margin, abstain_key)},
        wait_seconds=wait_seconds,
    )
    return rankings.get("decision")


async def _choose_rankings(
    task: str,
    *,
    state: JsonValue,
    questions: Mapping[str, DecisionQuestion],
    wait_seconds: float,
) -> dict[str, tuple[str, ...]]:
    """共享串行推理槽。等待槽位和结果共用调用方的时间窗口。"""
    global _in_flight
    base_url = SETTINGS.decision_base_url
    if not base_url:
        return {}
    generation = _service_generation
    questions = dict(questions)
    if not questions or any(
        not 2 <= len({option.key for option in question.options}) == len(question.options) <= DECISION_OPTION_LIMIT
        for question in questions.values()
    ):
        return {}
    payload = _payload(state, questions)
    if not _fits_input(payload):
        logger.info("Decision model input exceeds budget", extra={"decision_task": task})
        return {}

    deadline = monotonic() + wait_seconds
    while (running := _in_flight) is not None and not running.done():
        remaining = deadline - monotonic()
        if remaining <= 0:
            return {}
        completed, _ = await asyncio.wait((running,), timeout=remaining)
        if not completed:
            logger.info("Decision model busy within caller window", extra={"decision_task": task})
            return {}
    remaining = deadline - monotonic()
    if generation != _service_generation or remaining <= 0:
        return {}

    headers = {"Authorization": f"Bearer {SETTINGS.decision_api_key}"} if SETTINGS.decision_api_key else {}
    request = asyncio.create_task(
        _request_decision(task, base_url, headers, payload, questions),
        name=f"decision.{task}",
    )
    _in_flight = request
    request.add_done_callback(_decision_finished)
    try:
        completed, _ = await asyncio.wait((request,), timeout=remaining)
    except asyncio.CancelledError:
        await cancel_and_wait((request,))
        raise
    if not completed:
        await cancel_and_wait((request,))
        logger.info("Decision suggestion unavailable within caller window", extra={"decision_task": task})
        return {}
    if generation != _service_generation or request.cancelled() or request.exception() is not None:
        return {}
    return request.result()


def _decision_finished(request: asyncio.Task[dict[str, tuple[str, ...]]]) -> None:
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
) -> dict[str, tuple[str, ...]]:
    started = monotonic()
    try:
        async with trusted_service_async_client(base_url, timeout=_TIMEOUT, trust_env=False, headers=headers) as client:
            endpoint = "systemone" if client.base_url.raw_path.endswith(b"/v1/") else "v1/systemone"
            async with client.stream("POST", endpoint, json=payload) as response:
                response.raise_for_status()
                body = await read_capped_response(
                    response, max_bytes=_MAX_RESPONSE_BYTES, too_large=ValueError("Decision response exceeds limit")
                )
        rankings = _validated_rankings(body, questions)
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
            "accepted": bool(rankings),
            "duration_ms": round((monotonic() - started) * 1000),
        },
    )
    return rankings
