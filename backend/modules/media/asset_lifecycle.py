from datetime import datetime

from common import ModelBase
from sqlalchemy import DateTime, ForeignKey, Integer, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column


class AssetCleanupPending(ModelBase):
    """业务引用释放与回收待办同事务；磁盘错误保留待办供启动和定时补偿。"""

    __tablename__ = "asset_cleanup_pending"
    __table_args__ = (UniqueConstraint("user_id", "path", name="uq_asset_cleanup_pending_user_path"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
