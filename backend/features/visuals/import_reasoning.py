"""原件内容理解与材料复核的实际多模态请求。"""

from typing import Literal

from infrastructure.ai.models import ModelModality
from infrastructure.ai.prompt_engineer import LlmJsonValidationError, call_llm_json
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import load_user_llm_config
from infrastructure.api_ids import ResourceId
from infrastructure.assets.asset_store import build_data_uri, read_valid_image_data_uri
from infrastructure.async_io import run_blocking
from pydantic import BaseModel, ConfigDict, Field
from runtime.paid_work import require_new_generation_call

from .import_jobs import ImportCatalogCandidate, ImportWork, guard_import_work
from .import_prompts import (
    IMPORT_ACTION_EVIDENCE_REVIEW,
    IMPORT_EXTRACTION_REVIEW,
    IMPORT_IMAGE_ANALYSIS,
    IMPORT_JSON_REPAIR,
    IMPORT_MATERIAL_MATCH,
    IMPORT_PRESENTATION_REVIEW,
    IMPORT_VIDEO_ANALYSIS,
)
from .schemas import ImportMatch, ImportObservation, ImportUnderstanding


class ImportMatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["reuse", "new", "uncertain"]
    selected_id: ResourceId | None = None
    matches: list[ImportMatch] = Field(default_factory=list, max_length=8)


class ImportQualityVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["pass", "review", "fail"]
    reason: str = Field(min_length=1, max_length=1000)


async def _reference_images(work: ImportWork) -> tuple[list[str], list[dict[str, object]]]:
    images: list[str] = []
    roles: list[dict[str, object]] = []
    for index, frame in enumerate(work.context.frames):
        uri = await run_blocking(read_valid_image_data_uri, frame.path)
        if uri is None:
            raise ValueError("上传影像的分析帧缺失，请重新分析")
        images.append(uri)
        roles.append({"image": len(images), "role": "original", "frame_index": index, "seconds": frame.seconds})
    for role, path in (
        ("identity_portrait", work.context.portrait_path),
        ("identity_body", work.context.identity_path),
    ):
        if path:
            uri = await run_blocking(read_valid_image_data_uri, path)
            if uri:
                images.append(uri)
                roles.append({"image": len(images), "role": role})
    return images, roles


async def _before_call(work: ImportWork) -> None:
    require_new_generation_call(work.user_id)
    await guard_import_work(work, paid=True)


async def understand_import(work: ImportWork) -> ImportUnderstanding:
    config = await load_user_llm_config(
        work.user_id, AiTaskKey.visuals, required_modalities=frozenset({ModelModality.text, ModelModality.image})
    )
    images, roles = await _reference_images(work)

    def parse(value: object) -> ImportUnderstanding:
        result = ImportUnderstanding.model_validate(value)
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
    images, roles = await _reference_images(work)
    for candidate in candidates:
        if candidate.path:
            uri = await run_blocking(read_valid_image_data_uri, candidate.path)
            if uri:
                images.append(uri)
                roles.append({"image": len(images), "role": "candidate", "material_id": str(candidate.material_id)})
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


async def review_import_material(work: ImportWork, observation: ImportObservation, data: bytes) -> ImportQualityVerdict:
    images, roles = await _reference_images(work)
    images.append(build_data_uri(data, "image/png"))
    roles.append({"image": len(images), "role": "extracted_candidate"})
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
        },
        schema=ImportQualityVerdict.model_json_schema(),
        schema_name="visual_import_extraction_review",
        parse_output=ImportQualityVerdict.model_validate,
        repair_prompt=IMPORT_JSON_REPAIR,
        reference_images=tuple(images),
        preserve_choices=("decision",),
        before_call=lambda: _before_call(work),
    )


async def review_import_action(work: ImportWork, observation: ImportObservation) -> ImportQualityVerdict:
    images, roles = await _reference_images(work)
    config = await load_user_llm_config(
        work.user_id, AiTaskKey.visuals, required_modalities=frozenset({ModelModality.text, ModelModality.image})
    )
    return await call_llm_json(
        config,
        IMPORT_ACTION_EVIDENCE_REVIEW,
        {
            "output_language": work.context.language,
            "references": roles,
            "duration_seconds": (work.duration_ms or 0) / 1000,
            "observation": observation.model_dump(mode="json"),
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
    images, roles = await _reference_images(work)
    for candidate in selected:
        if candidate.path:
            uri = await run_blocking(read_valid_image_data_uri, candidate.path)
            if uri:
                images.append(uri)
                roles.append(
                    {
                        "image": len(images),
                        "role": f"selected_{candidate.kind}",
                        "material_id": str(candidate.material_id),
                    }
                )
    config = await load_user_llm_config(
        work.user_id, AiTaskKey.visuals, required_modalities=frozenset({ModelModality.text, ModelModality.image})
    )
    return await call_llm_json(
        config,
        IMPORT_PRESENTATION_REVIEW,
        {
            "output_language": work.context.language,
            "references": roles,
            "media_kind": work.media_kind,
            "user_corrections": work.context.correction_notes,
            "duration_seconds": (work.duration_ms or 0) / 1000,
            "analysis": work.result.understanding.model_dump(mode="json") if work.result else None,
            "selected_materials": [candidate.model_dump(mode="json", exclude={"path"}) for candidate in selected],
        },
        schema=ImportQualityVerdict.model_json_schema(),
        schema_name="visual_import_presentation_review",
        parse_output=ImportQualityVerdict.model_validate,
        repair_prompt=IMPORT_JSON_REPAIR,
        reference_images=tuple(images),
        preserve_choices=("decision",),
        before_call=lambda: _before_call(work),
    )
