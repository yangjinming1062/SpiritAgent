"""第一人称日记和可重建的检索索引。"""

from datetime import date

from infrastructure.models import ModelBase, TimestampMixin
from infrastructure.search_index import SearchIndexRow, search_index_constraints
from sqlalchemy import Boolean, Date, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column


class DiaryEntry(ModelBase, TimestampMixin):
    __tablename__ = "diary_entries"
    __table_args__ = (
        UniqueConstraint("user_id", "entry_date", name="uq_diary_entries_user_date"),
        Index("ix_diary_entries_unread", "user_id", postgresql_where=text("is_read IS FALSE")),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    entry_date: Mapped[date] = mapped_column(Date)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    title: Mapped[str] = mapped_column(String(128))
    body: Mapped[str] = mapped_column(Text)


class DiarySearchIndex(SearchIndexRow):
    __tablename__ = "diary_search_index"
    __table_args__ = search_index_constraints(__tablename__)

    record_id: Mapped[int] = mapped_column(ForeignKey("diary_entries.id", ondelete="CASCADE"))
