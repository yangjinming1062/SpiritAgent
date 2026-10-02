"""本机调用日志：原子认领执行权，持久化终态并支持中断后的结果查询。"""

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .constants import get_spiritagent_home
from .file_io import atomic_replace
from .memory_scope import SkillScope
from .pid import pid_exists

logger = logging.getLogger(__name__)

JOURNAL_RETENTION_DAYS: float = 7.0
_CALL_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
_TERMINAL_STATUSES = frozenset({"completed", "failed", "unknown"})
# 取消与完成可能在不同线程落盘，校验终态和写入必须在同一临界区。
_UPDATE_LOCK = threading.Lock()

type CallStatus = Literal["claimed", "completed", "failed", "unknown"]
type ClaimDisposition = Literal[
    "claimed",
    "journal_unavailable",
    "completed",
    "failed",
    "unknown",
    "conflict",
    "claimed_elsewhere",
    "invalid_call_id",
]


class _JournalRecord(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow")

    call_id: str
    status: CallStatus
    fingerprint: str | None = None
    owner_pid: int | None = Field(default=None, gt=0)
    claim_token: str | None = None
    claimed_at: int | float | None = None
    finished_at: int | float | None = None
    result: Any = None
    error: str | None = None

    @field_validator("fingerprint", "owner_pid", "claim_token", "claimed_at", "finished_at", "error", mode="before")
    @classmethod
    def _reject_explicit_null(cls, value: Any) -> Any:
        # 磁盘字段可省略，显式 null 属损坏记录；result 则允许 null。
        if value is None:
            raise ValueError("journal metadata cannot be null")
        return value

    @model_validator(mode="after")
    def _validate_status_fields(self) -> Self:
        if self.status != "unknown" and self.fingerprint is None:
            raise ValueError("journal fingerprint is required")
        if self.status == "claimed" and self.owner_pid is None:
            raise ValueError("claimed journal record requires an owner")
        if self.status == "completed" and "result" not in self.model_fields_set:
            raise ValueError("completed journal record requires a result")
        return self


class CallResult(BaseModel):
    model_config = ConfigDict(strict=True)

    call_id: str
    status: CallStatus
    result: Any
    error: str | None
    claimed_at: int | float | None
    finished_at: int | float | None


@dataclass(slots=True)
class ClaimOutcome:
    disposition: ClaimDisposition
    claim_token: str | None = None
    result: Any = None
    error: str | None = None

    @property
    def should_execute(self) -> bool:
        return self.disposition in {"claimed", "journal_unavailable"}


def _journal_dir() -> Path:
    return get_spiritagent_home() / "call-journal"


def _safe_call_id(call_id: str) -> Path | None:
    if not call_id or len(call_id) > 128 or any(c not in _CALL_ID_CHARS for c in call_id):
        return None
    return _journal_dir() / f"{call_id}.json"


def _args_fingerprint(name: str, args: dict[str, Any], skill_scope: SkillScope | None) -> str:
    canonical = json.dumps(
        {"name": name, "args": args, "skill_scope": asdict(skill_scope) if skill_scope is not None else None},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _unreadable_record(path: Path) -> _JournalRecord:
    # 无法读取不等于从未认领；向恢复方报告待核对，避免重跑可能已发生的副作用。
    return _JournalRecord(
        call_id=path.stem,
        status="unknown",
        error="journal record unreadable; side effects unverified",
    )


def _read_record(path: Path) -> _JournalRecord | None:
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return _unreadable_record(path)
    try:
        record = _JournalRecord.model_validate(data)
    except ValidationError:
        return _unreadable_record(path)
    return record if record.call_id == path.stem else _unreadable_record(path)


def claim(
    call_id: str,
    name: str,
    args: dict[str, Any],
    owner_pid: int,
    skill_scope: SkillScope | None = None,
) -> ClaimOutcome:
    """原子认领；不可写时允许执行但不授予日志写入凭据，已有记录只重放或拒绝。"""
    path = _safe_call_id(call_id)
    if path is None:
        return ClaimOutcome("invalid_call_id", error="call_id contains unsupported characters")
    fingerprint = _args_fingerprint(name, args, skill_scope)
    if (record := _read_record(path)) is not None:
        return _resolve_existing(record, path, fingerprint)

    claim_token = uuid.uuid4().hex
    payload = _JournalRecord(
        call_id=call_id,
        fingerprint=fingerprint,
        status="claimed",
        owner_pid=owner_pid,
        claim_token=claim_token,
        claimed_at=time.time(),
    )
    try:
        # 记录含工具参数与结果，目录与文件都仅本人可读；终态改写沿用文件权限。
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            record = _read_record(path)
            if record is None:
                return ClaimOutcome("unknown", error="journal record disappeared during claim; side effects unverified")
            return _resolve_existing(record, path, fingerprint)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload.model_dump(exclude_unset=True), fh, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as e:
        logger.warning("call journal claim failed for %s: %s", call_id, e)
        return ClaimOutcome("journal_unavailable")
    return ClaimOutcome("claimed", claim_token=claim_token)


def _resolve_existing(record: _JournalRecord, path: Path, fingerprint: str) -> ClaimOutcome:
    if record.fingerprint is not None and record.fingerprint != fingerprint:
        return ClaimOutcome("conflict", error="same call_id already used with different arguments or skill scope")
    record = _resolve_owner(record, path)
    match record.status:
        case "completed":
            return ClaimOutcome("completed", result=record.result)
        case "failed":
            return ClaimOutcome("failed", error=record.error or "tool failed (details lost)")
        case "unknown":
            return ClaimOutcome(
                "unknown",
                error=record.error or "interrupted before completion; side effects unverified",
            )
        case "claimed":
            return ClaimOutcome("claimed_elsewhere", error="call is already claimed by a live process")


def _resolve_owner(record: _JournalRecord, path: Path) -> _JournalRecord:
    if record.status == "claimed" and record.owner_pid is not None and not pid_exists(record.owner_pid):
        return _mark_stale(record, path)
    return record


def _mark_stale(record: _JournalRecord, path: Path) -> _JournalRecord:
    record = record.model_copy(update={"status": "unknown", "finished_at": time.time()})
    _atomic_write(path, record)
    return record


def mark_completed(call_id: str, claim_token: str, result: Any) -> None:
    """仅认领者保存完整结果，不在重放路径另行截断或改写。"""
    _finalize(call_id, claim_token, "completed", result=result)


def mark_failed(call_id: str, claim_token: str, error: str) -> None:
    """错误脱敏由执行边界负责；日志原样保存其错误摘要。"""
    _finalize(call_id, claim_token, "failed", error=error)


def mark_unknown(call_id: str, claim_token: str) -> None:
    """取消不保证工作线程或外部副作用停止，禁止自动重跑。"""
    _finalize(call_id, claim_token, "unknown", error="interrupted before completion; side effects unverified")


async def settle_cancelled_claim(call_id: str, claim_task: asyncio.Task[ClaimOutcome]) -> None:
    """等认领和待核对写入收尾；重复取消不能再次中断这段资源回收。"""

    async def settle() -> None:
        outcome = await claim_task
        if outcome.claim_token is not None:
            await asyncio.to_thread(mark_unknown, call_id, outcome.claim_token)

    cleanup = asyncio.create_task(settle())
    while not cleanup.done():
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.shield(cleanup)
    cleanup.result()


def _finalize(
    call_id: str,
    claim_token: str,
    status: CallStatus,
    *,
    result: Any = None,
    error: str | None = None,
) -> None:
    path = _safe_call_id(call_id)
    if path is None:
        return
    with _UPDATE_LOCK:
        record = _read_record(path)
        if record is None or record.status != "claimed" or record.claim_token != claim_token:
            return
        record.status = status
        record.finished_at = time.time()
        if status == "completed":
            record.result = result
        elif error is not None:
            record.error = error
        _atomic_write(path, record)


def lookup(call_id: str) -> CallResult | None:
    """仅不存在返回 None；损坏、不可读或持有者已死的记录报告待核对。"""
    path = _safe_call_id(call_id)
    if path is None:
        raise ValueError("call_id contains unsupported characters")
    record = _read_record(path)
    if record is None:
        return None
    record = _resolve_owner(record, path)
    return CallResult(
        call_id=call_id,
        status=record.status,
        result=record.result,
        error=record.error,
        claimed_at=record.claimed_at,
        finished_at=record.finished_at,
    )


def sweep_stale_claims(owner_pid: int) -> int:
    """启动时核对死进程或本次启动复用 PID 的认领，按终态产生时间保留七天。"""
    cutoff = time.time() - JOURNAL_RETENTION_DAYS * 86400
    updated = 0
    try:
        for entry in _journal_dir().glob("*.json"):
            record = _read_record(entry)
            if record is None:
                continue
            if record.status in _TERMINAL_STATUSES:
                finished = record.finished_at or record.claimed_at
                if finished is not None and finished < cutoff:
                    with contextlib.suppress(OSError):
                        entry.unlink(missing_ok=True)
            elif record.owner_pid is not None and (record.owner_pid == owner_pid or not pid_exists(record.owner_pid)):
                _mark_stale(record, entry)
                updated += 1
    except OSError as e:
        logger.warning("call journal sweep aborted: %s", e)
    return updated


def _atomic_write(path: Path, record: _JournalRecord) -> None:
    try:
        atomic_replace(str(path), record.model_dump_json(exclude_unset=True))
    except OSError as e:
        logger.warning("call journal write failed for %s: %s", path.name, e)
