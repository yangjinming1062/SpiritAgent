"""成品的自动使用资格与文件完整性；不判断生活情境或调度时机。"""

from infrastructure.assets.asset_store import resolve_asset_reference

from features.media.review_contracts import VisualReview, visual_reference_basis
from features.media.review_evidence import review_evidence_intact


def existing_asset(reference: str | None) -> str | None:
    resolved = resolve_asset_reference(reference)
    return reference if resolved is not None and resolved[0].is_file() else None


def actor_body_ready(body: dict | None, clip_kind: str | None) -> bool:
    return bool(
        body
        and (clip_kind == "once" or body.get("continuity_key"))
        and all(existing_asset(body.get(field)) for field in ("mask_path", "first_frame_path", "last_frame_path"))
    )


def automatic_media_ready(
    *,
    media_kind: str,
    asset_kind: str,
    media_path: str,
    auto_selectable: bool | None,
    clip_kind: str | None,
    body: dict | None,
    presentation_approved: bool | None = False,
    review_result: VisualReview | dict | None = None,
    reference_source: dict | None = None,
    media_content_hash: str | None = None,
    scene_reference: bool = True,
    action_clip_kind: str | None = None,
) -> bool:
    try:
        report = (
            review_result if isinstance(review_result, VisualReview) else VisualReview.model_validate(review_result)
        )
    except ValueError:
        return False
    required = {"goal", "quality"} | ({"identity"} if asset_kind in {"actor", "cinematic"} else set())
    if reference_source is None or report.reference_basis != visual_reference_basis(reference_source):
        return False
    if not required.issubset(report.required) or report.verdict != "pass" or report.evidence is None:
        return False
    resolved = resolve_asset_reference(media_path)
    if (
        resolved is None
        or media_content_hash is None
        or report.evidence.media_hash != media_content_hash
        or not review_evidence_intact(report.evidence)
    ):
        return False
    return bool(
        presentation_approved
        and (media_kind == "video" or media_kind == "image")
        and (media_kind != "video" or clip_kind in ("once", "loop"))
        and (media_kind != "video" or asset_kind != "scene" or clip_kind == "loop")
        and (asset_kind not in ("scene", "cinematic") or scene_reference)
        and (media_kind != "video" or asset_kind == "scene" or clip_kind == action_clip_kind)
        and (asset_kind == "scene" or (asset_kind in ("actor", "cinematic") and auto_selectable is True))
        and existing_asset(media_path)
        and (asset_kind != "actor" or actor_body_ready(body, clip_kind))
    )
