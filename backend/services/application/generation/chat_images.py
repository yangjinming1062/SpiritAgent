"""聊天图片请求、实际验图与有界重做。"""

import asyncio
import json
from dataclasses import asdict, replace
from typing import Literal, get_args
from uuid import uuid4

from components import SESSION_LOCAL, get_logger, parse_llm_json, tool_error
from modules.companion import CharacterCardSnapshot
from prompts.generation import CHAT_IMAGE_CORRECTION_PREFIX
from prompts.tools import MEDIA_INSPECTION_INSTRUCTIONS
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

# 每回合初次生成的图片总预算（含失败项）；单次调用只描述一个画面，n 为该画面的张数。
CHAT_IMAGES_PER_TURN = 16
ImageAspectRatio = Literal["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9"]
IMAGE_ASPECT_RATIOS: tuple[str, ...] = get_args(ImageAspectRatio)


class ImageRequest(BaseModel):
    """一次 image_generate 调用：同一描述的 n 张图片，字段说明在工具 schema。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    prompt: str = Field(min_length=1, max_length=16000)
    subject: Literal["self"] | None = None
    aspect_ratio: ImageAspectRatio = "1:1"
    n: int = Field(default=1, ge=1, le=CHAT_IMAGES_PER_TURN, strict=True)
    outfit_override: str | None = Field(default=None, max_length=8000)


class InspectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["pass", "revise"]
    issues: list[str] = Field(max_length=8)


async def _freeze_plan(request: ImageRequest, user_id: int) -> ImagePlan:
    if request.subject != "self":
        return ImagePlan(request=request.prompt, prompt=request.prompt, size=request.aspect_ratio)
    visual = await load_self_visual_context(user_id)
    plan = apply_outfit_override(visual, request.outfit_override)
    outfit = await optional_outfit_image_reference(plan, user_id)
    return ImagePlan(
        request=request.model_dump_json(),
        prompt=build_self_image_prompt(plan, request.prompt, has_outfit_reference=bool(outfit)),
        size=request.aspect_ratio,
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


def _delivery_hint(state: MediaTurnState) -> str:
    """图片就绪后的下一步：交付由最终回复完成，模型不必再调用工具。"""
    if state.structured_reply:
        return (
            "Ready images are delivered in your final reply: put each ready media_id in an image bubble. "
            "The reply is not a tool call, so no further tool call is needed."
        )
    return "Ready images are attached to your reply automatically; describe the result briefly."


def _call_result(state: MediaTurnState, media_ids: list[str], *, reused: bool = False) -> str:
    """本次调用的图片结果；一张都没有就绪时按失败返回，保持顶层 error 供模型与循环守卫识别。"""
    media = [state.artifacts[media_id].tool_view() for media_id in media_ids]
    ready = any(item["status"] == "ready" for item in media)
    payload: dict[str, object] = {"success": ready, "media": media}
    if reused:
        payload["reused"] = True
    if ready:
        payload["next"] = _delivery_hint(state)
    else:
        payload["error"] = next((item["error"] for item in media if item.get("error")), "图片生成失败")
    return json.dumps(payload, ensure_ascii=False)


def _validation_message(exc: ValidationError) -> str:
    return "图片请求参数无效：" + "；".join(
        f"{'.'.join(str(part) for part in error['loc'])}：{error['msg']}"
        for error in exc.errors(include_input=False, include_url=False, include_context=False)
    )


async def generate_chat_images(arguments: dict, state: MediaTurnState) -> str:
    """生成同一描述的 n 张图片。图片请求只在回合的一个工具步里提交（同一步可分别描述多个画面），总量受回合预算约束；完全相同的请求只返回已有结果。"""
    try:
        request = ImageRequest.model_validate(arguments)
    except ValidationError as exc:
        return tool_error(_validation_message(exc))
    if request.outfit_override and request.subject != "self":
        return tool_error("outfit_override 仅用于 subject='self' 的本人出镜图片")
    key = request.model_dump_json()
    async with state.lock:
        if (media_ids := state.image_requests.get(key)) is not None:
            return _call_result(state, media_ids, reused=True)
        if state.image_round is not None and state.image_round != state.tool_round:
            return tool_error(
                "本回合的图片请求已经提交，不再接受新的图片请求；需要多幅不同的画面须在同一步里一起提交，或下一回合再提出",
            )
        if any(a.type == "image" and a.status == "result_unknown" for a in state.artifacts.values()):
            return tool_error("前一次图片生成的结果尚未核实，本轮不再提交新的图片生成")
        if state.image_budget_used + request.n > CHAT_IMAGES_PER_TURN:
            return tool_error(
                f"每轮最多生成 {CHAT_IMAGES_PER_TURN} 张图片，本轮已请求 {state.image_budget_used} 张，请减少张数",
            )
        state.image_budget_used += request.n
        state.image_round = state.tool_round
        state.mark_media_accepted()
        artifacts: list[MediaArtifact] = []
        for _ in range(request.n):
            media_id = uuid4().hex
            artifact = MediaArtifact(media_id, "image", media_id, "pending")
            state.artifacts[media_id] = artifact
            state.current_versions[media_id] = media_id
            artifacts.append(artifact)
        media_ids = [artifact.media_id for artifact in artifacts]
        state.image_requests[key] = media_ids
    try:
        try:
            plan = await _freeze_plan(request, state.user_id)
        except (AvatarGenerationError, VisualReasoningError, ImageGenerationError) as exc:
            _fail_all(artifacts, "failed", str(exc))
            return _call_result(state, media_ids)
        for artifact in artifacts:
            state.plans[artifact.goal_id] = plan
        # 合批后一次失败波及这次调用的全部图片，硬失败不再逐张隔离。
        try:
            results = await _generate_batch(plan, state.user_id, state.asset_directory, len(artifacts))
        except asyncio.CancelledError as exc:
            _fail_all(artifacts, *_artifact_failure(exc))
            raise
        except ImageGenerationError as exc:
            _fail_all(artifacts, *_artifact_failure(exc))
            return _call_result(state, media_ids)
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
        for artifact in artifacts:
            if artifact.status == "pending":
                artifact.status, artifact.error = "failed", "生成已中断，此项未执行"
    return _call_result(state, media_ids)


def _inspection_result(inspection: MediaInspection) -> str:
    next_step = (
        "Redraw once with image_regenerate using this inspection_id and a specific correction, or deliver the image as is."
        if inspection.verdict == "revise"
        else "Nothing needs fixing; the image can be delivered as is."
    )
    return json.dumps({**asdict(inspection), "next": next_step}, ensure_ascii=False)


async def inspect_chat_image(media_id: str, state: MediaTurnState) -> str:
    async with state.lock:
        artifact = state.artifacts.get(media_id)
        if artifact is None or artifact.type != "image" or artifact.status != "ready" or not artifact.url:
            return tool_error("只能检查当前会话中已就绪的图片")
        prior = state.inspections.get(media_id)
        if prior is not None:
            return _inspection_result(prior)
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
        return _inspection_result(inspection)


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
