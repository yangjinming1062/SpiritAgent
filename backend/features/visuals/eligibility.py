"""成品的自动使用资格与文件完整性；不判断生活情境或调度时机。"""

from infrastructure.assets.asset_store import resolve_asset_reference

from features.media.review_contracts import VisualReview, visual_reference_basis
from features.media.review_evidence import review_evidence_intact

from .schemas import VisualAutoPlayBlock


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
    return not automatic_media_blocks(
        media_kind=media_kind,
        asset_kind=asset_kind,
        media_path=media_path,
        auto_selectable=auto_selectable,
        clip_kind=clip_kind,
        body=body,
        presentation_approved=presentation_approved,
        review_result=review_result,
        reference_source=reference_source,
        media_content_hash=media_content_hash,
        scene_reference=scene_reference,
        action_clip_kind=action_clip_kind,
    )


def automatic_media_blocks(
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
) -> list[VisualAutoPlayBlock]:
    """返回自动使用资格的确定性阻塞项；资格判断和提示必须共用这一份逻辑。"""

    blocks: list[VisualAutoPlayBlock] = []

    try:
        report = (
            review_result if isinstance(review_result, VisualReview) else VisualReview.model_validate(review_result)
        )
    except ValueError:
        report = None

    required = {"goal", "quality"} | ({"identity"} if asset_kind in {"actor", "cinematic"} else set())
    resolved = resolve_asset_reference(media_path)
    asset_exists = resolved is not None and resolved[0].is_file()
    if (
        not asset_exists
        or media_content_hash is None
        or report is None
        or report.evidence is None
        or report.evidence.media_hash != media_content_hash
        or not review_evidence_intact(report.evidence)
    ):
        blocks.append(
            VisualAutoPlayBlock(
                code="media_unavailable" if not asset_exists else "presentation_review",
                reason=(
                    "影像文件不可用或检查依据已失效，请重新制作"
                    if not asset_exists
                    else "当前版本的呈现检查依据不完整，请重新检查"
                ),
                next_step="redo",
            )
        )

    if reference_source is None or report is None or report.reference_basis != visual_reference_basis(reference_source):
        if not any(block.code == "presentation_review" for block in blocks):
            blocks.append(
                VisualAutoPlayBlock(
                    code="presentation_review",
                    reason="影像尚未完成与当前来源匹配的呈现审核",
                    next_step="redo",
                )
            )
    elif (
        not presentation_approved
        or not required.issubset(report.required)
        or report.verdict != "pass"
        or report.evidence is None
    ):
        labels = {"identity": "身份", "goal": "画面要求", "quality": "画面质量"}
        failed: list[str] = []
        for name in sorted(required):
            decision = getattr(report, name, None)
            if decision is None or decision.verdict != "pass":
                failed.append(
                    f"{labels[name]}未通过" if decision and decision.verdict == "fail" else f"{labels[name]}待复核"
                )
        blocks.append(
            VisualAutoPlayBlock(
                code="presentation_review",
                reason=("、".join(failed) + "，请完成呈现审核") if failed else "呈现审核尚未通过",
                next_step="redo",
            )
        )

    if media_kind not in {"image", "video"}:
        blocks.append(
            VisualAutoPlayBlock(
                code="unsupported_media", reason="当前媒体类型不支持自动演出", next_step="replace_media"
            )
        )
    elif media_kind == "video" and clip_kind not in {"once", "loop"}:
        blocks.append(
            VisualAutoPlayBlock(code="timing_mismatch", reason="视频没有可用的短动作或持续动作时序", next_step="redo")
        )
    elif media_kind == "video" and asset_kind == "scene" and clip_kind != "loop":
        blocks.append(
            VisualAutoPlayBlock(code="timing_mismatch", reason="环境视频必须是可循环的持续画面", next_step="redo")
        )
    elif media_kind == "video" and asset_kind != "scene" and clip_kind != action_clip_kind:
        blocks.append(
            VisualAutoPlayBlock(code="timing_mismatch", reason="视频时序与对应动作的播放方式不一致", next_step="redo")
        )

    if asset_kind in {"scene", "cinematic"} and not scene_reference:
        blocks.append(
            VisualAutoPlayBlock(
                code="materials_incomplete", reason="还没有确认与影像相符的环境素材", next_step="confirm_materials"
            )
        )
    if asset_kind != "scene" and auto_selectable is not True:
        blocks.append(
            VisualAutoPlayBlock(
                code="semantics_unclear",
                reason="影像的实际动作或适用情境还不足以支持自动选择",
                next_step="reanalyze" if reference_source and reference_source.get("kind") == "analysis" else "redo",
            )
        )
    if asset_kind == "actor" and not actor_body_ready(body, clip_kind):
        blocks.append(
            VisualAutoPlayBlock(
                code="actor_composition",
                reason="透明主体缺少可合成的遮罩、首末帧或连续性数据",
                next_step="redo",
            )
        )

    return _unique_blocks(blocks)


def _unique_blocks(blocks: list[VisualAutoPlayBlock]) -> list[VisualAutoPlayBlock]:
    unique: list[VisualAutoPlayBlock] = []
    seen: set[tuple[str, str]] = set()
    for block in blocks:
        key = (block.code, block.reason)
        if key not in seen:
            seen.add(key)
            unique.append(block)
    return unique
