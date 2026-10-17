"""脚本输出的有界收集和结果格式。"""

import logging
import os
import threading
import time
from collections import deque
from collections.abc import Iterator
from typing import IO, Any

from spirit_agent_runner.contracts.config import load_config
from spirit_agent_runner.infrastructure.clean import clean_output

logger = logging.getLogger(__name__)


MAX_STDOUT_BYTES = 50_000


STDOUT_HEAD_BYTES = int(MAX_STDOUT_BYTES * 0.4)


STDOUT_TAIL_BYTES = MAX_STDOUT_BYTES - STDOUT_HEAD_BYTES


MAX_STDERR_BYTES = 10_000


def truncation_notice(omitted: int, total: int) -> str:
    return f"\n\n... [OUTPUT TRUNCATED - {omitted:,} chars omitted out of {total:,} total] ...\n\n"


def error_result(error: str, tool_calls: int, duration: float) -> dict[str, Any]:
    return {"status": "error", "error": error, "tool_calls_made": tool_calls, "duration_seconds": duration}


def build_result(
    *,
    status: str,
    output: str,
    stderr: str,
    exit_code: int,
    timeout: int,
    tool_calls: int,
    duration: float,
) -> dict[str, Any]:
    output = clean_output(output, config=load_config())
    stderr = clean_output(stderr, config=load_config())
    result: dict[str, Any] = {
        "status": status,
        "output": output,
        "tool_calls_made": tool_calls,
        "duration_seconds": duration,
    }
    if status == "timeout":
        timeout_msg = f"Script timed out after {timeout}s and was killed."
        result["error"] = timeout_msg
        result["output"] = f"{output}\n\n⏰ {timeout_msg}" if output else f"⏰ {timeout_msg}"
        logger.warning(
            "execute_code timed out after %ss (limit %ss) with %d tool calls",
            duration,
            timeout,
            tool_calls,
        )
    elif status == "interrupted":
        result["output"] = output + "\n[execution interrupted]"
    elif exit_code != 0:
        result["status"] = "error"
        result["error"] = stderr or f"Script exited with code {exit_code}"
        if stderr:
            result["output"] = output + "\n--- stderr ---\n" + stderr
    return result


def _pipe_chunks(pipe: IO[bytes], stop_event: threading.Event) -> Iterator[bytes]:
    """脚本结束后排空现有输出；继承写端的后代不能无限持有读线程。"""
    try:
        with pipe:
            fd = pipe.fileno()
            os.set_blocking(fd, False)
            drain_deadline: float | None = None
            while True:
                if stop_event.is_set():
                    if drain_deadline is None:
                        drain_deadline = time.monotonic() + 0.2
                    elif time.monotonic() >= drain_deadline:
                        return
                try:
                    data = os.read(fd, 4096)
                except BlockingIOError:
                    if stop_event.is_set():
                        return
                    stop_event.wait(0.02)
                    continue
                if not data:
                    return
                yield data
    except (ValueError, OSError) as e:
        logger.debug("Error reading process output: %s", e, exc_info=True)


def drain_capped(pipe: IO[bytes], chunks: list[bytes], max_bytes: int, stop_event: threading.Event) -> None:
    """读尽管道但只保留前 max_bytes 字节；持续读取避免子进程因管道写满而阻塞。"""
    total = 0
    for data in _pipe_chunks(pipe, stop_event):
        if total < max_bytes:
            chunks.append(data[: max_bytes - total])
        total += len(data)


def drain_head_tail(
    pipe: IO[bytes],
    head_chunks: list[bytes],
    tail_chunks: list[bytes],
    total: list[int],
    stop_event: threading.Event,
) -> None:
    """读尽管道，保留开头与结尾各一段；结尾段在读完后一次性发布，避免与主线程读取竞争。"""
    head_collected = 0
    tail_buf: deque[bytes] = deque()
    tail_collected = 0
    for data in _pipe_chunks(pipe, stop_event):
        total[0] += len(data)
        if head_collected < STDOUT_HEAD_BYTES:
            keep = data[: STDOUT_HEAD_BYTES - head_collected]
            head_chunks.append(keep)
            head_collected += len(keep)
            if not (data := data[len(keep) :]):
                continue
        tail_buf.append(data)
        tail_collected += len(data)
        while tail_collected > STDOUT_TAIL_BYTES and tail_buf:
            tail_collected -= len(tail_buf.popleft())
    tail_chunks.extend(tail_buf)
