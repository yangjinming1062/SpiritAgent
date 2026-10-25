"""用户消息提交的持久幂等账本，连接恢复不重新执行已受理操作。"""

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, field

from infrastructure.database import session_scope
from sqlalchemy import BigInteger, case, delete, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer
from sqlalchemy.orm.attributes import set_committed_value

from features.conversation.asset_bindings import commit_conversation, flush_conversation
from features.conversation.history import build_session_messages
from features.conversation.models import Message
from features.conversation.request_contracts import ChatMessageRequest
from features.conversation.submission_models import PromptSubmission, SubmissionMessage
from features.conversation.turn_store import build_persisted_content


class SubmissionConflictError(ValueError):
    pass


@dataclass(frozen=True)
class SubmissionReceipt:
    request_id: str
    status: str
    error: str | None
    message_ids: list[int] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)
    replace_reply_message_id: int | None = None
    result_message_id: int | None = None

    def to_result(self) -> dict[str, object]:
        return {
            "queued": self.status in ("accepted", "running"),
            "request_id": self.request_id,
            "status": self.status,
            "error": self.error,
            "message_ids": self.message_ids,
            "messages": self.messages,
            "replace_reply_message_id": self.replace_reply_message_id,
            "result_message_id": self.result_message_id,
        }


def _receipt(row: PromptSubmission, conversation_id: int, fingerprint: str | None) -> SubmissionReceipt:
    if row.conversation_id != conversation_id or (fingerprint is not None and row.fingerprint != fingerprint):
        raise SubmissionConflictError("提交标识已用于另一条消息")
    return SubmissionReceipt(
        row.request_id,
        row.status,
        row.error,
        deepcopy(row.message_ids),
        replace_reply_message_id=row.replace_reply_message_id,
        result_message_id=row.result_message_id,
    )


async def find_submission(
    user_id: int,
    conversation_id: int,
    request_id: str,
    fingerprint: str | None,
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
        if fingerprint is None and row.conversation_id != conversation_id:
            return None
        receipt = _receipt(row, conversation_id, fingerprint)
        delivered_ids = [*receipt.message_ids]
        if receipt.result_message_id is not None and receipt.result_message_id not in delivered_ids:
            delivered_ids.append(receipt.result_message_id)
        messages = await build_session_messages(conversation_id, db, only_ids=delivered_ids) if delivered_ids else []
        return SubmissionReceipt(
            receipt.request_id,
            receipt.status,
            receipt.error,
            receipt.message_ids,
            messages,
            receipt.replace_reply_message_id,
            receipt.result_message_id,
        )


async def get_submission(user_id: int, conversation_id: int, request_id: str) -> SubmissionReceipt | None:
    return await find_submission(user_id, conversation_id, request_id, None)


async def reserve_submission(
    user_id: int,
    conversation_id: int,
    request_id: str,
    fingerprint: str,
    origin_kind: str,
    origin_id: str,
    replace_reply_message_id: int | None = None,
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
                replace_reply_message_id=replace_reply_message_id,
            )
            .on_conflict_do_nothing(constraint="uq_prompt_submissions_user_request")
            .returning(PromptSubmission.id),
        )
        await commit_conversation(db)
        if inserted_id is not None:
            return SubmissionReceipt(
                request_id,
                "accepted",
                None,
                replace_reply_message_id=replace_reply_message_id,
            ), True
    existing = await find_submission(user_id, conversation_id, request_id, fingerprint)
    if existing is None:
        raise RuntimeError("The conflicting submission disappeared")
    return existing, False


async def _load_locked_input_ids(db: AsyncSession, submission: PromptSubmission) -> None:
    # 等待受理行锁之后重新取关联；锁语句里的标量子查询可能来自等待前的 MVCC 快照。
    identifiers = list(
        await db.scalars(
            select(SubmissionMessage.message_id)
            .where(
                SubmissionMessage.user_id == submission.user_id,
                SubmissionMessage.submission_id == submission.id,
            )
            .order_by(SubmissionMessage.position)
        )
    )
    set_committed_value(submission, "message_ids", identifiers)


async def replace_submission_messages(db: AsyncSession, submission: PromptSubmission, message_ids: list[int]) -> None:
    ids = list(dict.fromkeys(message_ids))
    if list(submission.message_ids) == ids:
        return
    if submission.message_ids:
        await db.execute(
            delete(SubmissionMessage).where(
                SubmissionMessage.submission_id == submission.id, SubmissionMessage.user_id == submission.user_id
            )
        )
    if ids:
        positions = case(*[(Message.id == identifier, position) for position, identifier in enumerate(ids)])
        source = select(Message.user_id, literal(submission.id, BigInteger()), Message.id, positions).where(
            Message.user_id == submission.user_id,
            Message.conversation_id == submission.conversation_id,
            Message.role == "user",
            Message.id.in_(ids),
        )
        inserted = set(
            await db.scalars(
                insert(SubmissionMessage)
                .from_select(
                    ["user_id", "submission_id", "message_id", "position"],
                    source,
                    include_defaults=False,
                )
                .returning(SubmissionMessage.message_id)
            )
        )
        if inserted != set(ids):
            raise SubmissionConflictError("提交消息不存在或不属于当前会话")
    set_committed_value(submission, "message_ids", ids)


async def persist_submission_inputs(
    user_id: int,
    conversation_id: int,
    request_id: str,
    messages: Sequence[ChatMessageRequest],
    *,
    persisted_message_id: int | None = None,
) -> list[int]:
    """整批输入及受理关联共用短事务；重复调用复用已受理的完整消息序列。"""
    if not messages or (persisted_message_id is not None and len(messages) != 1):
        raise SubmissionConflictError("提交必须包含完整输入，重试只能引用一条原消息")
    contents = [build_persisted_content(message.content, message.attachments) for message in messages]
    async with session_scope() as db:
        submission = await db.scalar(
            select(PromptSubmission)
            .options(defer(PromptSubmission.message_ids, raiseload=True))
            .where(PromptSubmission.user_id == user_id, PromptSubmission.request_id == request_id)
            .with_for_update()
        )
        if submission is None or submission.conversation_id != conversation_id:
            raise SubmissionConflictError("提交记录不存在或不属于当前会话")
        await _load_locked_input_ids(db, submission)
        if submission.message_ids:
            identifiers = list(submission.message_ids)
            if persisted_message_id is not None and identifiers != [persisted_message_id]:
                raise SubmissionConflictError("这个提交已经绑定了其他输入消息")
            existing = {
                row.id: row
                for row in await db.scalars(
                    select(Message).where(
                        Message.id.in_(identifiers),
                        Message.user_id == user_id,
                        Message.conversation_id == conversation_id,
                        Message.role == "user",
                    )
                )
            }
            if existing.keys() != set(identifiers):
                raise SubmissionConflictError("提交的原输入消息已不存在")
            if persisted_message_id is None and (
                len(identifiers) != len(contents)
                or any(
                    (existing[identifier].content, existing[identifier].content_type) != content
                    for identifier, content in zip(identifiers, contents, strict=False)
                )
            ):
                raise SubmissionConflictError("这个提交已经用于不同的输入内容")
            await db.commit()
            return identifiers
        if submission.status != "accepted":
            raise SubmissionConflictError("提交已结束，不能重新创建输入消息")
        if persisted_message_id is None:
            rows = [
                Message(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    role="user",
                    content=content,
                    content_type=content_type,
                )
                for content, content_type in contents
            ]
            db.add_all(rows)
            await flush_conversation(db)
            identifiers = [row.id for row in rows]
        else:
            identifiers = [persisted_message_id]
        await replace_submission_messages(db, submission, identifiers)
        await commit_conversation(db)
    return identifiers


async def record_submission_state(
    user_id: int,
    request_id: str,
    status: str,
    *,
    message_ids: list[int] | None = None,
    error: str | None = None,
) -> tuple[str, str | None]:
    values: dict[str, object] = {"status": status, "error": error}
    async with session_scope() as db:
        row = await db.scalar(
            select(PromptSubmission)
            .options(defer(PromptSubmission.message_ids, raiseload=True))
            .where(
                PromptSubmission.user_id == user_id,
                PromptSubmission.request_id == request_id,
            )
            .with_for_update(),
        )
        if row is None:
            return status, error
        if row.result_message_id is not None:
            values.update(status="completed", error=None)
        if message_ids is not None:
            await _load_locked_input_ids(db, row)
            await replace_submission_messages(db, row, message_ids)
        for key, value in values.items():
            setattr(row, key, value)
        await commit_conversation(db)
        return row.status, row.error


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
            if row.result_message_id is not None:
                row.status, row.error = "completed", None
                continue
            row.status = "interrupted"
            row.error = "服务已重启，上一条任务已中断。请核对已执行的操作后再继续。"
            db.add(
                Message(
                    user_id=row.user_id,
                    conversation_id=row.conversation_id,
                    role="system",
                    subtype="status_interrupted",
                    content=row.error,
                ),
            )
        await commit_conversation(db)
