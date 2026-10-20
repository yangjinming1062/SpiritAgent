"""上传桌景独立持有来源及动作作用域。"""

import hashlib
import json
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PAUSED = "上传来源已独立保存，请确认画面后继续制作"


def _load(value: str | None) -> dict[str, Any]:
    return json.loads(value) if value else {}


def _dump(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _path(*values: Any) -> str | None:
    return next((value for value in values if isinstance(value, str) and value.strip()), None)


def _upload_context(suite: Any, actions: list[Any]) -> dict[str, Any]:
    idle = next((action for action in actions if action["key"] == "idle"), None)
    asset = _load(idle["accepted_asset_json"]) if idle is not None else {}
    progress = _load(idle["generation_state_json"]) if idle is not None else {}
    refinement = progress.get("video_refinement") or {}
    uploaded_draft = progress.get("source") == "external_upload"
    original_image = _path(asset.get("source_image_path"), progress.get("source_image_path"))
    original_video = _path(
        asset.get("source_video_path"),
        asset.get("video_path") if asset.get("source") == "external_upload" else None,
        refinement.get("original_video_path"),
        progress.get("source_path") if uploaded_draft else None,
    )
    if suite["source"] == "image_upload" and original_image is None and uploaded_draft:
        original_image = _path(progress.get("pose_path") if progress.get("image_origin") is None else None)
    reference = _path(
        original_image,
        suite["base_image_path"] if suite["base_version"] else None,
        asset.get("image_path"),
        refinement.get("image_path"),
        progress.get("pose_path") if progress.get("image_confirmed") else None,
    )
    locked = bool(
        original_image
        or original_video
        or reference
        or any(action["accepted_asset_json"] for action in actions)
        or any(
            (draft := _load(action["generation_state_json"])).get("pose_path") or draft.get("candidate")
            for action in actions
        ),
    )
    return {
        "source_key": hashlib.sha256(uuid4().bytes).hexdigest(),
        "original_image_path": original_image,
        "original_video_path": original_video,
        "reference_image_path": reference,
        "outfit_description": "",
        "outfit_analyzed": False,
        "persona": _load(suite["context_json"]).get("persona", {}),
        "locked": locked,
    }


def _pause_progress(action: Any, reference: str | None) -> dict[str, Any]:
    progress = _load(action["generation_state_json"])
    if not progress:
        if action["status"] in {"queued", "processing"}:
            return {"status": "failed", "stage": "paused", "error": _PAUSED, "update_pending": False}
        return {"update_pending": False}
    image_chain = _load(progress.get("image_chain_json"))
    alignment_chain = _load(progress.get("reference_alignment_chain_json"))
    unknown = any(
        chain.get("phase") == "submitting" or chain.get("stop_reason") == "result_unknown"
        for chain in (image_chain, alignment_chain)
        if chain
    )
    if progress.get("image_chain_json") and not unknown:
        progress["previous_image_chain_json"] = progress["image_chain_json"]
        progress["image_chain_json"] = None
    if not unknown:
        progress["reference_alignment_chain_json"] = None
        progress["reference_alignment_source_hash"] = None
        progress["aligned_outfit_path"] = None
        progress["aligned_outfit_hash"] = None
    progress["submission_unknown"] = bool(progress.get("submission_unknown") or unknown)
    progress["creation_plan"] = None
    progress["motion_prompt"] = ""
    progress["auto_continue"] = False
    progress["force_review"] = True
    progress["paused_by_user"] = True
    if not progress.get("image_confirmed") and not progress.get("candidate"):
        progress["previous_pose_path"] = reference
        progress["pose_path"] = ""
        progress["opening_review"] = None
    values: dict[str, Any] = {"generation_state_json": _dump(progress), "update_pending": False}
    if action["status"] in {"queued", "processing"}:
        values.update(status="failed", stage="paused", error=_PAUSED)
    return values


def upgrade() -> None:
    connection = op.get_bind()
    metadata = sa.MetaData()
    suites = sa.Table("desktop_video_sets", metadata, autoload_with=connection)
    actions = sa.Table("desktop_video_actions", metadata, autoload_with=connection)
    definitions = sa.Table("action_definitions", metadata, autoload_with=connection)
    targets = sa.Table("action_sync_targets", metadata, autoload_with=connection)
    for suite in connection.execute(sa.select(suites).where(suites.c.source != "ai")).mappings().all():
        if "source_key" in _load(suite["context_json"]):
            continue
        suite_actions = connection.execute(sa.select(actions).where(actions.c.set_id == suite["id"])).mappings().all()
        snapshot = _upload_context(suite, suite_actions)
        context_json = _dump(snapshot)
        connection.execute(
            suites.update()
            .where(suites.c.id == suite["id"])
            .values(
                avatar_id=None,
                outfit_id=None,
                scene_id=None,
                context_json=context_json,
                context_hash=hashlib.sha256(context_json.encode()).hexdigest(),
                source_visual_key=snapshot["source_key"],
            ),
        )
        suite_targets = (
            connection.execute(
                sa.select(targets).where(targets.c.target_kind == "desktop", targets.c.target_id == suite["id"]),
            )
            .mappings()
            .all()
        )
        definition_ids = {action["definition_id"] for action in suite_actions if action["definition_id"] is not None}
        definition_ids.update(target["definition_id"] for target in suite_targets)
        clones: dict[int, int] = {}
        for definition in (
            connection.execute(
                sa.select(definitions).where(definitions.c.id.in_(definition_ids)),
            )
            .mappings()
            .all()
        ):
            reference = snapshot["reference_image_path"]
            source = {
                "user_id": suite["user_id"],
                "outfit_id": None,
                "target_kind": "desktop",
                "target_id": suite["id"],
                "scope": "target",
                "label": suite["title"] or "桌面背景",
                "source_visual_key": snapshot["source_key"],
                "ready": bool(reference),
                "blocked_reason": None if reference else "上传来源缺失，请重新创建桌面背景",
                "reference_paths": [reference] if reference else [],
                "reference_roles": ["opening"] if reference else [],
                "identity": None,
                "outfit_description": "",
                "persona": snapshot["persona"],
            }
            values = {key: value for key, value in definition.items() if key != "id"}
            values.update(
                outfit_id=None,
                source_visual_key=snapshot["source_key"],
                review_source_json=_dump(source),
                origin_kind="desktop",
                origin_target_id=suite["id"],
                reuse_definition_id=None,
                fanout_pending=False,
            )
            if values["status"] in {"pending", "reviewing", "deferred"}:
                values.update(status="deferred", review_reason=_PAUSED)
            if values["status"] == "reused":
                values.update(status="deferred", review_reason=_PAUSED)
            clones[definition["id"]] = connection.execute(
                definitions.insert().values(**values).returning(definitions.c.id),
            ).scalar_one()
        for action in suite_actions:
            values = _pause_progress(action, snapshot["reference_image_path"])
            if action["definition_id"] is not None:
                values["definition_id"] = clones[action["definition_id"]]
            connection.execute(actions.update().where(actions.c.id == action["id"]).values(**values))
        for target in suite_targets:
            values = {key: value for key, value in target.items() if key != "id"}
            values.update(definition_id=clones[target["definition_id"]], automatic=False)
            if target["state"] != "excluded":
                values.update(state="manual", reason=_PAUSED)
            connection.execute(targets.delete().where(targets.c.id == target["id"]))
            connection.execute(targets.insert().values(**values))
        linked = {target["definition_id"] for target in suite_targets}
        for action in suite_actions:
            if action["definition_id"] is None or action["definition_id"] in linked:
                continue
            connection.execute(
                targets.insert().values(
                    user_id=suite["user_id"],
                    definition_id=clones[action["definition_id"]],
                    target_kind="desktop",
                    target_id=suite["id"],
                    action_id=action["id"],
                    automatic=False,
                    state="manual",
                    reason=_PAUSED,
                ),
            )


def downgrade() -> None:
    raise RuntimeError("上传来源隔离无法安全还原为素材库来源，请恢复升级前备份")
