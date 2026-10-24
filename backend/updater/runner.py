"""子进程执行：超时、日志采集与收尾。"""

import asyncio
import contextlib
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

LineSink = Callable[[str], Awaitable[None]]


@dataclass(slots=True)
class RunOutput:
    code: int
    stdout: str
    stderr: str


async def run_command(
    program: str,
    *args: str,
    timeout: float,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
) -> RunOutput:
    """执行单条命令；超时按 TimeoutError 上抛，调用方转换失败语义。"""

    merged_env = {**os.environ, **dict(env)} if env else None
    process = await asyncio.create_subprocess_exec(
        program,
        *args,
        cwd=cwd,
        env=merged_env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        await _terminate(process)
        raise
    return RunOutput(
        code=process.returncode if process.returncode is not None else -1,
        stdout=stdout.decode("utf-8", "replace"),
        stderr=stderr.decode("utf-8", "replace"),
    )


async def _terminate(process: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError):
        process.kill()
    with contextlib.suppress(ProcessLookupError):
        await process.wait()


async def stream_command(
    program: str,
    *args: str,
    timeout: float,
    on_line: LineSink,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
) -> int:
    """逐行采集合并输出（stdout+stderr）并转发；返回退出码。"""

    merged_env = {**os.environ, **dict(env)} if env else None
    process = await asyncio.create_subprocess_exec(
        program,
        *args,
        cwd=cwd,
        env=merged_env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert process.stdout is not None
    try:
        async with asyncio.timeout(timeout):

            async def pump() -> None:
                while raw := await process.stdout.readline():
                    await on_line(raw.decode("utf-8", "replace").rstrip("\n"))

            await asyncio.gather(pump(), process.wait())
    except TimeoutError:
        await _terminate(process)
        raise
    return process.returncode if process.returncode is not None else -1
