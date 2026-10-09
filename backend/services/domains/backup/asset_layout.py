"""备份导入的资产归属规划；同一素材可为不同拥有者目录复制。"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from services.domains.assets import asset_paths
from services.infrastructure.assets import (
    dated_asset_directory,
    outfit_asset_directory,
    pack_asset_directory,
    scene_asset_directory,
)


@dataclass
class AssetLayout:
    mapping: dict[str, str] = field(default_factory=dict)
    rows: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)
    # 以目标为键，允许一个源文件复制到多个拥有者目录。
    copies: dict[str, str] = field(default_factory=dict)
    directories: dict[tuple[str, str], str] = field(default_factory=dict)


def plan_asset_layout(
    rows: dict[str, list[dict[str, Any]]],
    source_user_id: int,
    target_user_id: int,
    timezone: str,
    *,
    id_map: dict[str, dict[str, int | str]] | None = None,
) -> AssetLayout:
    layout = AssetLayout()
    packs = {str(row["id"]): row for row in rows.get("companion_action_packs", [])}

    def mapped(table: str, value: Any) -> int:
        return int(id_map[table][str(value)] if id_map is not None else value)

    def directory(table: str, row: dict[str, Any]) -> tuple[int, str]:
        if table in {"avatar_assets", "companion_character_cards"}:
            return 0, ""
        if table == "companion_outfits":
            return 1, outfit_asset_directory(mapped(table, row["id"]))
        if table == "companion_scenes":
            return 2, scene_asset_directory(mapped(table, row["id"]))
        if table in {"companion_action_packs", "companion_actions"}:
            pack = row if table == "companion_action_packs" else packs[str(row["pack_id"])]
            outfit_id = pack.get("outfit_id")
            return 3, pack_asset_directory(
                mapped("companion_outfits", outfit_id) if outfit_id is not None else None,
                mapped("companion_action_packs", pack["id"]),
            )
        if table == "desktop_video_sets":
            return 3, f"desktop/{mapped(table, row['id'])}/references"
        if table == "desktop_video_actions":
            return 3, f"desktop/{mapped('desktop_video_sets', row['set_id'])}/{mapped(table, row['id'])}"
        created_at = row.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        if isinstance(created_at, datetime):
            return 4, dated_asset_directory(created_at, timezone)
        return 5, ""

    ordered = sorted(
        ((directory(table, row), table, row) for table, records in rows.items() for row in records if "id" in row),
        key=lambda entry: (entry[0], entry[1], str(entry[2]["id"])),
    )
    for (priority, folder), table, row in ordered:
        key = table, str(row["id"])
        layout.directories[key] = folder
        references = asset_paths(row, source_user_id)
        own = priority < 4
        # 外观来源元数据与角色卡引用身份，不将固定资产搬入外观目录。
        if table == "companion_outfits":
            owned = asset_paths(row.get("fullbody_url"), source_user_id)
        elif table == "companion_character_cards":
            owned = set()
        else:
            owned = references if own else set()
        mapping: dict[str, str] = {}
        for old in sorted(references):
            previous = layout.mapping.get(old)
            target = str(PurePosixPath("companion-assets", str(target_user_id), folder, PurePosixPath(old).name))
            if old not in owned and previous is not None:
                target = previous
            if priority == 5 and previous is None:
                # 无生产时间的引用保留已有相对目录；不能按迁移当天猜测归属日。
                target = str(PurePosixPath("companion-assets", str(target_user_id), *PurePosixPath(old).parts[2:]))
            collision = layout.copies.get(target)
            if collision is not None and collision != old:
                raise ValueError(f"资产目标路径冲突：{target}")
            layout.copies[target] = old
            layout.mapping.setdefault(old, target)
            mapping[old] = target
        # 行内映射指向本行目录副本（生产者）；非生产者 owned 为空，target 取首认领值，与 layout.mapping 恒同。
        layout.rows[key] = mapping
    return layout


def freeze_asset_directory(table: str, column: str, value: Any, directory: str) -> Any:
    """按结构冻结可恢复生图状态和动作上下文；视频状态不接受生图专有字段。"""
    if value is None or not isinstance(value, str | dict | list):
        return value
    if isinstance(value, str):
        if not value.lstrip().startswith(("{", "[")):
            return value
        try:
            parsed = json.loads(value)
        except ValueError:
            return value
        updated = freeze_asset_directory(table, column, parsed, directory)
        return json.dumps(updated, ensure_ascii=False) if updated != parsed else value
    if isinstance(value, list):
        return [freeze_asset_directory(table, column, item, directory) for item in value]
    updated = {key: freeze_asset_directory(table, key, item, directory) for key, item in value.items()}
    if "generation_id" in value and ("pending_slots" in value or "inputs" in value):
        updated["storage_directory"] = directory
    if table == "companion_action_packs" and column == "context_json" and "identity" in value:
        updated["storage_directory"] = directory
    return updated
