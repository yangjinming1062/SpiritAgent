"""业务备份贡献契约与事务内表操作；ORM 仅保留在贡献者内部。"""

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from sqlalchemy import Date, DateTime, Select, bindparam, case, delete, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ModelBase
from .text import ensure_utc

BackupRows = dict[str, list[dict[str, Any]]]
IdMap = dict[str, dict[str, int | str]]
BackupImportMode = Literal["overwrite", "merge"]
BACKUP_TABLE_ORDER: list[str] = [
    "user_preferences",
    "user_model_configs",
    "user_social_accounts",
    "custom_voices",
    "user_settings",
    "assets",
    "jobs",
    "job_requests",
    "job_assets",
    "conversations",
    "companion_topic_batches",
    "companion_topics",
    "companion_topic_feedback",
    "messages",
    "message_assets",
    "message_voice_refs",
    "companion_topic_usages",
    "avatar_assets",
    "companion_outfits",
    "outfit_creation_jobs",
    "appearance_assets",
    "action_definitions",
    "companion_scenes",
    "scene_jobs",
    "visual_items",
    "visual_versions",
    "visual_version_assets",
    "visual_jobs",
    "visual_import_jobs",
    "visual_presentations",
    "visual_playbacks",
    "video_gen_jobs",
    "post_publications",
    "personas",
    "cron_jobs",
    "companion_intents",
    "companion_entries",
    "entry_links",
    "entry_assets",
    "entry_comments",
    "life_pursuits",
    "life_action_admissions",
    "life_steps",
    "life_events",
    "life_states",
    "pursuit_evidence",
    "memories",
    "memory_evidence",
    "memory_revisions",
    "memory_fingerprints",
]


class BackupPathRewriter(Protocol):
    inserted_rows: set[tuple[str, str]]
    created: list[Path]
    pending_references: list[tuple[str, str, str, str, str]]

    def rewrite(self, value: Any) -> Any: ...

    def __call__(self, value: str) -> str: ...


@dataclass(frozen=True)
class BackupContext:
    user_id: int
    asset_owner_id: int
    mode: BackupImportMode
    import_batch_id: str
    rewriter: BackupPathRewriter
    id_map: IdMap


@dataclass(frozen=True)
class BackupColumn:
    name: str
    kind: Literal["value", "date", "datetime"] = "value"


PayloadHook = Callable[[dict[str, Any], dict[str, Any], BackupContext], None]
PayloadCheck = Callable[[AsyncSession, dict[str, Any], dict[str, Any], BackupContext], Awaitable[None]]
ExistingRow = Callable[[AsyncSession, dict[str, Any], dict[str, Any], BackupContext], Awaitable[int | None]]
RowsHook = Callable[[AsyncSession, list[dict[str, Any]], dict[str, int | str], BackupContext], Awaitable[None]]
ContextHook = Callable[[AsyncSession, BackupRows, BackupContext], Awaitable[None]]
RestoreRows = Callable[[AsyncSession, list[dict[str, Any]], BackupContext], Awaitable[tuple[dict[str, int | str], int]]]


@dataclass(frozen=True)
class BackupTable:
    name: str
    _model: type[ModelBase]
    foreign_keys: dict[str, str] = field(default_factory=dict)
    unique_keys: tuple[str, ...] | None = None
    excluded_columns: frozenset[str] = frozenset()
    included_columns: tuple[str, ...] | None = None
    strict_references: bool = False
    merge_unique_on_overwrite: bool = False
    order_by_original_id: bool = False
    scope_column: str = "user_id"
    export_query: Callable[[int], Select[Any]] | None = None
    export_payload: Callable[[dict[str, Any]], None] | None = None
    prepare_payload: PayloadHook | None = None
    validate_payload: PayloadCheck | None = None
    find_existing: ExistingRow | None = None
    after_rows: RowsHook | None = None
    restore_context: ContextHook | None = None
    custom_restore: RestoreRows | None = None
    custom_delete: Callable[[AsyncSession, int], Awaitable[None]] | None = None
    skip_existing_parent: str | None = None

    @property
    def identity_name(self) -> str:
        return next(iter(self._model.__table__.primary_key.columns)).name

    @property
    def identity_column(self) -> Any:
        return self._model.__table__.c[self.identity_name]

    def column_nullable(self, name: str) -> bool:
        return self._model.__table__.c[name].nullable

    @property
    def ownership_column(self) -> str | None:
        columns = self._model.__table__.c
        return "owner_id" if "owner_id" in columns else "user_id" if "user_id" in columns else None

    @property
    def references(self) -> dict[str, str]:
        refs = {
            element.parent.name: element.column.table.name
            for constraint in self._model.__table__.foreign_key_constraints
            for element in constraint.elements
            if element.parent.name not in {"user_id", "owner_id"}
            and element.column.table.name != "users"
            and element.column.primary_key
        }
        return {**refs, **self.foreign_keys}

    @property
    def inherited_identity(self) -> str | None:
        mapper = self._model.__mapper__
        return mapper.inherits.local_table.name if mapper.inherits is not None else None

    @property
    def columns(self) -> tuple[BackupColumn, ...]:
        columns = tuple(
            BackupColumn(
                column.name,
                "datetime"
                if isinstance(column.type, DateTime)
                else "date"
                if isinstance(column.type, Date)
                else "value",
            )
            for column in self._model.__table__.columns
            if (
                column.name in self.included_columns
                if self.included_columns is not None
                else column.name not in self.excluded_columns | {"user_id", "owner_id"}
            )
        )
        return columns if any(column.name == "id" for column in columns) else (BackupColumn("id"), *columns)

    def _query(self, user_id: int) -> Select[Any]:
        table = self._model.__table__
        if self.export_query is not None:
            return self.export_query(user_id).with_only_columns(*table.c, maintain_column_froms=True)
        return (
            select(*table.c)
            .select_from(self._model.__mapper__.persist_selectable)
            .where(getattr(self._model, self.scope_column) == user_id)
        )

    async def export(self, db: AsyncSession, user_id: int, *, batch_size: int = 1000) -> list[dict[str, Any]]:
        query = self._query(user_id)
        columns = self.columns
        result: list[dict[str, Any]] = []
        after_id: int | None = None
        while True:
            page = query.where(self.identity_column > after_id) if after_id is not None else query
            rows = (await db.execute(page.order_by(self.identity_column).limit(batch_size))).mappings().all()
            if not rows:
                break
            for row in rows:
                payload = {
                    column.name: row[self.identity_name if column.name == "id" else column.name] for column in columns
                }
                if self.export_payload is not None:
                    self.export_payload(payload)
                for name, value in payload.items():
                    if isinstance(value, datetime):
                        payload[name] = ensure_utc(value).isoformat()
                    elif isinstance(value, date):
                        payload[name] = value.isoformat()
                result.append(payload)
            after_id = rows[-1][self.identity_name]
            if len(rows) < batch_size:
                break
            await asyncio.sleep(0)
        return result

    async def restore(
        self,
        db: AsyncSession,
        raw_rows: list[dict[str, Any]],
        context: BackupContext,
        build_payload: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> tuple[dict[str, int | str], int]:
        if not raw_rows:
            return {}, 0
        if self.custom_restore is not None:
            restored = await self.custom_restore(db, [build_payload(raw) for raw in raw_rows], context)
            await db.flush()
            return restored
        table = self._model.__table__
        records = raw_rows
        if self.skip_existing_parent is not None:
            parent = self.references[self.skip_existing_parent]
            records = [
                row
                for row in records
                if (parent, str(row[self.skip_existing_parent])) in context.rewriter.inserted_rows
            ]
        ordered = sorted(records, key=lambda raw: int(raw["id"])) if self.order_by_original_id else records
        prepared = [(raw, build_payload(raw)) for raw in ordered]
        new_map: dict[str, int | str] = {}
        existing: dict[tuple[Any, ...], int] = {}
        if self.unique_keys not in {None, ("source_key",)} and (
            context.mode == "merge" or self.merge_unique_on_overwrite
        ):
            keys = self.unique_keys
            current = await db.execute(
                self._query(context.user_id).with_only_columns(
                    self.identity_column, *(table.c[key] for key in keys), maintain_column_froms=True
                )
            )
            existing = {tuple(row[1:]): row[0] for row in current}
        source_existing: dict[str, int] = {}
        if "source_key" in table.c and (context.mode == "merge" or self.merge_unique_on_overwrite):
            sources = list({payload["source_key"] for _, payload in prepared if payload.get("source_key")})
            for offset in range(0, len(sources), 5000):
                current = await db.execute(
                    self._query(context.user_id)
                    .with_only_columns(self.identity_column, table.c.source_key, maintain_column_froms=True)
                    .where(table.c.source_key.in_(sources[offset : offset + 5000]))
                )
                source_existing.update({row[1]: row[0] for row in current})
        parent_table = self.inherited_identity
        existing_children: set[int] = set()
        if parent_table is not None:
            mapped = context.id_map.get(parent_table, {})
            for raw, payload in prepared:
                if str(raw["id"]) not in mapped:
                    raise ValueError(f"Missing {parent_table} identity for {self.name}")
                payload[self.identity_name] = mapped[str(raw["id"])]
            if prepared:
                existing_children = set(
                    await db.scalars(
                        select(self.identity_column).where(
                            self.identity_column.in_([payload[self.identity_name] for _, payload in prepared])
                        )
                    )
                )
        active_exists = False
        if context.mode == "merge" and "active" in table.c and any(payload.get("active") for _, payload in prepared):
            active_exists = (
                await db.scalar(
                    self._query(context.user_id)
                    .with_only_columns(self.identity_column)
                    .where(table.c.active.is_(True))
                    .limit(1)
                )
                is not None
            )
        pending: list[tuple[str, dict[str, Any]]] = []
        for raw, payload in prepared:
            if self.validate_payload is not None:
                await self.validate_payload(db, raw, payload, context)
            existing_id = source_existing.get(payload.get("source_key", ""))
            if existing_id is None and self.unique_keys is not None:
                existing_id = existing.get(tuple(payload[key] for key in self.unique_keys))
            if existing_id is None and self.find_existing is not None:
                existing_id = await self.find_existing(db, raw, payload, context)
            if parent_table is not None and payload[self.identity_name] in existing_children:
                existing_id = payload[self.identity_name]
            if existing_id is not None:
                new_map[str(raw["id"])] = existing_id
                continue
            if payload.get("active") and context.mode == "merge":
                if active_exists:
                    payload["active"] = False
                else:
                    active_exists = True
            if self.identity_name == "user_id":
                payload["user_id"] = context.user_id
            pending.append((str(raw["id"]), payload))
        for offset in range(0, len(pending), 500):
            batch = pending[offset : offset + 500]
            returned = list(
                await db.scalars(
                    insert(table).returning(self.identity_column, sort_by_parameter_order=True),
                    [payload for _, payload in batch],
                )
            )
            for (old_id, _), new_id in zip(batch, returned, strict=True):
                new_map[old_id] = new_id
                context.rewriter.inserted_rows.add((self.name, old_id))
        if self.after_rows is not None:
            await self.after_rows(db, records, new_map, context)
        await db.flush()
        return new_map, len(pending)

    async def has_rows(self, db: AsyncSession, user_id: int, reference_column: str) -> bool:
        query = self._query(user_id).with_only_columns(self.identity_column, maintain_column_froms=True)
        query = query.where(self._model.__table__.c[reference_column].is_not(None))
        return await db.scalar(query.limit(1)) is not None

    def _deletion_query(self, user_id: int, preserve_unfinished: bool) -> Select[Any]:
        ids = self._query(user_id).with_only_columns(self.identity_column, maintain_column_froms=True)
        if preserve_unfinished and self.inherited_identity == "jobs":
            root = self._model.__mapper__.inherits.local_table
            ids = ids.where(root.c.state.in_(("succeeded", "failed", "cancelled")), root.c.cleanup_pending.is_(False))
        return ids

    async def clear(self, db: AsyncSession, user_id: int, *, preserve_unfinished: bool = False) -> None:
        if self.custom_delete is not None:
            await self.custom_delete(db, user_id)
        elif self.custom_restore is None:
            ids = self._deletion_query(user_id, preserve_unfinished)
            if preserve_unfinished and self.inherited_identity == "jobs":
                root = self._model.__mapper__.inherits.local_table
                await db.execute(
                    update(root)
                    .where(root.c.id.in_(ids))
                    .values(checkpoint=None, error=None, updated_at=root.c.updated_at)
                )
            await db.execute(delete(self._model.__table__).where(self.identity_column.in_(ids)))

    async def patch_rows(self, db: AsyncSession, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        table = self._model.__table__
        groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        for row in rows:
            groups.setdefault(tuple(sorted(key for key in row if key != "id")), []).append(
                {"_restore_id": row["id"], **{key: value for key, value in row.items() if key != "id"}}
            )
        for columns, payloads in groups.items():
            if columns:
                statement = (
                    update(table)
                    .where(self.identity_column == bindparam("_restore_id"))
                    .values({key: bindparam(key) for key in columns})
                )
                await db.execute(statement, payloads)


class BackupRegistry:
    def __init__(self) -> None:
        self._tables: dict[str, BackupTable] = {}

    def configure(self, tables: Iterable[BackupTable]) -> None:
        contributors = tuple(tables)
        configured = {table.name: table for table in contributors}
        if len(configured) != len(contributors):
            raise ValueError("backup table names must have exactly one owner")
        remaining = dict(configured)
        ordered: dict[str, BackupTable] = {}

        def cycle_member(name: str, current: str, visited: set[str]) -> bool:
            for target in remaining[current].references.values():
                if target == name and target != current:
                    return True
                if (
                    target in remaining
                    and target not in visited
                    and target != current
                    and cycle_member(name, target, visited | {target})
                ):
                    return True
            return False

        while remaining:
            ready = [
                name
                for name, contributor in remaining.items()
                if all(target not in remaining or target == name for target in contributor.references.values())
            ]
            if not ready:
                ready = [
                    name
                    for name, contributor in remaining.items()
                    if cycle_member(name, name, {name})
                    and all(
                        target not in remaining or target == name or contributor.column_nullable(column)
                        for column, target in contributor.references.items()
                    )
                ]
                if not ready:
                    raise ValueError("Backup relations contain a non-nullable dependency cycle")
                ready = [
                    min(
                        ready,
                        key=lambda name: (
                            BACKUP_TABLE_ORDER.index(name) if name in BACKUP_TABLE_ORDER else len(BACKUP_TABLE_ORDER)
                        ),
                    )
                ]
            ready.sort(
                key=lambda name: (
                    BACKUP_TABLE_ORDER.index(name) if name in BACKUP_TABLE_ORDER else len(BACKUP_TABLE_ORDER)
                )
            )
            for name in ready:
                ordered[name] = remaining.pop(name)
        self._tables = ordered
        BACKUP_TABLE_ORDER[:] = ordered

    async def detach_retained_job_targets(self, db: AsyncSession, user_id: int, selected: set[str]) -> None:
        for contributor in self._tables.values():
            if contributor.inherited_identity != "jobs":
                continue
            table = contributor._model.__table__
            root = contributor._model.__mapper__.inherits.local_table
            retained = (
                contributor._query(user_id)
                .with_only_columns(contributor.identity_column, maintain_column_froms=True)
                .where(or_(root.c.state.not_in(("succeeded", "failed", "cancelled")), root.c.cleanup_pending.is_(True)))
            )
            changes = {}
            matches = []
            for column, target in contributor.references.items():
                if target not in selected or not contributor.column_nullable(column):
                    continue
                condition = table.c[column].in_(self._tables[target]._deletion_query(user_id, True))
                changes[column] = case((condition, None), else_=table.c[column])
                matches.append(condition)
            if changes:
                await db.execute(
                    update(table).where(contributor.identity_column.in_(retained), or_(*matches)).values(changes)
                )

    def validate_job_graph(self, rows: BackupRows) -> None:
        if "jobs" not in rows:
            return
        root = self._tables["jobs"]
        allowed = root._model.__mapper__.polymorphic_map
        jobs = {str(row["id"]): row for row in rows["jobs"]}
        if len(jobs) != len(rows["jobs"]):
            raise ValueError("Duplicate task identities in backup")
        detail_kinds = {
            table._model.__mapper__.polymorphic_identity: name
            for name, table in self._tables.items()
            if table.inherited_identity == "jobs"
        }
        seen: dict[str, str] = {}
        for kind, name in detail_kinds.items():
            for detail in rows.get(name, []):
                identity = str(detail["id"])
                if identity in seen:
                    raise ValueError("A task cannot have multiple detail records")
                task = jobs.get(identity)
                if task is None or task.get("kind") != kind:
                    raise ValueError("Task type does not match its detail record")
                seen[identity] = name
        for identity, task in jobs.items():
            kind = task.get("kind")
            if not isinstance(kind, str) or kind not in allowed:
                raise ValueError("Unknown task type in backup")
            expected = detail_kinds.get(kind)
            if expected is None or expected not in rows or identity in seen:
                continue
            tombstone = (
                task.get("state") in {"succeeded", "failed", "cancelled"}
                and task.get("checkpoint") is None
                and not task.get("cleanup_pending")
            )
            if not tombstone:
                raise ValueError("An unfinished task requires its business detail record")

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tables)

    def table(self, name: str) -> BackupTable:
        return self._tables[name]


BACKUP_REGISTRY = BackupRegistry()
