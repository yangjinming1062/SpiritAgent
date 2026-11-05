"""原件内容理解与材料复核的实际多模态请求。"""

import json
from typing import Literal

from infrastructure.ai.models import ModelModality
from infrastructure.ai.prompt_engineer import LlmJsonValidationError, call_llm_json
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import load_user_llm_config
from infrastructure.api_ids import ResourceId
from infrastructure.assets.asset_store import build_data_uri, resolve_asset_reference
from infrastructure.async_io import run_blocking
from infrastructure.video_processing.desktop import sample_desktop_video_frames
from pydantic import BaseModel, ConfigDict, Field
from runtime.paid_work import require_new_generation_call

from features.action_library.schemas import ActionAssetDesign
from features.media.review_contracts import ReviewDecision, ReviewVerdict, VisualReview
from features.media.review_evidence import freeze_review_evidence, read_review_evidence

from .import_jobs import ImportCatalogCandidate, ImportWork, guard_import_work, save_import_progress
from .import_media import image_frame, import_reference_uri
from .import_prompts import (
    IMPORT_ACTION_EVIDENCE_REVIEW,
    IMPORT_EXTRACTION_REVIEW,
    IMPORT_IMAGE_ANALYSIS,
    IMPORT_JSON_REPAIR,
    IMPORT_MATERIAL_MATCH,
    IMPORT_POSE_DESIGN,
    IMPORT_POSE_REVIEW,
    IMPORT_PRESENTATION_REVIEW,
    IMPORT_VIDEO_ANALYSIS,
)
from .schemas import ImportMatch, ImportObservation, ImportUnderstanding


class ImportMatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["reuse", "new", "uncertain"]
    selected_id: ResourceId | None = None
    # 召回候选需要覆盖同一素材库中新近提取和相似内容，不能让固定的 8 项
    # 上限把正确匹配排除在模型复核之外。
    matches: list[ImportMatch] = Field(default_factory=list, max_length=24)


class ImportQualityVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["pass", "review", "fail"]
    reason: str = Field(min_length=1, max_length=1000)


class ImportPresentationReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identity_verdict: ReviewVerdict
    identity_reason: str
    goal_verdict: ReviewVerdict
    goal_reason: str = Field(min_length=1)
    quality_verdict: ReviewVerdict
    quality_reason: str = Field(min_length=1)


def _component_notes(work: ImportWork, observation: ImportObservation) -> str:
    return (
        next((entry.notes for entry in work.result.components if entry.observation.key == observation.key), "")
        if work.result
        else ""
    )


def _append_reference(images: list[str], roles: list[dict[str, object]], uri: str, **attributes: object) -> None:
    if uri not in images:
        images.append(uri)
    roles.append({"image": images.index(uri) + 1, **attributes})


async def _reference_images(
    work: ImportWork, *, include_identity: bool = True
) -> tuple[list[str], list[dict[str, object]]]:
    images: list[str] = []
    roles: list[dict[str, object]] = []
    for index, frame in enumerate(work.context.frames):
        uri = await run_blocking(import_reference_uri, frame.path)
        if uri is None:
            raise ValueError("上传影像的分析帧缺失，请重新分析")
        _append_reference(images, roles, uri, role="original", frame_index=index, seconds=frame.seconds)
    if not include_identity:
        return images, roles
    for role, path in (
        ("identity_portrait", work.context.portrait_path),
        ("identity_body", work.context.identity_path),
    ):
        if path:
            uri = await run_blocking(import_reference_uri, path)
            if uri:
                _append_reference(images, roles, uri, role=role)
    return images, roles


async def _before_call(work: ImportWork) -> None:
    require_new_generation_call(work.user_id)
    await guard_import_work(work, paid=not work.review_only)


async def understand_import(work: ImportWork) -> ImportUnderstanding:
    config = await load_user_llm_config(
        work.user_id, AiTaskKey.visuals, required_modalities=frozenset({ModelModality.text, ModelModality.image})
    )
    images, roles = await _reference_images(work)

    def parse(value: object) -> ImportUnderstanding:
        result = ImportUnderstanding.model_validate(value)
        if (result.asset_kind != "scene" or work.media_kind == "video") and result.media_semantics is None:
            raise LlmJsonValidationError("missing_playback_semantics", fields=("media_semantics",))
        if result.asset_kind == "actor" and (work.media_kind != "video" or not work.context.transparent_video):
            raise LlmJsonValidationError("actor_requires_transparent_video", fields=("asset_kind",))
        for observation in result.observations:
            if observation.frame_index >= len(work.context.frames):
                raise LlmJsonValidationError("invalid_frame_index", fields=("frame_index",))
            if observation.kind == "action":
                if (
                    work.media_kind == "image"
                    and not work.context.animated_image
                    and (observation.extractable or observation.action_design is not None)
                ):
                    raise LlmJsonValidationError(
                        "image_cannot_establish_motion", fields=("extractable", "action_design")
                    )
                if observation.extractable and (
                    observation.action_design is None
                    or observation.start_seconds is None
                    or observation.end_seconds is None
                ):
                    raise LlmJsonValidationError(
                        "action_needs_temporal_evidence", fields=("action_design", "start_seconds", "end_seconds")
                    )
                if (
                    observation.end_seconds is not None
                    and observation.end_seconds > (work.duration_ms or 0) / 1000 + 0.1
                ):
                    raise LlmJsonValidationError("action_outside_video", fields=("end_seconds",))
                if (
                    observation.extractable
                    and len(
                        {
                            frame.seconds
                            for frame in work.context.frames
                            if observation.start_seconds <= frame.seconds <= observation.end_seconds
                        }
                    )
                    < 2
                ):
                    raise LlmJsonValidationError(
                        "action_interval_has_insufficient_frames",
                        fields=("start_seconds", "end_seconds", "extractable"),
                    )
        return result

    return await call_llm_json(
        config,
        IMPORT_IMAGE_ANALYSIS
        if work.media_kind == "image" and not work.context.animated_image
        else IMPORT_VIDEO_ANALYSIS,
        {
            "output_language": work.context.language,
            "user_notes": work.context.user_notes,
            "user_corrections": work.context.correction_notes,
            "filename": work.snapshot.source.original_filename,
            "duration_seconds": (work.duration_ms or 0) / 1000,
            "references": roles,
            "transparent_video": work.context.transparent_video,
            "animated_image": work.context.animated_image,
            "timing_reliable": work.context.timing_reliable,
            "identity": work.context.identity.model_dump(mode="json") if work.context.identity else None,
        },
        schema=ImportUnderstanding.model_json_schema(),
        schema_name="visual_import_understanding",
        parse_output=parse,
        repair_prompt=IMPORT_JSON_REPAIR,
        reference_images=tuple(images),
        preserve_choices=("asset_kind", "identity_match", "presentation_verdict"),
        before_call=lambda: _before_call(work),
    )


async def match_import_material(
    work: ImportWork, observation: ImportObservation, candidates: list[ImportCatalogCandidate]
) -> ImportMatchResult:
    if not candidates:
        return ImportMatchResult(decision="new")
    images, roles = await _reference_images(work, include_identity=observation.kind != "scene")
    for candidate in candidates:
        if candidate.path:
            uri = await run_blocking(import_reference_uri, candidate.path)
            if uri:
                _append_reference(images, roles, uri, role="candidate", material_id=str(candidate.material_id))
    allowed = {candidate.material_id for candidate in candidates}

    def parse(value: object) -> ImportMatchResult:
        result = ImportMatchResult.model_validate(value)
        if {match.material_id for match in result.matches} != allowed or len(result.matches) != len(allowed):
            raise LlmJsonValidationError("candidate_ids_changed", fields=("material_id", "matches"))
        same = [match for match in result.matches if match.decision == "same" and match.confidence == "high"]
        if result.decision == "reuse" and (len(same) != 1 or result.selected_id != same[0].material_id):
            raise LlmJsonValidationError("ambiguous_material_reuse", fields=("selected_id", "decision"))
        if result.decision != "reuse" and result.selected_id is not None:
            raise LlmJsonValidationError("unexpected_selected_material", fields=("selected_id",))
        if result.decision == "new" and any(match.decision != "different" for match in result.matches):
            raise LlmJsonValidationError("new_material_has_unresolved_matches", fields=("decision",))
        return result

    config = await load_user_llm_config(
        work.user_id, AiTaskKey.visuals, required_modalities=frozenset({ModelModality.text, ModelModality.image})
    )
    return await call_llm_json(
        config,
        IMPORT_MATERIAL_MATCH.format(kind=observation.kind),
        {
            "output_language": work.context.language,
            "observation": observation.model_dump(mode="json"),
            "user_corrections": _component_notes(work, observation),
            "references": roles,
            "media_kind": work.media_kind,
            "candidates": [candidate.model_dump(mode="json", exclude={"path"}) for candidate in candidates],
        },
        schema=ImportMatchResult.model_json_schema(),
        schema_name="visual_import_material_match",
        parse_output=parse,
        repair_prompt=IMPORT_JSON_REPAIR,
        reference_images=tuple(images),
        preserve_choices=("decision",),
        before_call=lambda: _before_call(work),
    )


async def review_import_material(
    work: ImportWork, observation: ImportObservation, data: bytes, *, creation_method: Literal["pixels", "generated"]
) -> ImportQualityVerdict:
    images, roles = await _reference_images(work, include_identity=observation.kind == "outfit")
    candidate = await run_blocking(image_frame, data)
    _append_reference(images, roles, build_data_uri(candidate, "image/png"), role="extracted_candidate")
    config = await load_user_llm_config(
        work.user_id, AiTaskKey.visuals, required_modalities=frozenset({ModelModality.text, ModelModality.image})
    )
    return await call_llm_json(
        config,
        IMPORT_EXTRACTION_REVIEW.format(kind=observation.kind),
        {
            "output_language": work.context.language,
            "references": roles,
            "observation": observation.model_dump(mode="json"),
            "user_corrections": _component_notes(work, observation),
            "creation_method": creation_method,
        },
        schema=ImportQualityVerdict.model_json_schema(),
        schema_name="visual_import_extraction_review",
        parse_output=ImportQualityVerdict.model_validate,
        repair_prompt=IMPORT_JSON_REPAIR,
        reference_images=tuple(images),
        preserve_choices=("decision",),
        before_call=lambda: _before_call(work),
    )


async def design_import_pose(work: ImportWork, observation: ImportObservation) -> ActionAssetDesign:
    images, roles = await _reference_images(work)
    config = await load_user_llm_config(
        work.user_id, AiTaskKey.visuals, required_modalities=frozenset({ModelModality.text, ModelModality.image})
    )

    def parse(value: object) -> ActionAssetDesign:
        if not isinstance(value, dict):
            raise ValueError("姿态定义格式无效")
        return ActionAssetDesign.model_validate(
            {
                **value,
                "clip_kind": "loop",
                "duration_seconds": 4,
                "performance_role": "sustained",
                "motion_intensity": "subtle",
                "allowed_triggers": ["manual", "periodic"],
            }
        )

    return await call_llm_json(
        config,
        IMPORT_POSE_DESIGN,
        {
            "output_language": work.context.language,
            "references": roles,
            "observation": observation.model_dump(mode="json"),
            "user_corrections": _component_notes(work, observation),
        },
        schema=ActionAssetDesign.model_json_schema(),
        schema_name="visual_import_pose",
        parse_output=parse,
        repair_prompt=IMPORT_JSON_REPAIR,
        reference_images=tuple(images),
        before_call=lambda: _before_call(work),
    )


async def review_import_action(
    work: ImportWork, observation: ImportObservation, *, pose: bool = False
) -> ImportQualityVerdict:
    images, roles = await _reference_images(work)
    config = await load_user_llm_config(
        work.user_id, AiTaskKey.visuals, required_modalities=frozenset({ModelModality.text, ModelModality.image})
    )
    return await call_llm_json(
        config,
        IMPORT_POSE_REVIEW if pose else IMPORT_ACTION_EVIDENCE_REVIEW,
        {
            "output_language": work.context.language,
            "references": roles,
            "duration_seconds": (work.duration_ms or 0) / 1000,
            "observation": observation.model_dump(mode="json"),
            "user_corrections": _component_notes(work, observation),
        },
        schema=ImportQualityVerdict.model_json_schema(),
        schema_name="visual_import_action_evidence",
        parse_output=ImportQualityVerdict.model_validate,
        repair_prompt=IMPORT_JSON_REPAIR,
        reference_images=tuple(images),
        preserve_choices=("decision",),
        before_call=lambda: _before_call(work),
    )


async def review_import_presentation(work: ImportWork, selected: list[ImportCatalogCandidate]) -> ImportQualityVerdict:
    if (
        work.context.presentation_review
        and work.context.presentation_review.human_approval
        and work.context.presentation_review.verdict == "pass"
    ):
        return ImportQualityVerdict(decision="pass", reason="用户人工放行")
    images, roles = await _reference_images(work)
    for candidate in selected:
        if candidate.path:
            uri = await run_blocking(import_reference_uri, candidate.path)
            if uri:
                _append_reference(
                    images, roles, uri, role=f"selected_{candidate.kind}", material_id=str(candidate.material_id)
                )
    delivery_path = work.context.actor_asset.media_path if work.context.actor_asset else work.media_path
    if work.context.actor_asset and work.media_kind == "video" and work.context.presentation_review is None:
        resolved = resolve_asset_reference(delivery_path)
        if resolved is None:
            raise ValueError("主体成品无法读取")
        delivered = await run_blocking(sample_desktop_video_frames, resolved[0], sample_count=9)
        for frame, seconds in zip(delivered.frames, delivered.times_seconds, strict=True):
            _append_reference(
                images, roles, build_data_uri(frame, "image/webp"), role="delivered_frame", seconds=seconds
            )
    config = await load_user_llm_config(
        work.user_id, AiTaskKey.visuals, required_modalities=frozenset({ModelModality.text, ModelModality.image})
    )
    payload = {
        "references": roles,
        "selected_materials": [candidate.model_dump(mode="json", exclude={"path"}) for candidate in selected],
        "analysis": work.result.understanding.model_dump(mode="json") if work.result else None,
        "user_corrections": work.context.correction_notes,
        "output_language": work.context.language,
        "media_kind": work.media_kind,
        "asset_kind": work.result.understanding.asset_kind if work.result else None,
        "duration_seconds": (work.duration_ms or 0) / 1000,
        "component_corrections": {entry.observation.key: entry.notes for entry in work.result.components if entry.notes}
        if work.result
        else {},
    }
    prompt = IMPORT_PRESENTATION_REVIEW
    previous = work.context.presentation_review
    evidence = previous.evidence if previous else None
    if evidence is None:
        evidence = await freeze_review_evidence(
            work.user_id, tuple(images), prompt=prompt, context=payload, media_path=delivery_path
        )
        work.context.presentation_review = VisualReview(
            required=("goal", "quality")
            if work.result and work.result.understanding.asset_kind == "scene"
            else ("identity", "goal", "quality"),
            evidence=evidence,
            status="unavailable",
        )
        await save_import_progress(work, "presentation_review")
    result = await call_llm_json(
        config,
        evidence.policy_prompt,
        json.loads(evidence.context_json),
        schema=ImportPresentationReview.model_json_schema(),
        schema_name="visual_import_presentation_review",
        parse_output=ImportPresentationReview.model_validate,
        repair_prompt=IMPORT_JSON_REPAIR,
        reference_images=await read_review_evidence(evidence),
        preserve_choices=("identity_verdict", "goal_verdict", "quality_verdict"),
        before_call=lambda: _before_call(work),
    )

    report = VisualReview(
        required=("goal", "quality")
        if work.result and work.result.understanding.asset_kind == "scene"
        else ("identity", "goal", "quality"),
        identity=ReviewDecision(verdict=result.identity_verdict, reason=result.identity_reason)
        if result.identity_verdict and result.identity_reason
        else None,
        goal=ReviewDecision(verdict=result.goal_verdict, reason=result.goal_reason),
        quality=ReviewDecision(verdict=result.quality_verdict, reason=result.quality_reason),
        evidence=evidence,
    )
    if previous:
        for name in report.required:
            saved = getattr(previous, name)
            if saved is not None and saved.verdict in {"pass", "fail"}:
                setattr(report, name, saved)
    work.context.presentation_review = report
    await save_import_progress(work, "presentation_review")
    return ImportQualityVerdict(
        decision=report.verdict,
        reason="；".join(getattr(report, name).reason for name in report.required if getattr(report, name)),
    )
