"""业务备份贡献契约与事务内表操作；ORM 仅保留在贡献者内部。"""

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from sqlalchemy import Date, DateTime, Select, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ModelBase
from .text import ensure_utc

BackupRows = dict[str, list[dict[str, Any]]]
IdMap = dict[str, dict[str, int | str]]
BackupImportMode = Literal["overwrite", "merge"]
BACKUP_TABLE_ORDER = (
    "user_preferences",
    "conversations",
    "user_model_configs",
    "avatar_assets",
    "companion_character_cards",
    "companion_outfits",
    "companion_action_packs",
    "companion_actions",
    "companion_scenes",
    "desktop_video_sets",
    "desktop_video_actions",
    "desktop_video_states",
    "personas",
    "custom_voices",
    "user_settings",
    "cron_jobs",
    "companion_intents",
    "memories",
    "companion_posts",
    "companion_post_comments",
    "companion_diary_entries",
    "companion_topic_batches",
    "companion_topics",
    "companion_topic_feedback",
    "messages",
    "companion_topic_usages",
)


class BackupPathRewriter(Protocol):
    inserted_rows: set[tuple[str, str]]
    created: list[Path]

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

    @property
    def columns(self) -> tuple[BackupColumn, ...]:
        return tuple(
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
                else column.name not in self.excluded_columns | {"user_id"}
            )
        )

    def _query(self, user_id: int) -> Select[Any]:
        if self.export_query is not None:
            return self.export_query(user_id)
        return select(self._model).where(getattr(self._model, self.scope_column) == user_id)

    async def export(self, db: AsyncSession, user_id: int, *, batch_size: int = 1000) -> list[dict[str, Any]]:
        query = self._query(user_id)
        columns = self.columns
        result: list[dict[str, Any]] = []
        offset = 0
        while True:
            rows = (await db.execute(query.order_by(self._model.id).offset(offset).limit(batch_size))).scalars().all()
            if not rows:
                break
            for row in rows:
                payload = {column.name: getattr(row, column.name) for column in columns}
                if self.export_payload is not None:
                    self.export_payload(payload)
                for name, value in payload.items():
                    if isinstance(value, datetime):
                        payload[name] = ensure_utc(value).isoformat()
                    elif isinstance(value, date):
                        payload[name] = value.isoformat()
                result.append(payload)
            if len(rows) < batch_size:
                break
            offset += len(rows)
            await asyncio.sleep(0)
        return result

    async def restore(
        self,
        db: AsyncSession,
        raw_rows: list[dict[str, Any]],
        context: BackupContext,
        build_payload: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> tuple[dict[str, int | str], int]:
        if self.custom_restore is not None:
            restored = await self.custom_restore(db, [build_payload(raw) for raw in raw_rows], context)
            await db.flush()
            return restored
        new_map: dict[str, int | str] = {}
        pending: list[tuple[str, ModelBase]] = []
        ordered = sorted(raw_rows, key=lambda raw: int(raw["id"])) if self.order_by_original_id else raw_rows
        for raw in ordered:
            payload = build_payload(raw)
            if self.validate_payload is not None:
                await self.validate_payload(db, raw, payload, context)
            existing_id = None
            if self.find_existing is not None:
                existing_id = await self.find_existing(db, raw, payload, context)
            if (
                existing_id is None
                and self.unique_keys is not None
                and (context.mode == "merge" or self.merge_unique_on_overwrite)
            ):
                await db.flush()
                existing_id = await db.scalar(
                    select(self._model.id).where(
                        getattr(self._model, self.scope_column) == context.user_id,
                        *(getattr(self._model, key) == payload[key] for key in self.unique_keys),
                    ),
                )
            if existing_id is not None:
                new_map[str(raw["id"])] = existing_id
                continue
            if context.mode == "merge" and payload.get("active"):
                await db.flush()
                if await db.scalar(
                    select(self._model.id)
                    .where(self._model.user_id == context.user_id, self._model.active.is_(True))
                    .limit(1),
                ):
                    payload["active"] = False
            instance = self._model(**payload)
            db.add(instance)
            pending.append((str(raw["id"]), instance))
            context.rewriter.inserted_rows.add((self.name, str(raw["id"])))
        await db.flush()
        for original_id, instance in pending:
            new_map[original_id] = instance.id
        if self.after_rows is not None:
            await self.after_rows(db, raw_rows, new_map, context)
        await db.flush()
        return new_map, len(pending)

    async def has_rows(
        self,
        db: AsyncSession,
        user_id: int,
        reference_column: str | None = None,
        reference_values: tuple[Any, ...] | None = None,
    ) -> bool:
        query = select(self._model.id).where(getattr(self._model, self.scope_column) == user_id)
        if reference_column is not None:
            column = getattr(self._model, reference_column)
            query = query.where(column.is_not(None) if reference_values is None else column.in_(reference_values))
        return await db.scalar(query.limit(1)) is not None

    async def ids(self, db: AsyncSession, user_id: int) -> tuple[int, ...]:
        return tuple(await db.scalars(select(self._model.id).where(getattr(self._model, self.scope_column) == user_id)))

    async def clear(self, db: AsyncSession, user_id: int) -> None:
        if self.custom_delete is not None:
            await self.custom_delete(db, user_id)
        else:
            await db.execute(delete(self._model).where(getattr(self._model, self.scope_column) == user_id))

    async def rewrite_inserted(
        self,
        db: AsyncSession,
        row_id: int | str,
        rewrite: Callable[[str, Any], Any],
    ) -> None:
        row = await db.get(self._model, row_id)
        if row is None:
            raise ValueError(f"Restored {self.name} row is missing")
        for column in self._model.__table__.columns:
            value = getattr(row, column.name)
            if isinstance(value, str | dict | list):
                rewritten = rewrite(column.name, value)
                if rewritten != value:
                    setattr(row, column.name, rewritten)


class BackupRegistry:
    def __init__(self) -> None:
        self._tables: dict[str, BackupTable] = {}

    def configure(self, tables: Iterable[BackupTable]) -> None:
        contributors = tuple(tables)
        configured = {table.name: table for table in contributors}
        if len(configured) != len(contributors):
            raise ValueError("backup table names must have exactly one owner")
        self._tables = configured

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tables)

    def table(self, name: str) -> BackupTable:
        return self._tables[name]


BACKUP_REGISTRY = BackupRegistry()
