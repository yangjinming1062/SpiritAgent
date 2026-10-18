"""共享动作定义和独立目标制作。"""

import base64
import hashlib
import json
import logging
import unicodedata
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from infrastructure.assets.asset_store import resolve_asset_reference

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "action_definitions",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("outfit_id", sa.Integer(), nullable=True),
        sa.Column("source_visual_key", sa.String(64), nullable=False),
        sa.Column("normalized_name", sa.String(128), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("motion_description", sa.Text(), nullable=False),
        sa.Column("use_when_json", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("avoid_when_json", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("clip_kind", sa.String(8), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("desktop_continuity", sa.String(16), server_default=sa.text("'independent'"), nullable=False),
        sa.Column(
            "desktop_allowed_triggers_json",
            sa.Text(),
            server_default=sa.text('\'["chat", "manual"]\''),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("review_reason", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("review_source_json", sa.Text(), nullable=False),
        sa.Column("origin_kind", sa.String(16), nullable=False),
        sa.Column("origin_target_id", sa.Integer(), nullable=False),
        sa.Column("reuse_definition_id", sa.Integer(), nullable=True),
        sa.Column("fanout_pending", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["outfit_id"], ["companion_outfits.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reuse_definition_id"], ["action_definitions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "outfit_id",
            "source_visual_key",
            "normalized_name",
            name="uq_action_definition_name",
        ),
    )
    op.create_index("ix_action_definitions_user_id", "action_definitions", ["user_id"])
    op.create_index("ix_action_definitions_status", "action_definitions", ["status"])
    op.create_table(
        "action_sync_targets",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("definition_id", sa.Integer(), nullable=False),
        sa.Column("target_kind", sa.String(16), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("action_id", sa.Integer(), nullable=True),
        sa.Column("automatic", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("state", sa.String(16), server_default=sa.text("'manual'"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["definition_id"], ["action_definitions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("definition_id", "target_kind", "target_id", name="uq_action_sync_target"),
        sa.CheckConstraint("target_kind IN ('window', 'desktop')", name="ck_action_sync_target_kind"),
    )
    op.create_index("ix_action_sync_targets_user_id", "action_sync_targets", ["user_id"])
    op.create_index("ix_action_sync_targets_definition_id", "action_sync_targets", ["definition_id"])
    op.create_index("ix_action_sync_targets_state", "action_sync_targets", ["state"])
    op.add_column(
        "companion_action_packs",
        sa.Column("source_visual_key", sa.String(64), server_default=sa.text("''"), nullable=False),
    )
    op.add_column(
        "desktop_video_sets",
        sa.Column("source_visual_key", sa.String(64), server_default=sa.text("''"), nullable=False),
    )
    for table, container, unique in (
        ("companion_actions", "pack_id", "uq_companion_actions_pack_definition"),
        ("desktop_video_actions", "set_id", "uq_desktop_video_actions_definition"),
    ):
        op.add_column(table, sa.Column("definition_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_definition_id",
            table,
            "action_definitions",
            ["definition_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_unique_constraint(unique, table, [container, "definition_id"])
        op.create_index(f"ix_{table}_definition_id", table, ["definition_id"])
    _upgrade_existing_actions(op.get_bind())
    op.drop_table("action_proposals")
    op.drop_table("desktop_video_proposals")
    op.drop_table("action_creations")
    op.execute(
        sa.text("DELETE FROM system_settings WHERE setting_key IN (:autonomous, :requested)").bindparams(
            autonomous="action_autonomous_create_daily_limit",
            requested="action_user_requested_create_daily_limit",
        ),
    )


def _upgrade_existing_actions(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    names = (
        "companion_action_packs",
        "companion_actions",
        "desktop_video_sets",
        "desktop_video_actions",
        "companion_outfits",
        "avatar_assets",
        "action_proposals",
        "desktop_video_proposals",
        "action_definitions",
        "action_sync_targets",
    )
    tables = {name: sa.Table(name, metadata, autoload_with=connection) for name in names}
    packs = _rows(connection, tables["companion_action_packs"])
    suites = _rows(connection, tables["desktop_video_sets"])
    outfits = {row["id"]: row for row in _rows(connection, tables["companion_outfits"])}
    avatars = {row["id"]: row for row in _rows(connection, tables["avatar_assets"])}
    window_proposals = _rows(connection, tables["action_proposals"])
    desktop_proposals = _rows(connection, tables["desktop_video_proposals"])
    sources: dict[tuple[str, int], dict[str, Any]] = {}
    for suite in suites:
        source = _desktop_source(suite)
        sources[("desktop", suite["id"])] = source
        connection.execute(
            tables["desktop_video_sets"]
            .update()
            .where(tables["desktop_video_sets"].c.id == suite["id"])
            .values(source_visual_key=source["source_visual_key"]),
        )
    for pack in packs:
        context = _object(pack.get("context_json"))
        identity = context.get("identity", {})
        source = _window_source(
            pack,
            context,
            outfits.get(pack["outfit_id"]),
            avatars.get(identity.get("avatar_id")),
        )
        sources[("window", pack["id"])] = source
        connection.execute(
            tables["companion_action_packs"]
            .update()
            .where(tables["companion_action_packs"].c.id == pack["id"])
            .values(source_visual_key=source["source_visual_key"]),
        )
    definitions: dict[tuple[object, ...], dict[str, Any]] = {}
    linked: dict[tuple[int, str, int], int] = {}
    actions = {
        "window": _rows(connection, tables["companion_actions"]),
        "desktop": _rows(connection, tables["desktop_video_actions"]),
    }
    proposal_lists = {"window": window_proposals, "desktop": desktop_proposals}
    for kind, rows in actions.items():
        action_table = tables["companion_actions" if kind == "window" else "desktop_video_actions"]
        container_field = "pack_id" if kind == "window" else "set_id"
        for action in rows:
            if action["system_slot"] if kind == "window" else action["preset"]:
                continue
            source = sources[(kind, action[container_field])]
            # 外部静态素材没有已冻结的动作设计，继续由本地素材条目拥有。
            if kind == "window" and not action.get("source_design_json") and not action.get("motion_description"):
                continue
            proposal = next(
                (
                    row
                    for row in reversed(proposal_lists[kind])
                    if row.get("action_id") == action["id"]
                    and row[container_field] == action[container_field]
                    and row["user_id"] == action["user_id"]
                ),
                {},
            )
            design = _action_design(kind, action, proposal)
            scope = _definition_scope(source, design["name"])
            definition = definitions.get(scope)
            if definition is None:
                definition = _insert_definition(
                    connection,
                    tables["action_definitions"],
                    source,
                    design,
                    proposal.get("source", "user_requested"),
                    "approved",
                    proposal.get("review_reason") or "",
                )
                definitions[scope] = definition
            elif any(
                definition[field] != design[field]
                for field in (
                    "motion_description",
                    "use_when_json",
                    "avoid_when_json",
                    "clip_kind",
                    "duration_seconds",
                )
            ):
                raise RuntimeError(f"同着装同名动作的冻结设计冲突，请先核对 {kind} 动作 {action['id']}")
            key = (definition["id"], kind, source["target_id"])
            if key in linked:
                raise RuntimeError(f"同一目标存在重复同名动作，请先核对 {kind} 动作 {action['id']}")
            if kind == "desktop" and not any(item[0] == definition["id"] and item[1] == "desktop" for item in linked):
                desktop_values = {
                    field: design[field]
                    for field in (
                        "desktop_continuity",
                        "desktop_allowed_triggers_json",
                    )
                }
                connection.execute(
                    tables["action_definitions"]
                    .update()
                    .where(tables["action_definitions"].c.id == definition["id"])
                    .values(**desktop_values),
                )
                definition.update(desktop_values)
            action_values: dict[str, Any] = {"definition_id": definition["id"]}
            if (
                kind == "desktop"
                and action["status"] == "queued"
                and not action.get("generation_state_json")
                and not action.get("accepted_asset_json")
            ):
                # 放弃首次草稿也回到 queued；无任务或成品的旧条目需保留手动首次制作入口。
                action_values.update(status="not_generated", stage="prepare", error=None)
            connection.execute(
                action_table.update().where(action_table.c.id == action["id"]).values(**action_values),
            )
            linked[key] = action["id"]
            _insert_target(connection, tables["action_sync_targets"], definition, source, action["id"], "dispatched")
    for definition in definitions.values():
        origin = sources[(definition["origin_kind"], definition["origin_target_id"])]
        for source in sources.values():
            if not _matching_sources(origin, source):
                continue
            target_key = (definition["id"], source["target_kind"], source["target_id"])
            if target_key in linked:
                continue
            if source["target_kind"] == "window":
                pack = next(row for row in packs if row["id"] == source["target_id"])
                action_id = _insert_window_placeholder(connection, tables["companion_actions"], definition, pack)
            else:
                action_id = _insert_desktop_placeholder(connection, tables["desktop_video_actions"], definition, source)
            linked[target_key] = action_id
            _insert_target(connection, tables["action_sync_targets"], definition, source, action_id, "manual")
    # 已有动作优先；没有成品的同名历史提案保留最后一次决定，不重启旧评审或制作。
    for kind, proposals in proposal_lists.items():
        container_field = "pack_id" if kind == "window" else "set_id"
        for proposal in reversed(proposals):
            if proposal["status"] in {"approved", "reused"}:
                continue
            source = sources.get((kind, proposal[container_field]))
            if source is None or source["user_id"] != proposal["user_id"]:
                raise RuntimeError("历史动作提案的目标归属不一致")
            design = _proposal_design(kind, proposal)
            scope = _definition_scope(source, design["name"])
            if scope in definitions:
                continue
            status = "rejected" if proposal["status"] == "rejected" else "deferred"
            reason = proposal.get("review_reason") or ""
            if status == "deferred":
                reason = "原动作设计尚未完成，已保留设计；请手动重新提交评审。" + reason
            definition = _insert_definition(
                connection,
                tables["action_definitions"],
                source,
                design,
                proposal["source"],
                status,
                reason,
            )
            definitions[scope] = definition
    _validate_converted_actions(connection, tables, linked)


def _rows(connection: sa.Connection, table: sa.Table) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(sa.select(table).order_by(table.c.id)).mappings()]


def _object(raw: str | None) -> dict[str, Any]:
    value = json.loads(raw or "{}")
    if not isinstance(value, dict):
        raise RuntimeError("动作冻结资料必须是 JSON 对象")
    return value


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _visual_key(identity_hash: str, portrait_hash: str, outfit_hash: str, role: str) -> str:
    if not all(isinstance(value, str) and len(value) == 64 for value in (identity_hash, portrait_hash, outfit_hash)):
        raise RuntimeError("桌景冻结资料缺少有效的原始参考哈希")
    if role not in {"portrait", "fullbody"}:
        raise RuntimeError("动作冻结资料的身份参考职责无效")
    return hashlib.sha256(
        json.dumps([identity_hash, portrait_hash, outfit_hash, role], separators=(",", ":")).encode(),
    ).hexdigest()


def _read_asset(reference: str | None) -> tuple[bytes, str] | None:
    resolved = resolve_asset_reference(reference)
    if resolved is None:
        return None
    try:
        return resolved[0].read_bytes(), resolved[1]
    except OSError:
        return None


def _desktop_source(suite: dict[str, Any]) -> dict[str, Any]:
    snapshot = _object(suite["context_json"])
    role = snapshot.get("identity_reference_role", "portrait")
    key = _visual_key(snapshot["identity_hash"], snapshot["portrait_hash"], snapshot["outfit_hash"], role)
    references = [
        snapshot["identity_path"] if role == "fullbody" else snapshot["portrait_path"],
        snapshot["outfit_path"],
        snapshot["scene_path"],
    ]
    reason = None
    for path_field, hash_field in (
        ("identity_path", "identity_hash"),
        ("portrait_path", "portrait_hash"),
        ("outfit_path", "outfit_hash"),
        ("scene_path", "scene_hash"),
    ):
        loaded = _read_asset(snapshot[path_field])
        if loaded is None or hashlib.sha256(loaded[0]).hexdigest() != snapshot[hash_field]:
            reason = "桌景原始冻结参考缺失或已变化，已保留成品；请恢复原素材后制作"
            break
    return {
        "user_id": suite["user_id"],
        "outfit_id": suite["outfit_id"],
        "target_kind": "desktop",
        "target_id": suite["id"],
        "label": suite["title"],
        "source_visual_key": key,
        "ready": reason is None,
        "blocked_reason": reason,
        "reference_paths": references,
        "reference_roles": [role, "outfit", "scene"],
        "identity": snapshot["identity"],
        "outfit_description": snapshot.get("outfit_description", ""),
        "persona": snapshot.get("persona", {}),
    }


def _window_source(
    pack: dict[str, Any],
    context: dict[str, Any],
    outfit: dict[str, Any] | None,
    avatar: dict[str, Any] | None,
) -> dict[str, Any]:
    isolated = hashlib.sha256(
        f"unmatched-window-source|{pack['user_id']}|{pack['id']}|{pack['reference_hash']}".encode(),
    ).hexdigest()
    key, reason = isolated, "动作组原始参考无法核验，已保留成品；当前动作组独立保留，不同步到其他画面"
    identity = _read_asset(context.get("identity_reference_path"))
    styling = _read_asset(outfit["fullbody_url"]) if outfit else None
    portrait = _read_asset(avatar["asset_url"]) if avatar else None
    if (
        identity is not None
        and styling is not None
        and portrait is not None
        and outfit["user_id"] == pack["user_id"]
        and avatar["user_id"] == pack["user_id"]
        and pack["avatar_id"] in {None, avatar["id"]}
    ):
        uri = f"data:{styling[1]};base64,{base64.b64encode(styling[0]).decode()}"
        reference_hash = hashlib.sha256(
            "|".join(
                (
                    str(pack["outfit_id"]) if pack["outfit_id"] is not None else "",
                    outfit["fullbody_url"],
                    str(avatar["id"]),
                    uri,
                ),
            ).encode(),
        ).hexdigest()
        if reference_hash == pack["reference_hash"]:
            key = _visual_key(
                hashlib.sha256(identity[0]).hexdigest(),
                hashlib.sha256(portrait[0]).hexdigest(),
                hashlib.sha256(styling[0]).hexdigest(),
                context.get("identity_reference_role", "portrait"),
            )
            current_identity = _read_asset(avatar["seed_fullbody_url"])
            reason = (
                None
                if current_identity is not None
                and current_identity[0] == identity[0]
                and context.get("reference_alignment") == "ready"
                and _read_asset(pack["reference_path"]) is not None
                else "动作组冻结身份或对齐参考尚不可用于制作，已保留原成品"
            )
    if key == isolated:
        logging.getLogger(__name__).warning("动作组 %s 的原始来源不可核验，仅保留独立关联", pack["id"])
    return {
        "user_id": pack["user_id"],
        "outfit_id": pack["outfit_id"],
        "target_kind": "window",
        "target_id": pack["id"],
        "label": context.get("outfit_description", ""),
        "source_visual_key": key,
        "ready": reason is None,
        "blocked_reason": reason,
        "reference_paths": [pack["reference_path"]] if pack["reference_path"] else [],
        "reference_roles": ["完整角色与当前着装，以冻结图像为准"] if pack["reference_path"] else [],
        "identity": context.get("identity"),
        "outfit_description": context.get("outfit_description", ""),
        "persona": context.get("persona_definition", {}),
    }


def _proposal_design(kind: str, proposal: dict[str, Any]) -> dict[str, Any]:
    raw = _object(proposal["design_json"])
    return _checked_design(
        {
            "name": raw.get("name", ""),
            "motion_description": raw.get("motion_description", ""),
            "use_when_json": _json(raw.get("use_when", [])),
            "avoid_when_json": _json(raw.get("avoid_when", [])),
            "clip_kind": raw.get("clip_kind" if kind == "window" else "kind", "once"),
            "duration_seconds": raw.get("duration_seconds", 6),
            "reason": raw.get("reason") or proposal.get("reason") or "",
            "desktop_continuity": raw.get("continuity", "independent") if kind == "desktop" else "independent",
            "desktop_allowed_triggers_json": _json(raw.get("allowed_triggers", ["chat", "manual"]))
            if kind == "desktop"
            else '["chat", "manual"]',
        },
    )


def _action_design(kind: str, action: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
    raw = _object(action.get("source_design_json") or proposal.get("design_json"))
    return _checked_design(
        {
            "name": action["name"],
            "motion_description": action["motion_description"] if kind == "window" else action["description"],
            "use_when_json": action["use_when" if kind == "window" else "use_when_json"],
            "avoid_when_json": action["avoid_when" if kind == "window" else "avoid_when_json"],
            "clip_kind": action["kind"] or raw.get("clip_kind", "once"),
            "duration_seconds": action["target_duration_seconds"] or raw.get("duration_seconds", 6),
            "reason": raw.get("reason") or proposal.get("reason") or "",
            "desktop_continuity": action["continuity"] if kind == "desktop" else "independent",
            "desktop_allowed_triggers_json": action["allowed_triggers_json"]
            if kind == "desktop"
            else '["chat", "manual"]',
        },
    )


def _checked_design(design: dict[str, Any]) -> dict[str, Any]:
    duration = design["duration_seconds"]
    if (
        not isinstance(design["name"], str)
        or not 1 <= len(design["name"].strip()) <= 64
        or not isinstance(design["motion_description"], str)
        or not 10 <= len(design["motion_description"].strip()) <= 600
        or "\x00" in design["name"]
        or "\x00" in design["motion_description"]
        or design["clip_kind"] not in {"once", "loop"}
        or type(duration) not in {int, float}
        or not 1 <= duration <= 15
        or duration != int(duration)
        or design["desktop_continuity"] not in {"independent", "base"}
    ):
        raise RuntimeError("原自定义动作缺少有效的冻结设计，请先核对后再整理")
    design["name"], design["motion_description"] = design["name"].strip(), design["motion_description"].strip()
    design["duration_seconds"] = int(duration)
    design["reason"] = str(design["reason"])[:400]
    for field in ("use_when_json", "avoid_when_json"):
        values = json.loads(design[field])
        if (
            not isinstance(values, list)
            or len(values) > 8
            or any(not isinstance(value, str) or not value.strip() or len(value) > 120 for value in values)
        ):
            raise RuntimeError("原动作的适用条件无效，请先核对后再整理")
        design[field] = _json(values)
    triggers = json.loads(design["desktop_allowed_triggers_json"])
    if (
        not isinstance(triggers, list)
        or not triggers
        or any(
            not isinstance(value, str) or value not in {"periodic", "user_return", "chat", "manual"}
            for value in triggers
        )
        or len(set(triggers)) != len(triggers)
    ):
        raise RuntimeError("原桌景动作的触发条件无效")
    design["desktop_allowed_triggers_json"] = _json(triggers)
    return design


def _definition_scope(source: dict[str, Any], name: str) -> tuple[object, ...]:
    normalized = unicodedata.normalize("NFKC", name).strip().casefold()
    scope = (source["user_id"], source["outfit_id"], source["source_visual_key"], normalized)
    return scope if source["outfit_id"] is not None else (*scope, source["target_kind"], source["target_id"])


def _matching_sources(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        (left["target_kind"], left["target_id"]) == (right["target_kind"], right["target_id"])
        or left["outfit_id"] is not None
        and all(left[field] == right[field] for field in ("user_id", "outfit_id", "source_visual_key"))
    )


def _insert_definition(
    connection: sa.Connection,
    table: sa.Table,
    source: dict[str, Any],
    design: dict[str, Any],
    origin: str,
    status: str,
    reason: str,
) -> dict[str, Any]:
    values = {
        **design,
        "user_id": source["user_id"],
        "outfit_id": source["outfit_id"],
        "source_visual_key": source["source_visual_key"],
        "normalized_name": _definition_scope(source, design["name"])[3],
        "source": "autonomous" if origin == "autonomous" else "user_requested",
        "status": status,
        "review_reason": reason,
        "review_source_json": _json(source),
        "origin_kind": source["target_kind"],
        "origin_target_id": source["target_id"],
        "fanout_pending": False,
    }
    identifier = connection.execute(table.insert().values(**values).returning(table.c.id)).scalar_one()
    return {**values, "id": identifier}


def _insert_target(
    connection: sa.Connection,
    table: sa.Table,
    definition: dict[str, Any],
    source: dict[str, Any],
    action_id: int,
    state: str,
) -> None:
    connection.execute(
        table.insert().values(
            user_id=definition["user_id"],
            definition_id=definition["id"],
            target_kind=source["target_kind"],
            target_id=source["target_id"],
            action_id=action_id,
            automatic=False,
            state=state,
            reason=source["blocked_reason"],
        ),
    )


def _insert_window_placeholder(
    connection: sa.Connection,
    table: sa.Table,
    definition: dict[str, Any],
    pack: dict[str, Any],
) -> int:
    design = {
        "name": definition["name"],
        "motion_description": definition["motion_description"],
        "use_when": json.loads(definition["use_when_json"]),
        "avoid_when": json.loads(definition["avoid_when_json"]),
        "clip_kind": definition["clip_kind"],
        "duration_seconds": definition["duration_seconds"],
        "reason": definition["reason"] or "沿用已登记的同着装动作设计",
        "expected_pack_id": pack["id"],
    }
    return connection.execute(
        table.insert()
        .values(
            user_id=pack["user_id"],
            pack_id=pack["id"],
            outfit_id=pack["outfit_id"],
            definition_id=definition["id"],
            key=f"shared_{definition['id']}",
            name=definition["name"],
            motion_description=definition["motion_description"],
            media_type="video",
            kind=definition["clip_kind"],
            target_duration_seconds=definition["duration_seconds"],
            loopable=definition["clip_kind"] == "loop",
            use_when=definition["use_when_json"],
            avoid_when=definition["avoid_when_json"],
            status="not_generated",
            stage="design",
            reference_hash=pack["reference_hash"],
            source_design_json=_json(design),
        )
        .returning(table.c.id),
    ).scalar_one()


def _insert_desktop_placeholder(
    connection: sa.Connection,
    table: sa.Table,
    definition: dict[str, Any],
    source: dict[str, Any],
) -> int:
    return connection.execute(
        table.insert()
        .values(
            user_id=source["user_id"],
            set_id=source["target_id"],
            definition_id=definition["id"],
            key=f"shared_{definition['id']}",
            name=definition["name"],
            description=definition["motion_description"],
            preset=False,
            kind=definition["clip_kind"],
            duration_seconds=definition["duration_seconds"],
            target_duration_seconds=definition["duration_seconds"],
            continuity=definition["desktop_continuity"],
            allowed_triggers_json=definition["desktop_allowed_triggers_json"],
            use_when_json=definition["use_when_json"],
            avoid_when_json=definition["avoid_when_json"],
            status="not_generated",
            stage="prepare",
        )
        .returning(table.c.id),
    ).scalar_one()


def _validate_converted_actions(
    connection: sa.Connection,
    tables: dict[str, sa.Table],
    linked: dict[tuple[int, str, int], int],
) -> None:
    for (definition_id, kind, target_id), action_id in linked.items():
        table = tables["companion_actions" if kind == "window" else "desktop_video_actions"]
        container = table.c.pack_id if kind == "window" else table.c.set_id
        row = connection.execute(sa.select(table).where(table.c.id == action_id)).mappings().one()
        if row["definition_id"] != definition_id or row[container.name] != target_id:
            raise RuntimeError("动作共享定义或目标关联校验失败")
        if row["status"] == "not_generated" and any(
            row.get(field)
            for field in (
                "generation_state_json",
                "pose_generation_state_json",
                "provider_task_id",
                "accepted_asset_json",
                "result_json",
                "script_json",
                "media_path",
                "artifact_path",
                "pose_path",
            )
        ):
            raise RuntimeError("待制作占位不能包含供应商任务或既有成品")


def downgrade() -> None:
    raise RuntimeError("共享动作整理不可逆，请从升级前的数据库备份恢复")
