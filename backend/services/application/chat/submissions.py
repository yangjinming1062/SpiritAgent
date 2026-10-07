"""用户消息提交的持久幂等账本，连接恢复不重新执行已受理操作。"""

import json
from dataclasses import dataclass, field

from components import session_scope
from modules.conversation import Message
from modules.remote import PromptSubmission
from modules.system import ChatMessageRequest
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from services.domains.conversation import build_session_messages

from .persistence import _build_persisted_content


class SubmissionConflictError(ValueError):
    pass


@dataclass(frozen=True)
class SubmissionReceipt:
    request_id: str
    status: str
    error: str | None
    message_ids: list[int] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)

    def to_result(self) -> dict[str, object]:
        return {
            "queued": self.status in ("accepted", "running"),
            "request_id": self.request_id,
            "status": self.status,
            "error": self.error,
            "message_ids": self.message_ids,
            "messages": self.messages,
        }


def _receipt(row: PromptSubmission, conversation_id: int, fingerprint: str) -> SubmissionReceipt:
    if row.conversation_id != conversation_id or row.fingerprint != fingerprint:
        raise SubmissionConflictError("提交标识已用于另一条消息")
    return SubmissionReceipt(row.request_id, row.status, row.error, json.loads(row.message_ids_json))


async def find_submission(
    user_id: int,
    conversation_id: int,
    request_id: str,
    fingerprint: str,
) -> SubmissionReceipt | None:
    async with session_scope() as db:
        row = await db.scalar(
            select(PromptSubmission).where(
                PromptSubmission.user_id == user_id,
                PromptSubmission.request_id == request_id,
            ),
        )
        if row is None:
            return None
        receipt = _receipt(row, conversation_id, fingerprint)
        messages = (
            await build_session_messages(conversation_id, db, only_ids=receipt.message_ids)
            if receipt.message_ids
            else []
        )
        return SubmissionReceipt(receipt.request_id, receipt.status, receipt.error, receipt.message_ids, messages)


async def reserve_submission(
    user_id: int,
    conversation_id: int,
    request_id: str,
    fingerprint: str,
    origin_kind: str,
    origin_id: str,
) -> tuple[SubmissionReceipt, bool]:
    async with session_scope() as db:
        inserted_id = await db.scalar(
            insert(PromptSubmission)
            .values(
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
                fingerprint=fingerprint,
                origin_kind=origin_kind,
                origin_id=origin_id,
            )
            .on_conflict_do_nothing(constraint="uq_prompt_submissions_user_request")
            .returning(PromptSubmission.id),
        )
        await db.commit()
        if inserted_id is not None:
            return SubmissionReceipt(request_id, "accepted", None), True
    existing = await find_submission(user_id, conversation_id, request_id, fingerprint)
    if existing is None:
        raise RuntimeError("The conflicting submission disappeared")
    return existing, False


async def persist_submission_input(
    user_id: int,
    conversation_id: int,
    request_id: str,
    message: ChatMessageRequest,
    *,
    persisted_message_id: int | None = None,
    precursor_ids: list[int] | None = None,
) -> int:
    async with session_scope() as db:
        if persisted_message_id is None:
            content, content_type = _build_persisted_content(message.content, message.attachments)
            row = Message(conversation_id=conversation_id, role="user", content=content, content_type=content_type)
            db.add(row)
            await db.flush()
            persisted_message_id = row.id
        await db.execute(
            update(PromptSubmission)
            .where(
                PromptSubmission.user_id == user_id,
                PromptSubmission.request_id == request_id,
            )
            .values(message_ids_json=json.dumps([*(precursor_ids or []), persisted_message_id])),
        )
        await db.commit()
    return persisted_message_id


async def record_submission_state(
    user_id: int,
    request_id: str,
    status: str,
    *,
    message_ids: list[int] | None = None,
    error: str | None = None,
) -> None:
    values: dict[str, object] = {"status": status, "error": error}
    if message_ids is not None:
        values["message_ids_json"] = json.dumps(message_ids)
    async with session_scope() as db:
        await db.execute(
            update(PromptSubmission)
            .where(
                PromptSubmission.user_id == user_id,
                PromptSubmission.request_id == request_id,
            )
            .values(**values),
        )
        await db.commit()


async def recover_interrupted_submissions() -> None:
    async with session_scope() as db:
        rows = list(
            await db.scalars(
                select(PromptSubmission)
                .where(
                    PromptSubmission.status.in_(("accepted", "running")),
                )
                .with_for_update(),
            ),
        )
        for row in rows:
            row.status = "interrupted"
            row.error = "服务已重启，上一条任务已中断。请核对已执行的操作后再继续。"
            db.add(
                Message(
                    conversation_id=row.conversation_id,
                    role="system",
                    subtype="status_interrupted",
                    content=row.error,
                ),
            )
        await db.commit()
