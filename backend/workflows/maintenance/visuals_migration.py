"""旧桌景结构到影像库的一次性映射规则。

只读预检命令与迁移 `0003_visuals` 共用本模块，保证预检结论与正式转换一致。输入是固定旧
结构的原始行（普通 dict，由调用方以 SQL 读取），输出是新表行与诊断信息；不导入业务 ORM，
不调用模型，不移动或重编码媒体。迁移完成后本模块仅保留供排障读取，不再演进。
"""

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from features.action_library.views import normalize_action_name
from sqlalchemy import text as sa_text

# 一次性迁移确认的来源补全：上传桌景没有素材外键，按用户确认关联的衣柜与环境补齐。
# 生产 ID 只存在于本数据转换段，业务运行代码不得硬编码。
UPLOAD_SET_MATERIAL_OVERRIDES: dict[int, dict[str, int]] = {
    7: {"outfit_id": 3, "scene_id": 6},
}

RETIRED_NIGHTLY_CAPABILITIES = ("outfit.wear", "scene.activate")

_OLD_DEFINITION_STATUS = {
    "pending": "pending",
    "reviewing": "deferred",
    "approved": "approved",
    "reused": "approved",
    "deferred": "deferred",
    "rejected": "rejected",
}

# 旧 DesktopVideoProgress 中仍然有效的字段；衔接方式与套装基准不再保留，
# 可自然衔接改为依据冻结输入图的实际一致性判断。
_PROGRESS_FIELDS = (
    "generation_id",
    "target_width",
    "target_height",
    "source",
    "customization_priority",
    "image_only",
    "paused_by_user",
    "image_confirmed",
    "aspect_ratio",
    "duration_seconds",
    "source_image_path",
    "expand_image",
    "regenerate_image",
    "frame_width",
    "frame_height",
    "previous_pose_path",
    "previous_image_origin",
    "image_origin",
    "image_selection",
    "video_selection",
    "visual_description",
    "outfit_description",
    "action_description",
    "creation_plan",
    "opening_review",
    "repair_reason",
    "repair_description",
    "motion_replan_count",
    "motion_prompt",
    "pose_path",
    "reference_alignment_chain_json",
    "reference_alignment_source_hash",
    "aligned_outfit_path",
    "aligned_outfit_hash",
    "image_chain_json",
    "video_chain_json",
    "video_refinement",
    "candidate",
    "identity_score",
    "review_verdict",
    "review_reason",
    "submission_unknown",
    "failure_reason",
    "source_path",
    "reprocess_only",
)


@dataclass
class MigrationPlan:
    action_assets: list[dict[str, Any]] = field(default_factory=list)
    visual_items: list[dict[str, Any]] = field(default_factory=list)
    visual_jobs: list[dict[str, Any]] = field(default_factory=list)
    presentation_rows: list[dict[str, Any]] = field(default_factory=list)
    queue_retargets: list[dict[str, Any]] = field(default_factory=list)
    nightly_retired: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    # 旧 ID → 新行在各列表中的下标，供报告输出旧到新映射。
    action_mapping: dict[str, int] = field(default_factory=dict)
    item_mapping: dict[str, int] = field(default_factory=dict)
    job_mapping: dict[str, int] = field(default_factory=dict)


def _canon_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return [value]
    if not isinstance(value, list):
        return [str(value)]
    return [str(item) for item in value]


def _load_json(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return None


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def action_content_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    """动作内容指纹：只有全部内容一致才允许合并为同一份动作素材。"""

    return (
        row.get("user_id"),
        (row.get("name") or "").strip(),
        (row.get("motion_description") or "").strip(),
        tuple(_canon_list(row.get("use_when_json"))),
        tuple(_canon_list(row.get("avoid_when_json"))),
        row.get("clip_kind"),
        row.get("duration_seconds"),
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_asset_path(data_dir: Path, bare_path: str | None) -> Path | None:
    if not bare_path:
        return None
    cleaned = str(bare_path).replace("\\", "/").split("?", 1)[0]
    if cleaned.startswith("/api/companion/asset/"):
        cleaned = "companion-assets/" + cleaned.removeprefix("/api/companion/asset/")
    parts = cleaned.split("/")
    if (
        len(parts) < 3
        or parts[0] != "companion-assets"
        or not parts[1].isdigit()
        or any(part in {"", ".", ".."} or ":" in part for part in parts)
    ):
        return None
    resolved = (data_dir / cleaned).resolve()
    return resolved if resolved.is_relative_to(data_dir.resolve()) else None


def build_action_asset(row: Mapping[str, Any], *, source: str, preset_key: str | None = None) -> dict[str, Any]:
    review_source = _load_json(row.get("review_source_json"))
    if isinstance(review_source, dict):
        review_source = {
            k: v
            for k, v in review_source.items()
            if k
            in {
                "identity",
                "identity_path",
                "identity_hash",
                "portrait_path",
                "portrait_hash",
                "persona",
            }
        }
    else:
        review_source = {}
    triggers = _canon_list(row.get("allowed_triggers_json")) or ["chat", "manual"]
    return {
        "user_id": row.get("user_id"),
        "name": row.get("name") or "",
        "normalized_name": normalize_action_name(row.get("name") or ""),
        "motion_description": row.get("motion_description") or row.get("description") or "",
        "use_when_json": _dump_json(_canon_list(row.get("use_when_json"))),
        "avoid_when_json": _dump_json(_canon_list(row.get("avoid_when_json"))),
        "allowed_triggers_json": _dump_json(triggers),
        "clip_kind": row.get("clip_kind") or row.get("kind") or "once",
        "duration_seconds": int(row.get("duration_seconds") or 4),
        "reason": row.get("reason") or "",
        "source": source,
        "status": _OLD_DEFINITION_STATUS.get(row.get("status") or "pending", "pending")
        if not preset_key
        else "approved",
        "review_reason": row.get("review_reason") or "",
        "review_source_json": _dump_json(review_source),
    }


def convert_progress(raw: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not raw:
        return None
    out: dict[str, Any] = {}
    for name in _PROGRESS_FIELDS:
        if name in raw and raw[name] is not None:
            out[name] = _rename_task_values(raw[name])
    chain = _load_json(out.get("image_chain_json"))
    if isinstance(chain, dict) and chain.get("phase") == "complete" and chain.get("stop_reason") != "result_unknown":
        out.pop("image_chain_json", None)
    candidate = out.get("candidate")
    if isinstance(candidate, Mapping):
        out["candidate"] = convert_accepted_asset(candidate)
    return out or None


def _rename_task_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "visuals" if key == "task" and item == "desktop_life" else _rename_task_values(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_rename_task_values(item) for item in value]
    if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        parsed = _load_json(value)
        if isinstance(parsed, (dict, list)):
            return _dump_json(_rename_task_values(parsed))
    return value


def combo_snapshot(
    *,
    identity: Any,
    identity_path: str,
    identity_hash: str,
    portrait_path: str,
    portrait_hash: str,
    outfit: dict[str, Any],
    scene: dict[str, Any],
    action: dict[str, Any],
    plan: Any,
    target_width: int,
    target_height: int,
    requirements: str,
    persona: dict[str, str],
) -> dict[str, Any]:
    return {
        "identity": identity,
        "identity_path": identity_path,
        "identity_hash": identity_hash,
        "portrait_path": portrait_path,
        "portrait_hash": portrait_hash,
        "identity_reference_role": "portrait",
        "outfit": outfit,
        "scene": scene,
        "action": action,
        "plan": plan,
        "target_width": target_width,
        "target_height": target_height,
        "requirements": requirements,
        "persona": persona,
    }


def action_ref_from_asset(asset_row: Mapping[str, Any], asset: Mapping[str, Any]) -> dict[str, Any]:
    """从旧动作行构造动作素材冻结引用。"""

    return {
        "action_id": None,
        "name": asset_row.get("name") or "",
        "motion_description": asset_row.get("description") or "",
        "clip_kind": asset_row.get("kind") or "once",
        "duration_seconds": int(asset_row.get("duration_seconds") or 4),
        "use_when": _canon_list(asset_row.get("use_when_json")),
        "avoid_when": _canon_list(asset_row.get("avoid_when_json")),
        "allowed_triggers": _canon_list(asset_row.get("allowed_triggers_json")) or ["chat", "manual"],
    }


def convert_accepted_asset(raw: Mapping[str, Any]) -> dict[str, Any]:
    """把旧 DesktopVideoAsset 记录收敛为成品事实字段。"""

    media_kind = "video" if raw.get("video_path") else "image"
    return {
        "media_kind": media_kind,
        "media_path": raw.get("video_path") or raw.get("image_path") or "",
        "poster_path": raw.get("poster_path") or "",
        "input_image_path": raw.get("image_path") if media_kind == "video" else None,
        "width": int(raw.get("width") or 0),
        "height": int(raw.get("height") or 0),
        "duration_ms": int(raw.get("duration_ms")) if raw.get("duration_ms") else None,
        "visual_description": raw.get("visual_description") or "",
        "outfit_description": raw.get("outfit_description") or "",
        "creation_plan": raw.get("creation_plan"),
        "feedback": raw.get("feedback") or "",
        "video_feedback": raw.get("video_feedback") or "",
    }


def candidate_image_path(progress: Mapping[str, Any] | None) -> str | None:
    """暂停工作项中已通过检查的起始图路径。"""

    if not progress:
        return None
    candidate = progress.get("candidate") or {}
    if isinstance(candidate, Mapping) and candidate.get("image_path"):
        return str(candidate["image_path"])
    if progress.get("image_confirmed") and progress.get("pose_path"):
        return str(progress["pose_path"])
    return None


def queue_retarget_rows(
    rows: Iterable[Mapping[str, Any]],
    job_generation_ids: Mapping[str, int],
) -> list[dict[str, Any]]:
    """公共视频队列归属改指影像工作项；已消费或终止句柄保持原状等待独立收尾。"""

    out: list[dict[str, Any]] = []
    for row in rows:
        frozen = _load_json(row.get("frozen_json")) or {}
        if isinstance(frozen, dict) and frozen.get("task"):
            frozen = dict(frozen)
            frozen["task"] = "visuals"
        generation_id = row.get("generation_id") or (frozen or {}).get("generation_id")
        out.append(
            {
                "id": row.get("id"),
                "entity_id": job_generation_ids.get(str(generation_id)),
                "frozen_json": _dump_json(frozen) if frozen else row.get("frozen_json"),
            },
        )
    return out


def nightly_retired_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """把待执行且引用旧呈现操作的夜间计划标记为未执行终态。"""

    out: list[dict[str, Any]] = []
    for row in rows:
        if row.get("status") != "pending":
            continue
        if row.get("capability") not in RETIRED_NIGHTLY_CAPABILITIES:
            continue
        out.append({"id": row.get("id"), "capability": row.get("capability")})
    return out


def summarize(plan: MigrationPlan) -> dict[str, Any]:
    """旧到新映射的可读摘要，供预检与迁移报告共用。"""

    return {
        "action_assets": len(plan.action_assets),
        "visual_images": sum(1 for item in plan.visual_items if item["media_kind"] == "image"),
        "visual_videos": sum(1 for item in plan.visual_items if item["media_kind"] == "video"),
        "visual_jobs": len(plan.visual_jobs),
        "presentations": len(plan.presentation_rows),
        "queue_retargets": len(plan.queue_retargets),
        "retired_nightly_actions": len(plan.nightly_retired),
        "action_mapping": plan.action_mapping,
        "item_mapping": plan.item_mapping,
        "job_mapping": plan.job_mapping,
        "diagnostics": plan.diagnostics,
    }


# 固定的旧结构读取方式：不依赖业务 ORM，迁移与预检共用同一份 SQL。
OLD_TABLE_SQL: dict[str, str] = {
    "users": "SELECT id FROM users ORDER BY id",
    "avatars": (
        "SELECT id, user_id, asset_url, seed_fullbody_url, is_fullbody_confirmed "
        "FROM avatar_assets WHERE active ORDER BY id"
    ),
    "cards": (
        "SELECT id, user_id, avatar_id, revision, automatic_json, overrides_json "
        "FROM companion_character_cards ORDER BY id"
    ),
    "outfits": (
        "SELECT id, user_id, name, description, fullbody_url, status, source_json FROM companion_outfits ORDER BY id"
    ),
    "scenes": ("SELECT id, user_id, title, description, media_path, status FROM companion_scenes ORDER BY id"),
    "definitions": (
        "SELECT id, user_id, outfit_id, source_visual_key, name, motion_description, "
        "use_when_json, avoid_when_json, clip_kind, duration_seconds, reason, source, "
        "status, review_reason, review_source_json FROM action_definitions ORDER BY id"
    ),
    "sets": (
        "SELECT id, user_id, avatar_id, outfit_id, scene_id, source, context_json, title, "
        "target_width, target_height, status FROM desktop_video_sets ORDER BY id"
    ),
    "set_actions": (
        "SELECT id, user_id, set_id, definition_id, key, name, description, preset, kind, "
        "duration_seconds, use_when_json, avoid_when_json, allowed_triggers_json, status, "
        "stage, error, generation_state_json, accepted_asset_json "
        "FROM desktop_video_actions ORDER BY id"
    ),
    "states": (
        "SELECT id, user_id, current_set_id, selected_action_id, loop_action_id, set_epoch, "
        "version, pinned, autonomous_enabled, target_width, target_height "
        "FROM desktop_video_states ORDER BY id"
    ),
    "queue": (
        "SELECT id, user_id, kind, entity_id, generation_id, state, frozen_json, consumed_at "
        "from video_queue_requests WHERE kind IN ('action', 'desktop') ORDER BY id"
    ),
    "nightly": "SELECT id, user_id, capability, status FROM nightly_activity_actions ORDER BY id",
}

_FEATURE_KEYS = (
    "head_shape",
    "facial_features",
    "facial_surface",
    "head_identifiers",
    "body_shape",
    "proportions",
    "limbs_and_appendages",
    "body_surface",
)


def fetch_old_rows(bind: Any) -> dict[str, list[dict[str, Any]]]:
    """按固定 SQL 读取旧结构原始行。"""

    out: dict[str, list[dict[str, Any]]] = {}
    for name, sql in OLD_TABLE_SQL.items():
        out[name] = [dict(row) for row in bind.execute(sa_text(sql)).mappings()]
    return out


def _identity_dict(card: Mapping[str, Any] | None) -> dict[str, Any]:
    features = _load_json(card.get("automatic_json")) if card else None
    overrides = _load_json(card.get("overrides_json")) if card else None
    features = (
        {k: str(v) for k, v in (features or {}).items() if k in _FEATURE_KEYS} if isinstance(features, dict) else {}
    )
    overrides = (
        {k: (str(v) if v is not None else None) for k, v in (overrides or {}).items() if k in _FEATURE_KEYS}
        if isinstance(overrides, dict)
        else {}
    )
    return {
        "avatar_id": (card or {}).get("avatar_id") or 0,
        "revision": (card or {}).get("revision") or 0,
        "features": features,
        "overrides": overrides,
    }


def _material_from_row(
    row: Mapping[str, Any] | None,
    *,
    path_field: str,
    desc_field: str,
    data_dir: Path,
    frozen_path: str | None = None,
    frozen_hash: str | None = None,
    frozen_description: str = "",
) -> dict[str, Any]:
    path = frozen_path or (row or {}).get(path_field) or ""
    content_hash = frozen_hash or ""
    if not content_hash and path:
        resolved = resolve_asset_path(data_dir, path)
        if resolved and resolved.is_file():
            content_hash = file_sha256(resolved)
    return {
        "material_id": (row or {}).get("id"),
        "name": (row or {}).get("name") or (row or {}).get("title") or "",
        "path": path,
        "content_hash": content_hash,
        "description": frozen_description or (row or {}).get(desc_field) or "",
    }


def build_plan(rows: Mapping[str, list[dict[str, Any]]], data_dir: Path) -> MigrationPlan:
    """把旧结构行转换为影像库新行；不做写入，不做模型调用。"""

    plan = MigrationPlan()
    diagnostics = plan.diagnostics
    diagnostics.setdefault("notes", [])
    notes: list[str] = diagnostics["notes"]

    outfits = {row["id"]: row for row in rows["outfits"]}
    scenes = {row["id"]: row for row in rows["scenes"]}
    avatars = {(row["user_id"]): row for row in rows["avatars"]}
    cards = {(row["user_id"]): row for row in rows["cards"]}
    sets = {row["id"]: row for row in rows["sets"]}

    # ---- 动作素材：既有定义保留原 ID，预设动作登记新行；只合并内容完全一致的定义。----
    candidates: list[dict[str, Any]] = []
    for row in rows["definitions"]:
        asset = build_action_asset(row, source=row.get("source") or "user_requested")
        asset["_existing_id"] = row["id"]
        asset["_preset_key"] = None
        candidates.append(asset)
    seen_presets: set[tuple[Any, ...]] = set()
    for row in rows["set_actions"]:
        if not (row.get("preset") and not row.get("definition_id")):
            continue
        key = (row.get("user_id"), row.get("key"))
        if key in seen_presets:
            continue
        seen_presets.add(key)
        asset = build_action_asset(
            {
                **row,
                "motion_description": row.get("description") or "",
                "use_when_json": row.get("use_when_json"),
                "avoid_when_json": row.get("avoid_when_json"),
                "reason": "",
                "review_reason": "",
                "review_source_json": None,
            },
            source="preset",
            preset_key=row.get("key"),
        )
        asset["_existing_id"] = None
        asset["_preset_key"] = row.get("key")
        candidates.append(asset)

    kept: dict[tuple[Any, ...], dict[str, Any]] = {}
    dropped_duplicates: list[int] = []
    for asset in candidates:
        key = (
            asset["user_id"],
            asset["name"].strip(),
            asset["motion_description"].strip(),
            tuple(_canon_list(asset["use_when_json"])),
            tuple(_canon_list(asset["avoid_when_json"])),
            asset["clip_kind"],
            asset["duration_seconds"],
        )
        if key in kept:
            if asset.get("_existing_id"):
                dropped_duplicates.append(asset["_existing_id"])
            continue
        kept[key] = asset
    for asset in kept.values():
        row = {k: v for k, v in asset.items() if not k.startswith("_")}
        row["_index"] = len(plan.action_assets)
        asset["_index"] = row["_index"]
        row["_existing_id"] = asset.get("_existing_id")
        plan.action_assets.append(row)
    if dropped_duplicates:
        notes.append(f"合并内容完全一致的重复动作定义：{dropped_duplicates}")
    diagnostics["dropped_definition_ids"] = dropped_duplicates

    def action_index(user_id: Any, row: Mapping[str, Any]) -> int | None:
        """旧动作行对应的新动作素材在 plan.action_assets 中的下标。"""

        if row.get("definition_id"):
            for entry in plan.action_assets:
                if entry.get("_existing_id") == row["definition_id"]:
                    return entry["_index"]
            original = next((entry for entry in rows["definitions"] if entry["id"] == row["definition_id"]), None)
            canonical = kept.get(action_content_key(original)) if original is not None else None
            if canonical is not None:
                return canonical["_index"]
            return None
        if row.get("preset") and not row.get("definition_id"):
            probe = {
                "user_id": user_id,
                "name": (row.get("name") or "").strip(),
                "motion_description": (row.get("description") or "").strip(),
                "use_when_json": row.get("use_when_json"),
                "avoid_when_json": row.get("avoid_when_json"),
                "clip_kind": row.get("kind") or "once",
                "duration_seconds": row.get("duration_seconds"),
            }
            key = action_content_key(probe)
            asset = kept.get(key)
            return asset.get("_index") if asset else None
        return None

    def resolve_action_ref(row: Mapping[str, Any]) -> tuple[dict[str, Any], int | None]:
        index = action_index(row.get("user_id"), row)
        ref = action_ref_from_asset(row, {})
        # 冻结引用里的动作 ID 由写入方按插入结果回填。
        ref["action_id"] = None
        return ref, index

    # ---- 影像成品与制作工作项 ----
    image_items_by_content: dict[tuple[Any, str], int] = {}
    job_generation_ids: dict[str, int] = {}
    missing_files: list[str] = []

    def _hash_or_missing(path: str | None) -> tuple[str, bool]:
        resolved = resolve_asset_path(data_dir, path)
        if not path or not resolved or not resolved.is_file():
            missing_files.append(str(path or ""))
            return "", False
        return file_sha256(resolved), True

    def combo_for(
        set_row: Mapping[str, Any],
        action_ref: dict[str, Any],
        plan_json: Any,
        requirements: str,
    ) -> dict[str, Any]:
        ctx = _load_json(set_row.get("context_json")) or {}
        user_id = set_row.get("user_id")
        avatar = avatars.get(user_id)
        card = cards.get(user_id)
        if set_row.get("source") == "ai" and ctx.get("identity_path"):
            outfit_ref = _material_from_row(
                outfits.get(set_row.get("outfit_id")),
                path_field="fullbody_url",
                desc_field="description",
                data_dir=data_dir,
                frozen_path=ctx.get("outfit_path"),
                frozen_hash=ctx.get("outfit_hash"),
                frozen_description=ctx.get("outfit_description") or "",
            )
            scene_ref = _material_from_row(
                scenes.get(set_row.get("scene_id")),
                path_field="media_path",
                desc_field="description",
                data_dir=data_dir,
                frozen_path=ctx.get("scene_path"),
                frozen_hash=ctx.get("scene_hash"),
                frozen_description=ctx.get("scene_description") or "",
            )
            return combo_snapshot(
                identity=_identity_dict(card),
                identity_path=ctx.get("identity_path") or "",
                identity_hash=ctx.get("identity_hash") or "",
                portrait_path=ctx.get("portrait_path") or "",
                portrait_hash=ctx.get("portrait_hash") or "",
                outfit=outfit_ref,
                scene=scene_ref,
                action=action_ref,
                plan=plan_json,
                target_width=int(set_row.get("target_width") or 0) or 1,
                target_height=int(set_row.get("target_height") or 0) or 1,
                requirements=requirements,
                persona=ctx.get("persona") or {},
            )
        # 上传桌景：按一次性确认补全来源；原上传来源事实记录在迁移报告中。
        override = UPLOAD_SET_MATERIAL_OVERRIDES.get(int(set_row.get("id") or 0))
        outfit_row = outfits.get(override["outfit_id"]) if override else None
        scene_row = scenes.get(override["scene_id"]) if override else None
        if override and (not outfit_row or not scene_row):
            raise RuntimeError(f"一次性来源补全的素材不存在：set={set_row.get('id')} override={override}")
        if outfit_row and outfit_row.get("user_id") != user_id or scene_row and scene_row.get("user_id") != user_id:
            raise RuntimeError("一次性来源映射中的素材不属于桌景账户")
        notes.append(
            f"桌景 {set_row.get('id')} 原来源为 {set_row.get('source')}，"
            f"按一次性映射关联衣柜 {override['outfit_id'] if override else None} 与环境 {override['scene_id'] if override else None}",
        )
        outfit_ref = _material_from_row(
            outfit_row,
            path_field="fullbody_url",
            desc_field="description",
            data_dir=data_dir,
        )
        scene_ref = _material_from_row(
            scene_row,
            path_field="media_path",
            desc_field="description",
            data_dir=data_dir,
        )
        identity_path = (avatar or {}).get("seed_fullbody_url") or (avatar or {}).get("asset_url") or ""
        identity_hash, _ = _hash_or_missing(identity_path)
        portrait_path = (avatar or {}).get("asset_url") or ""
        portrait_hash, _ = _hash_or_missing(portrait_path)
        return combo_snapshot(
            identity=_identity_dict(card),
            identity_path=identity_path,
            identity_hash=identity_hash or "0" * 64,
            portrait_path=portrait_path,
            portrait_hash=portrait_hash or "0" * 64,
            outfit=outfit_ref,
            scene=scene_ref,
            action=action_ref,
            plan=plan_json,
            target_width=int(set_row.get("target_width") or 0) or 1,
            target_height=int(set_row.get("target_height") or 0) or 1,
            requirements=requirements,
            persona={},
        )

    for row in rows["set_actions"]:
        set_row = sets.get(row.get("set_id"))
        if not set_row:
            continue
        override = UPLOAD_SET_MATERIAL_OVERRIDES.get(int(set_row.get("id") or 0))
        material_outfit_id = set_row.get("outfit_id") or (override or {}).get("outfit_id")
        material_scene_id = set_row.get("scene_id") or (override or {}).get("scene_id")
        action_ref, action_index_value = resolve_action_ref(row)
        accepted = _load_json(row.get("accepted_asset_json"))
        progress_raw = _load_json(row.get("generation_state_json"))
        combo_cache: dict[str, dict[str, Any]] = {}

        def combo(action_ref_local: dict[str, Any], plan_json: Any, requirements: str) -> dict[str, Any]:
            cache_key = _dump_json({"p": plan_json, "r": requirements})
            if cache_key not in combo_cache:
                combo_cache[cache_key] = combo_for(set_row, action_ref_local, plan_json, requirements)
            return combo_cache[cache_key]

        if accepted:
            asset = convert_accepted_asset(accepted)
            plan_json = asset["creation_plan"]
            requirements = asset["feedback"]
            snapshot = combo(action_ref, plan_json, requirements)
            snapshot_json = _dump_json(snapshot)
            image_temp_id: int | None = None
            if asset["input_image_path"]:
                image_hash, ok = _hash_or_missing(asset["input_image_path"])
                dedup_key = (set_row.get("user_id"), image_hash)
                if ok and dedup_key not in image_items_by_content:
                    plan.visual_items.append(
                        {
                            "user_id": set_row.get("user_id"),
                            "media_kind": "image",
                            "title": row.get("name") or "",
                            "status": "ready",
                            "media_path": asset["input_image_path"],
                            "poster_path": None,
                            "width": asset["width"] or 1,
                            "height": asset["height"] or 1,
                            "duration_ms": None,
                            "content_hash": image_hash,
                            "description": asset["visual_description"],
                            "snapshot_json": snapshot_json,
                            "version": 1,
                            "outfit_id": material_outfit_id,
                            "scene_id": material_scene_id,
                            "action_id": None,
                            "_action_index": action_index_value,
                            "input_image_id": None,
                            "input_image_path": None,
                            "input_image_hash": None,
                            "_old": f"action:{row.get('id')}:image",
                        },
                    )
                    image_temp_id = len(plan.visual_items) - 1
                    image_items_by_content[dedup_key] = image_temp_id
                    plan.item_mapping[f"action:{row.get('id')}:image"] = image_temp_id
                elif ok:
                    image_temp_id = image_items_by_content[dedup_key]
            if asset["media_kind"] == "video":
                video_hash, _ = _hash_or_missing(asset["media_path"])
                plan.visual_items.append(
                    {
                        "user_id": set_row.get("user_id"),
                        "media_kind": "video",
                        "title": row.get("name") or "",
                        "status": "ready",
                        "media_path": asset["media_path"],
                        "poster_path": asset["poster_path"] or None,
                        "width": asset["width"] or 1,
                        "height": asset["height"] or 1,
                        "duration_ms": asset["duration_ms"],
                        "content_hash": video_hash,
                        "description": asset["visual_description"],
                        "snapshot_json": snapshot_json,
                        "version": 1,
                        "outfit_id": material_outfit_id,
                        "scene_id": material_scene_id,
                        "action_id": None,
                        "_action_index": action_index_value,
                        "input_image_id": image_temp_id,
                        "input_image_path": asset["input_image_path"],
                        "input_image_hash": None,
                        "_old": f"action:{row.get('id')}:video",
                    },
                )
                if image_temp_id is not None:
                    plan.visual_items[-1]["input_image_hash"] = plan.visual_items[image_temp_id]["content_hash"]
                video_temp_id = len(plan.visual_items) - 1
                plan.item_mapping[f"action:{row.get('id')}:video"] = video_temp_id
                plan.action_mapping[f"action:{row.get('id')}"] = video_temp_id
            elif image_temp_id is not None:
                plan.action_mapping[f"action:{row.get('id')}"] = image_temp_id

        if not accepted and progress_raw:
            progress = convert_progress(progress_raw)
            candidate_path = candidate_image_path(progress_raw)
            image_temp_id = None
            if candidate_path:
                image_hash, ok = _hash_or_missing(candidate_path)
                dedup_key = (set_row.get("user_id"), image_hash)
                if ok and dedup_key not in image_items_by_content:
                    plan.visual_items.append(
                        {
                            "user_id": set_row.get("user_id"),
                            "media_kind": "image",
                            "title": row.get("name") or "",
                            "status": "ready",
                            "media_path": candidate_path,
                            "poster_path": None,
                            "width": int(progress_raw.get("frame_width") or 0) or 1,
                            "height": int(progress_raw.get("frame_height") or 0) or 1,
                            "duration_ms": None,
                            "content_hash": image_hash,
                            "description": str(progress_raw.get("visual_description") or ""),
                            "snapshot_json": _dump_json(
                                combo(
                                    action_ref,
                                    progress_raw.get("creation_plan"),
                                    str(progress_raw.get("feedback") or ""),
                                ),
                            ),
                            "version": 1,
                            "outfit_id": material_outfit_id,
                            "scene_id": material_scene_id,
                            "action_id": None,
                            "_action_index": action_index_value,
                            "input_image_id": None,
                            "input_image_path": None,
                            "input_image_hash": None,
                            "_old": f"action:{row.get('id')}:image",
                        },
                    )
                    image_temp_id = len(plan.visual_items) - 1
                    image_items_by_content[dedup_key] = image_temp_id
                    plan.item_mapping[f"action:{row.get('id')}:image"] = image_temp_id
                elif ok:
                    image_temp_id = image_items_by_content[dedup_key]

            state = "paused"
            if progress_raw.get("submission_unknown"):
                state = "unknown"
            elif not progress_raw.get("paused_by_user") and (row.get("error") or progress_raw.get("failure_reason")):
                state = "failed"
            job = {
                "user_id": set_row.get("user_id"),
                "target_visual_id": None,
                "purpose": "create",
                "target_kind": "video",
                "expected_version": None,
                "snapshot_json": _dump_json(
                    combo(
                        action_ref,
                        progress_raw.get("creation_plan"),
                        str(progress_raw.get("feedback") or ""),
                    ),
                ),
                "feedback": str(progress_raw.get("feedback") or ""),
                "video_feedback": str(progress_raw.get("video_feedback") or ""),
                "state": state,
                "stage": row.get("stage") or "prepare",
                "progress_json": _dump_json(progress) if progress else None,
                "candidates_json": _dump_json([progress.get("candidate")])
                if progress and progress.get("candidate")
                else None,
                "repair_count": int(progress_raw.get("repair_count") or 0),
                "repair_limit": 1,
                "error": row.get("error") or progress_raw.get("failure_reason") or None,
                "result_visual_id": None,
                "result_image_id": image_temp_id,
                "_action_index": action_index_value,
                "_old": f"action:{row.get('id')}",
            }
            if accepted and progress.get("generation_id"):
                job["purpose"] = "regenerate"
            plan.visual_jobs.append(job)
            job_temp_id = len(plan.visual_jobs) - 1
            plan.job_mapping[f"action:{row.get('id')}"] = job_temp_id
            if progress and progress.get("generation_id"):
                job_generation_ids[str(progress["generation_id"])] = job_temp_id
                video_chain = _load_json(progress.get("video_chain_json")) or {}
                if video_chain.get("generation_id"):
                    job_generation_ids[str(video_chain["generation_id"])] = job_temp_id
        elif (
            accepted
            and progress_raw
            and (row.get("status") != "ready" or row.get("stage") != "complete" or progress_raw.get("paused_by_user"))
        ):
            # 重做中的暂停工作项：目标是既有成品。
            progress = convert_progress(progress_raw)
            state = "paused"
            if progress_raw.get("submission_unknown"):
                state = "unknown"
            elif row.get("error") or progress_raw.get("failure_reason"):
                state = "failed"
            job = {
                "user_id": set_row.get("user_id"),
                "target_visual_id": plan.action_mapping.get(f"action:{row.get('id')}"),
                "purpose": "regenerate",
                "target_kind": "video",
                "expected_version": 1,
                "snapshot_json": _dump_json(
                    combo(
                        action_ref,
                        progress_raw.get("creation_plan"),
                        str(progress_raw.get("feedback") or ""),
                    ),
                ),
                "feedback": str(progress_raw.get("feedback") or ""),
                "video_feedback": str(progress_raw.get("video_feedback") or ""),
                "state": state,
                "stage": row.get("stage") or "prepare",
                "progress_json": _dump_json(progress) if progress else None,
                "candidates_json": _dump_json([progress.get("candidate")])
                if progress and progress.get("candidate")
                else None,
                "repair_count": int(progress_raw.get("repair_count") or 0),
                "repair_limit": 1,
                "error": row.get("error") or progress_raw.get("failure_reason") or None,
                "result_visual_id": None,
                "result_image_id": None,
                "_action_index": action_index_value,
                "_old": f"action:{row.get('id')}",
            }
            plan.visual_jobs.append(job)
            job_temp_id = len(plan.visual_jobs) - 1
            plan.job_mapping[f"action:{row.get('id')}"] = job_temp_id
            if progress:
                video_chain = _load_json(progress.get("video_chain_json")) or {}
                if video_chain.get("generation_id"):
                    job_generation_ids[str(video_chain["generation_id"])] = job_temp_id
            if progress and progress.get("generation_id"):
                job_generation_ids[str(progress["generation_id"])] = job_temp_id

    # ---- 呈现状态 ----
    for row in rows["states"]:
        current = (
            plan.action_mapping.get(f"action:{row.get('selected_action_id')}")
            if row.get("selected_action_id")
            else None
        )
        loop = plan.action_mapping.get(f"action:{row.get('loop_action_id')}") if row.get("loop_action_id") else None
        plan.presentation_rows.append(
            {
                "user_id": row.get("user_id"),
                "current_visual_id": current,
                "loop_visual_id": loop,
                "pinned": bool(row.get("pinned")),
                "autonomous_enabled": bool(row.get("autonomous_enabled")),
                "target_width": row.get("target_width"),
                "target_height": row.get("target_height"),
                "presentation_epoch": int(row.get("set_epoch") or 0),
                "state_revision": int(row.get("version") or 0),
                "pending_command_json": None,
            },
        )
        if row.get("selected_action_id") and current is None:
            notes.append(f"呈现状态指向的旧动作 {row.get('selected_action_id')} 无可播放成品，当前影像置空")

    # ---- 队列、配置与夜间 ----
    plan.queue_retargets = queue_retarget_rows(rows["queue"], job_generation_ids)
    for entry in plan.queue_retargets:
        if entry["entity_id"] is None:
            notes.append(f"视频队列请求 {entry['id']} 无匹配工作项，保留终态等待独立收尾")
    pending_queue = [
        row["id"] for row in rows["queue"] if row.get("state") not in {"succeeded", "failed", "cancelled", "stopped"}
    ]
    diagnostics["pending_queue_requests"] = pending_queue
    if pending_queue:
        notes.append(f"存在未决视频队列提交：{pending_queue}，不得清空后重投，须先查明")

    plan.nightly_retired = nightly_retired_rows(rows["nightly"])

    old_actions = {int(row["id"]): row for row in rows["set_actions"]}
    for item in plan.visual_items:
        old = old_actions[int(item["_old"].split(":")[1])]
        accepted = _load_json(old.get("accepted_asset_json")) or {}
        progress = _load_json(old.get("generation_state_json")) or {}
        item["outfit_description"] = str(accepted.get("outfit_description") or progress.get("outfit_description") or "")
    if missing_files:
        diagnostics["missing_files"] = sorted(set(missing_files))
    diagnostics["notes"] = notes
    return plan


def resolve_action_ids(plan: MigrationPlan, inserted_ids: Mapping[int, int]) -> None:
    """按动作素材的实际 ID 回填影像行与冻结快照中的动作引用。

    `inserted_ids` 是 `plan.action_assets` 下标到实际动作素材 ID 的映射（保留的定义用原 ID，
    新登记的预设动作用插入返回的 ID）。回填后行内的 `_` 前缀键由写入方剥离。
    """

    for item in plan.visual_items:
        index = item.pop("_action_index", None)
        if index is None:
            continue
        real = inserted_ids.get(index)
        item["action_id"] = real
        snapshot = _load_json(item.get("snapshot_json"))
        if isinstance(snapshot, dict):
            action = dict(snapshot.get("action") or {})
            action["action_id"] = real
            snapshot["action"] = action
            item["snapshot_json"] = _dump_json(snapshot)
    for job in plan.visual_jobs:
        index = job.pop("_action_index", None)
        if index is None:
            continue
        real = inserted_ids.get(index)
        snapshot = _load_json(job.get("snapshot_json"))
        if isinstance(snapshot, dict):
            action = dict(snapshot.get("action") or {})
            action["action_id"] = real
            snapshot["action"] = action
            job["snapshot_json"] = _dump_json(snapshot)


def public_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """剥离 `_` 前缀的内部键，得到可写入数据库的行。"""

    return [{k: v for k, v in row.items() if not k.startswith("_")} for row in rows]
