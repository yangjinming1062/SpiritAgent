"""本机调用日志：按调用标识持久化参数指纹、执行阶段与可重取结果。

每条记录一个文件，位于 ``$SPIRITAGENT_HOME/call-journal/<call_id>.json``。

- **原子认领**：``claim`` 以 ``O_CREAT | O_EXCL`` 建文件，同一时刻只有一个进程能认领同一调用，
  认领前先查询已有终态，避免并发重复执行。
- **同标识不同参数拒绝**：认领冲突时比对参数指纹（tool + args 的规范 JSON 哈希），不一致返回
  conflict，调用方不得执行。
- **先保存再返回**：完成状态与结果落盘后才向调用方返回，进程中断后凭记录区分「已执行」与
  「从未开始」。
- **中断核对**：重启后仍处 claimed（执行中）的记录由持有进程存活性裁决——进程已死且无终态
  标记 unknown（副作用可能已发生、待核对），不自动重放；进程仍存活则视为在途。

记录不能保证任意外部副作用恰好发生一次，只降低重复执行风险并提供查询依据。
"""

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import get_spiritagent_home
from .file_io import atomic_replace
from .pid import pid_exists

logger = logging.getLogger(__name__)

# journal 中结果载荷的大小上限（字符）。结果本身已受 registry 的工具输出上限约束，这里再截一道，
# 防止个别直写大字符串的工具把 journal 撑爆。
MAX_JOURNAL_RESULT_CHARS: int = 120_000

# 记录保留窗口：超过该天数的终态记录在启动清理时删除。
JOURNAL_RETENTION_DAYS: float = 7.0

_STATUS_CLAIMED = "claimed"
_STATUS_COMPLETED = "completed"
_STATUS_FAILED = "failed"
_STATUS_UNKNOWN = "unknown"


@dataclass(slots=True)
class ClaimOutcome:
    """``claim`` 的裁决结果。``should_execute`` 为 False 时调用方不得执行工具。"""

    should_execute: bool
    # completed: 命中已完成记录（result 携带可重取结果）；failed: 命中已失败记录（error 为失败摘要）；
    # conflict: 同一标识对应不同参数；claimed_elsewhere: 另一存活进程持有。
    disposition: str
    result: Any = None
    error: str | None = None


def _journal_dir() -> Path:
    return get_spiritagent_home() / "call-journal"


def _safe_call_id(call_id: str) -> Path | None:
    """调用标识只允许有限字符集，防止 ``..`` / 分隔符逃逸出 journal 目录。"""
    if not call_id or len(call_id) > 128 or any(c not in _CALL_ID_CHARS for c in call_id):
        return None
    return _journal_dir() / f"{call_id}.json"


_CALL_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.",
)


def args_fingerprint(name: str, args: dict[str, Any]) -> str:
    """参数指纹：工具名 + 参数的规范 JSON SHA-256。"""

    canonical = json.dumps({"name": name, "args": args}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _truncate(value: Any) -> Any:
    # 递归截断：registry 的结果大小上限靠各工具自觉配合，嵌套结构里的大字符串同样要拦，
    # 否则幂等重放会把撑爆 journal 的载荷再原样读出来。
    if isinstance(value, str):
        if len(value) > MAX_JOURNAL_RESULT_CHARS:
            return value[:MAX_JOURNAL_RESULT_CHARS] + "…[journal truncated]"
        return value
    if isinstance(value, dict):
        return {k: _truncate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_truncate(v) for v in value]
    return value


def _read_record(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def claim(call_id: str, name: str, args: dict[str, Any], owner_pid: int) -> ClaimOutcome:
    """查询并原子认领一次工具调用。

    返回 ``should_execute=True`` 表示调用方获得执行权（新认领或接管 unknown 记录）；
    其余情形携带既有终态或冲突原因，调用方直接回放/拒绝，不得执行。
    """
    path = _safe_call_id(call_id)
    if path is None:
        return ClaimOutcome(False, "invalid_call_id", error="call_id contains unsupported characters")

    if (record := _read_record(path)) is not None:
        return _resolve_existing(record, path, name, args, owner_pid)

    _journal_dir().mkdir(parents=True, exist_ok=True)
    payload = {
        "call_id": call_id,
        "fingerprint": args_fingerprint(name, args),
        "status": _STATUS_CLAIMED,
        "owner_pid": owner_pid,
        "claimed_at": time.time(),
    }
    try:
        # O_CREAT | O_EXCL 是认领的原子性来源：并发认领同一 call_id 只有一个成功。
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if (record := _read_record(path)) is not None:
            return _resolve_existing(record, path, name, args, owner_pid)
        return ClaimOutcome(False, "invalid_call_id", error="journal record unreadable")
    except OSError as e:
        # journal 不可写不是拒绝执行的理由——工具调用本身不依赖它。
        logger.warning("call journal claim failed for %s: %s", call_id, e)
        return ClaimOutcome(True, "journal_unavailable")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    return ClaimOutcome(True, "claimed")


def _resolve_existing(
    record: dict[str, Any],
    path: Path,
    name: str,
    args: dict[str, Any],
    owner_pid: int,
) -> ClaimOutcome:
    status = record.get("status")

    if record.get("fingerprint") != args_fingerprint(name, args):
        return ClaimOutcome(False, "conflict", error="same call_id already used with different arguments")

    if status == _STATUS_COMPLETED:
        return ClaimOutcome(False, "completed", result=record.get("result"))
    if status == _STATUS_FAILED:
        return ClaimOutcome(False, "failed", error=record.get("error") or "tool failed (details lost)")
    if status == _STATUS_UNKNOWN:
        # 中断遗留：核对阶段已判明无法确认，只有持有进程仍存活的在途认领例外（见下）。
        return ClaimOutcome(False, "unknown", error="interrupted before completion; side effects unverified")

    # claimed：区分「同进程重入」「另一存活进程在途」与「持有者已死的中断遗留」。
    owner = record.get("owner_pid")
    if owner == owner_pid:
        return ClaimOutcome(False, "claimed_elsewhere", error="call already claimed in this process")
    if isinstance(owner, int) and pid_exists(owner):
        return ClaimOutcome(False, "claimed_elsewhere", error="call is being executed by another live process")

    # 持有进程已死且无终态：副作用可能已发生，标记 unknown 待人工/上层核对，不自动重放。
    record["status"] = _STATUS_UNKNOWN
    record["resolved_unknown_at"] = time.time()
    _atomic_write(path, record)
    return ClaimOutcome(False, "unknown", error="previous owner died mid-execution; side effects unverified")


def mark_completed(call_id: str, name: str, args: dict[str, Any], result: Any) -> None:
    """记录完成终态与可重取结果；必须先于向调用方返回结果调用。"""
    _finalize(call_id, name, args, {"status": _STATUS_COMPLETED, "result": _truncate(result)})


def mark_failed(call_id: str, name: str, args: dict[str, Any], error: str) -> None:
    """记录失败终态；错误摘要同样经脱敏由调用方负责（registry 已 sanitize）。"""
    _finalize(call_id, name, args, {"status": _STATUS_FAILED, "error": _truncate(str(error))})


def _finalize(call_id: str, name: str, args: dict[str, Any], patch: dict[str, Any]) -> None:
    path = _safe_call_id(call_id)
    if path is None:
        return
    record = _read_record(path)
    if record is None:
        # claim 阶段 journal 不可用（journal_unavailable）时的兜底：尽力补一条终态，
        # 让查询侧至少能看到已完成的事实。
        record = {
            "call_id": call_id,
            "fingerprint": args_fingerprint(name, args),
            "owner_pid": os.getpid(),
            "claimed_at": time.time(),
        }
    if record.get("status") in (_STATUS_COMPLETED, _STATUS_FAILED, _STATUS_UNKNOWN):
        # 终态不可覆盖：unknown 是核对语义，不能被迟到的结果改写成 completed。
        return
    record.update(patch)
    record["finished_at"] = time.time()
    _atomic_write(path, record)


def lookup(call_id: str) -> dict[str, Any] | None:
    """按调用标识查询记录；不存在或不可读返回 None。结果字段原样返回。"""
    path = _safe_call_id(call_id)
    if path is None:
        return None
    record = _read_record(path)
    if record is None:
        return None
    return {
        "call_id": call_id,
        "status": record.get("status"),
        "result": record.get("result"),
        "error": record.get("error"),
        "claimed_at": record.get("claimed_at"),
        "finished_at": record.get("finished_at"),
    }


def sweep_stale_claims(owner_pid: int) -> int:
    """启动时清理本进程的陈旧认领并裁决中断遗留。

    只处理两类记录：本进程在上一次运行中留下的 claimed（进程重启后 pid 复用场景极少但归我们管），
    以及持有进程已死的 claimed（标 unknown）。返回更新过的记录数。
    """
    journal_dir = _journal_dir()
    try:
        entries = list(journal_dir.iterdir())
    except OSError:
        return 0

    now = time.time()
    cutoff = now - JOURNAL_RETENTION_DAYS * 86400
    updated = 0
    for entry in entries:
        try:
            if entry.suffix != ".json":
                continue
            record = _read_record(entry)
            if record is None:
                continue
            status = record.get("status")
            finished = record.get("finished_at") or record.get("claimed_at") or now
            if status in (_STATUS_COMPLETED, _STATUS_FAILED, _STATUS_UNKNOWN):
                if isinstance(finished, (int, float)) and finished < cutoff:
                    entry.unlink(missing_ok=True)
                continue
            owner = record.get("owner_pid")
            if owner == owner_pid or not (isinstance(owner, int) and pid_exists(owner)):
                record["status"] = _STATUS_UNKNOWN
                record["resolved_unknown_at"] = now
                _atomic_write(entry, record)
                updated += 1
        except OSError:
            continue
    return updated


def _atomic_write(path: Path, record: dict[str, Any]) -> None:
    try:
        atomic_replace(str(path), json.dumps(record, ensure_ascii=False, default=str))
    except OSError as e:
        logger.warning("call journal write failed for %s: %s", path.name, e)
