from datetime import datetime

from common import ModelBase
from sqlalchemy import Boolean, DateTime, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column


class UpdateVersion(ModelBase):
    __tablename__ = "update_versions"

    version: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    release_notes: Mapped[str] = mapped_column(Text, default="")
    exe_filename: Mapped[str] = mapped_column(String(256))
    exe_sha512: Mapped[str] = mapped_column(String(128))
    exe_size: Mapped[int] = mapped_column(Integer, default=0)
    mac_filename: Mapped[str | None] = mapped_column(String(256), nullable=True)
    mac_sha512: Mapped[str | None] = mapped_column(String(128), nullable=True)
    mac_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    runner_filename: Mapped[str | None] = mapped_column(String(256), nullable=True)
    runner_sha512: Mapped[str | None] = mapped_column(String(128), nullable=True)
    runner_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    runner_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
