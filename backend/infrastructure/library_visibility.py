"""素材可见范围。每次私下制作在独立范围内运行，公开同一份成品不再生成。"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, SQLColumnExpression, event, func, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, ORMExecuteState, Session, mapped_column, with_loader_criteria
from sqlalchemy.sql.elements import ColumnElement

from infrastructure.models import ModelBase, TimestampMixin

_WORK: ContextVar[int | None] = ContextVar("library_work", default=None)


def current_library_work() -> int | None:
    return _WORK.get()


@contextmanager
def library_work(work_id: int | None) -> Iterator[None]:
    token = _WORK.set(work_id)
    try:
        yield
    finally:
        _WORK.reset(token)


class LibraryWork(ModelBase, TimestampMixin):
    __tablename__ = "library_works"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    output_paths: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")


class LibraryMaterial:
    library_work_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("library_works.id", ondelete="CASCADE"), default=current_library_work
    )


@contextmanager
def all_asset_references(db: AsyncSession) -> Iterator[None]:
    """回收完整账户资产时，私下作品的引用具有相同的保留效力。"""
    previous = db.info.get("all_asset_references")
    db.info["all_asset_references"] = True
    try:
        yield
    finally:
        if previous is None:
            db.info.pop("all_asset_references", None)
        else:
            db.info["all_asset_references"] = previous


async def create_private_work(db: AsyncSession, user_id: int) -> int:
    work = LibraryWork(user_id=user_id)
    db.add(work)
    await db.flush()
    return work.id


async def release_private_work(db: AsyncSession, user_id: int, work_id: int) -> bool:
    return bool(
        await db.scalar(
            update(LibraryWork)
            .where(LibraryWork.user_id == user_id, LibraryWork.id == work_id, LibraryWork.released_at.is_(None))
            .values(released_at=func.now())
            .returning(LibraryWork.id)
        )
    )


def visible_library_work(column: SQLColumnExpression[int | None]) -> ColumnElement[bool]:
    released = (
        select(LibraryWork.id)
        .where(LibraryWork.id == column, LibraryWork.released_at.is_not(None))
        .correlate_except(LibraryWork)
    )
    return or_(column.is_(None), released.exists())


async def record_work_outputs(db: AsyncSession, user_id: int, work_id: int | None, paths: set[str]) -> None:
    if work_id is None:
        return
    row = await db.scalar(
        select(LibraryWork).where(LibraryWork.id == work_id, LibraryWork.user_id == user_id).with_for_update()
    )
    if row is None:
        raise LookupError("作品已不存在")
    prefix = f"companion-assets/{user_id}/private-work/{work_id}/"
    outputs = set(row.output_paths) | {path for path in paths if path.startswith(prefix)}
    if outputs != set(row.output_paths):
        row.output_paths = sorted(outputs)


async def released_work_file(db: AsyncSession, user_id: int, work_id: int, path: str) -> bool:
    return bool(
        await db.scalar(
            select(LibraryWork.id).where(
                LibraryWork.id == work_id,
                LibraryWork.user_id == user_id,
                LibraryWork.released_at.is_not(None),
                LibraryWork.output_paths.contains([path]),
            )
        )
    )


async def released_work(db: AsyncSession, user_id: int, work_id: int) -> bool:
    return bool(
        await db.scalar(
            select(LibraryWork.id).where(
                LibraryWork.id == work_id, LibraryWork.user_id == user_id, LibraryWork.released_at.is_not(None)
            )
        )
    )


@event.listens_for(Session, "do_orm_execute")
def _restrict_materials(execution: ORMExecuteState) -> None:
    if (
        not execution.is_orm_statement
        or execution.execution_options.get("discover_private_works")
        or execution.session.info.get("all_asset_references")
    ):
        return
    if execution.is_select or execution.is_update or execution.is_delete:
        work_id = current_library_work() or 0
        execution.statement = execution.statement.options(
            with_loader_criteria(
                LibraryMaterial,
                lambda item: or_(visible_library_work(item.library_work_id), item.library_work_id == work_id),
                include_aliases=True,
            )
        )
