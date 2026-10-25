from datetime import datetime
from uuid import uuid4

from sqlalchemy import BigInteger, DateTime, Identity, MetaData, String, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# 约束使用稳定名称，供 Alembic autogenerate 比对。
_NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "pk": "%(table_name)s_pkey",
    "fk": "%(table_name)s_%(column_0_N_name)s_fkey",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}


class ModelBase(DeclarativeBase):
    __abstract__ = True
    __mapper_args__ = {"eager_defaults": True}
    metadata = MetaData(naming_convention=_NAMING_CONVENTION)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_key: Mapped[str] = mapped_column(
        String(36), default=lambda: str(uuid4()), server_default=text("(gen_random_uuid())::text")
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
