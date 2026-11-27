from datetime import date

from sqlalchemy import Select, select

from .models import DiaryEntry


def date_scoped_diaries(user_id: int, date_from: date | None, date_to: date | None) -> Select[tuple[DiaryEntry]]:
    statement = select(DiaryEntry).where(DiaryEntry.user_id == user_id)
    if date_from is not None:
        statement = statement.where(DiaryEntry.entry_date >= date_from)
    if date_to is not None:
        statement = statement.where(DiaryEntry.entry_date <= date_to)
    return statement
