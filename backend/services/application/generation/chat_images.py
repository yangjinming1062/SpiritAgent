"""聊天图片批次、实际验图与有界重做。"""

import asyncio
import json
from dataclasses import asdict, replace
from typing import Literal
from uuid import uuid4

from components import SESSION_LOCAL, get_logger, parse_llm_json, tool_error
from modules.companion import CharacterCardSnapshot
from prompts.generation import CHAT_IMAGE_CORRECTION_PREFIX
from prompts.tools import IMAGE_GENERATION_PARAM_DESCS, MEDIA_INSPECTION_INSTRUCTIONS
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from services.contracts import ImagePlan, MediaArtifact, MediaInspection, MediaTurnState
from services.domains.companion import character_snapshot_is_current, render_character_identity
from services.infrastructure.assets import asset_store, build_data_uri
from services.infrastructure.llm import VisualReasoningError, vision_chat

from .avatar_service import AvatarGenerationError
from .character_images import ImageChainState, generate_character_images
from .image_generation import ImageGenerationError, generate_images
from .media_chain import MEDIA_IDENTITY_ACCEPT_SCORE
from .visual_identity import (
    apply_outfit_override,
    build_self_image_prompt,
    load_self_visual_context,
    optional_outfit_image_reference,
)

logger = get_logger(__name__)


class ImageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    prompt: str = Field(min_length=1, max_length=16000, description=IMAGE_GENERATION_PARAM_DESCS["prompt"])
    subject: Literal["self"] | None = Field(default=None, description=IMAGE_GENERATION_PARAM_DESCS["subject"])
    size: Literal["1024x1024", "1024x1792", "1792x1024", "1:1", "16:9", "4:3", "3:2", "2:3", "3:4", "9:16", "21:9"] = (
        "1024x1024"
    )
    n: int = Field(default=1, ge=1, le=16, strict=True)
    outfit_override: str | None = Field(
        default=None,
        max_length=8000,
        description=IMAGE_GENERATION_PARAM_DESCS["outfit_override"],
    )


class ImageBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requests: list[ImageRequest] = Field(min_length=1, max_length=16)


class InspectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["pass", "revise"]
    issues: list[str] = Field(max_length=8)


async def _freeze_plan(request: ImageRequest, user_id: int) -> ImagePlan:
    if request.subject != "self":
        return ImagePlan(request=request.prompt, prompt=request.prompt, size=request.size)
    visual = await load_self_visual_context(user_id)
    plan = apply_outfit_override(visual, request.outfit_override)
    outfit = await optional_outfit_image_reference(plan, user_id)
    return ImagePlan(
        request=request.model_dump_json(),
        prompt=build_self_image_prompt(plan, request.prompt, has_outfit_reference=bool(outfit)),
        size=request.size,
        reference_image=visual.reference_image,
        secondary_reference_image=outfit,
        identity_json=visual.identity.model_dump_json(),
    )


async def _generate_batch(
    plan: ImagePlan,
    user_id: int,
    directory: str,
    n: int,
) -> list[tuple[str | None, int | None, str | None]]:
    """为同 request 的 n 张产出 per-slot (url, score, error)；硬失败整体抛出，由调用方按 request 落状态。"""
    results: list[tuple[str | None, int | None, str | None]] = [(None, None, None)] * n

    async def request(count: int) -> list[str]:
        return await generate_images(
            plan.prompt,
            size=plan.size,
            n=count,
            user_id=user_id,
            persist_user_assets=True,
            storage_directory=directory,
        )

    identity = CharacterCardSnapshot.model_validate_json(plan.identity_json) if plan.identity_json else None
    if identity is None:
        urls = await request(n)
        filled = min(len(urls), n)
        results[:filled] = [(url, None, None) for url in urls[:filled]]
        # 供应商单次出图上限不同可能少给（gemini 忽略 n 等）；缺额先批量补齐，批量不可用再逐张兜底。
        index = filled
        while index < n:
            try:
                retry = await request(n - index)
            except ImageGenerationError:
                retry = []
            got = min(len(retry), n - index)
            results[index : index + got] = [(url, None, None) for url in retry[:got]]
            index += got
            if got == 0:
                for slot in range(index, n):
                    try:
                        single = await request(1)
                    except ImageGenerationError as exc:
                        results[slot] = (None, None, str(exc))
                        continue
                    results[slot] = (single[0], None, None)
                break
        return results
    async with SESSION_LOCAL() as db:
        if not await character_snapshot_is_current(db, user_id, identity):
            raise ImageGenerationError("角色外形已更新，请使用当前形象重新提出生成请求")
    state = ImageChainState()
    await generate_character_images(
        plan.prompt,
        size=plan.size,
        n=n,
        user_id=user_id,
        storage_directory=directory,
        reference_image=plan.reference_image or "",
        secondary_reference_image=plan.secondary_reference_image,
        identity_reference=plan.reference_image or "",
        identity_text=render_character_identity(identity),
        state=state,
    )
    delivered: list[str] = []
    for slot in range(n):
        best = state.best(slot)
        if best is None:
            results[slot] = (None, None, "图片生成失败，未取得可用候选")
            continue
        results[slot] = (best.path, best.score, None)
        delivered.append(best.path)
    async with SESSION_LOCAL() as db:
        current = await character_snapshot_is_current(db, user_id, identity)
    if not current:
        for url in delivered:
            await asyncio.to_thread(asset_store.unlink_companion_asset, url)
        raise ImageGenerationError("生成期间角色外形已更新，本轮图片未交付")
    return results


async def _generate(plan: ImagePlan, user_id: int, directory: str) -> tuple[str, int | None]:
    url, score, error = (await _generate_batch(plan, user_id, directory, 1))[0]
    if url is None:
        raise ImageGenerationError(error or "图片生成服务没有返回图片")
    return url, score


def _artifact_failure(exc: BaseException) -> tuple[str, str]:
    """异常 → (状态, 文案)：初次合批与单张重做共用同一映射契约。"""
    if isinstance(exc, asyncio.CancelledError):
        return "result_unknown", "生成已取消，结果尚未核实；不要重复提交"
    if isinstance(exc, ImageGenerationError):
        return ("result_unknown" if exc.result_unknown else "failed"), str(exc)
    return "result_unknown", "生成结果尚未核实，请勿重复提交"


def _fail_all(artifacts: list[MediaArtifact], status: str, error: str) -> None:
    for artifact in artifacts:
        artifact.status, artifact.error = status, error


async def _produce(state: MediaTurnState, artifact: MediaArtifact, plan: ImagePlan) -> None:
    try:
        artifact.url, artifact.identity_score = await _generate(plan, state.user_id, state.asset_directory)
        artifact.status = "ready"
    except asyncio.CancelledError as exc:
        artifact.status, artifact.error = _artifact_failure(exc)
        raise
    except ImageGenerationError as exc:
        artifact.status, artifact.error = _artifact_failure(exc)
    except Exception as exc:
        artifact.status, artifact.error = _artifact_failure(exc)
        raise


def _batch_result(state: MediaTurnState, *, reused: bool = False) -> str:
    return json.dumps({"success": True, "reused": reused, "media": state.image_results()}, ensure_ascii=False)


async def generate_chat_images(requests: list[dict], state: MediaTurnState) -> str:
    try:
        batch = ImageBatch.model_validate({"requests": requests})
        if sum(item.n for item in batch.requests) > 16:
            return tool_error("每轮初次生成最多 16 张图片，请缩小生成清单")
    except ValidationError:
        return tool_error("图片请求必须是非空 requests 数组，每项提供 prompt 和有效的生成参数")
    async with state.lock:
        if state.image_batch_claimed:
            return _batch_result(state, reused=True)
        state.image_batch_claimed = True
        # 先登记整个清单，第二个并行调用只能查询这批产物。
        entries: list[tuple[ImageRequest, list[MediaArtifact]]] = []
        for request in batch.requests:
            artifacts = []
            for _ in range(request.n):
                media_id = uuid4().hex
                artifact = MediaArtifact(media_id, "image", media_id, "pending")
                state.artifacts[media_id] = artifact
                state.current_versions[media_id] = media_id
                artifacts.append(artifact)
            entries.append((request, artifacts))
    try:
        for request, artifacts in entries:
            try:
                plan = await _freeze_plan(request, state.user_id)
            except (AvatarGenerationError, VisualReasoningError, ImageGenerationError) as exc:
                _fail_all(artifacts, "failed", str(exc))
                continue
            for artifact in artifacts:
                state.plans[artifact.goal_id] = plan
            # 合批后一次失败波及本 request 全部图片，硬失败不再逐张隔离。
            try:
                results = await _generate_batch(plan, state.user_id, state.asset_directory, len(artifacts))
            except asyncio.CancelledError as exc:
                _fail_all(artifacts, *_artifact_failure(exc))
                raise
            except ImageGenerationError as exc:
                _fail_all(artifacts, *_artifact_failure(exc))
                continue
            except Exception as exc:
                _fail_all(artifacts, *_artifact_failure(exc))
                raise
            for artifact, (url, score, error) in zip(artifacts, results):
                if url is None or error is not None:
                    artifact.status, artifact.error = "failed", error or "图片生成服务没有返回图片"
                    continue
                artifact.url, artifact.identity_score = url, score
                artifact.status = "ready"
                state.required_goals.add(artifact.goal_id)
    finally:
        for _, artifacts in entries:
            for artifact in artifacts:
                if artifact.status == "pending":
                    artifact.status, artifact.error = "failed", "生成清单已中断，此项未执行"
    return _batch_result(state)


async def inspect_chat_image(media_id: str, state: MediaTurnState) -> str:
    async with state.lock:
        artifact = state.artifacts.get(media_id)
        if artifact is None or artifact.type != "image" or artifact.status != "ready" or not artifact.url:
            return tool_error("只能检查当前会话中已就绪的图片")
        prior = state.inspections.get(media_id)
        if prior is not None:
            return json.dumps(asdict(prior), ensure_ascii=False)
        parsed = asset_store.parse_companion_asset_path(artifact.url)
        local = asset_store.resolve_companion_asset_path(*parsed) if parsed and parsed[0] == state.user_id else None
        if local is None:
            return tool_error("图片资产不可访问")
        plan = state.plans.get(artifact.goal_id)
        try:
            data = await asyncio.to_thread(local[0].read_bytes)
            uri = await asyncio.to_thread(build_data_uri, data, local[1])
            raw = await vision_chat(
                state.user_id,
                MEDIA_INSPECTION_INSTRUCTIONS.format(accept_score=MEDIA_IDENTITY_ACCEPT_SCORE),
                json.dumps(
                    {
                        "user_request": state.original_request,
                        "image_request": plan.request if plan else "",
                        "identity_score": artifact.identity_score,
                    },
                    ensure_ascii=False,
                ),
                reference_images=(uri,),
            )
            result = InspectionResult.model_validate(parse_llm_json(raw))
            issues = tuple(issue.strip()[:1000] for issue in result.issues if issue.strip())
            verdict = result.verdict if result.verdict == "pass" or issues else "unavailable"
        except Exception:
            logger.warning(
                "Image inspection unavailable",
                extra={"media_id": media_id, "user_id": state.user_id},
                exc_info=True,
            )
            verdict, issues = "unavailable", ("未取得有效验图结果，不据此重新生成",)
        inspection = MediaInspection(uuid4().hex, media_id, verdict, issues)
        state.inspections[media_id] = inspection
        return json.dumps(asdict(inspection), ensure_ascii=False)


async def regenerate_chat_image(media_id: str, inspection_id: str, correction: str, state: MediaTurnState) -> str:
    async with state.lock:
        artifact = state.artifacts.get(media_id)
        inspection = state.inspections.get(media_id)
        if artifact is None or artifact.type != "image" or artifact.status != "ready":
            return tool_error("重做必须引用已成功生成的图片")
        if not inspection or inspection.inspection_id != inspection_id or inspection.verdict != "revise":
            return tool_error("需要实际验图发现具体问题后才能重做")
        if state.current_versions.get(artifact.goal_id) != media_id or artifact.goal_id not in state.plans:
            return tool_error("验图版本已过期，或图片不是本轮生成的产物")
        if artifact.goal_id in state.regenerated_goals:
            return tool_error("该图片本轮的一次重做额度已用完，请从已有版本中选择")
        if not correction.strip():
            return tool_error("请说明针对验图问题的修改要求")
        state.regenerated_goals.add(artifact.goal_id)
        original = state.plans[artifact.goal_id]
        plan = replace(
            original,
            prompt=original.prompt
            + CHAT_IMAGE_CORRECTION_PREFIX
            + json.dumps({"verified_issues": inspection.issues, "correction": correction[:8000]}, ensure_ascii=False),
        )
        revised = MediaArtifact(uuid4().hex, "image", artifact.goal_id, "pending")
        state.artifacts[revised.media_id] = revised
        state.current_versions[artifact.goal_id] = revised.media_id
    await _produce(state, revised, plan)
    return json.dumps(
        {
            "success": revised.status == "ready",
            "media": [artifact.tool_view(), revised.tool_view()],
            "select_one": True,
        },
        ensure_ascii=False,
    )
