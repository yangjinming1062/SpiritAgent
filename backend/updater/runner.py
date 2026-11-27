"""子进程执行：超时、日志采集与收尾。"""

import asyncio
import contextlib
import os
import signal
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass

from infrastructure.async_io import settle_task

LineSink = Callable[[str], Awaitable[None]]


@dataclass(slots=True)
class RunOutput:
    code: int
    stdout: str
    stderr: str


@contextlib.asynccontextmanager
async def _command(
    program: str, args: tuple[str, ...], timeout: float, env: Mapping[str, str] | None, cwd: str | None, *, stream: bool
) -> AsyncIterator[asyncio.subprocess.Process]:
    process = await asyncio.create_subprocess_exec(
        program,
        *args,
        cwd=cwd,
        env={**os.environ, **env} if env else None,
        start_new_session=True,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT if stream else asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(timeout):
            yield process
    except BaseException:
        # Git/Compose 可能已派生持有输出管道的子进程，整组停止后抽干输出并回收。
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await settle_task(asyncio.create_task(process.communicate()))
        raise


async def run_command(
    program: str, *args: str, timeout: float, env: Mapping[str, str] | None = None, cwd: str | None = None
) -> RunOutput:
    async with _command(program, args, timeout, env, cwd, stream=False) as process:
        stdout, stderr = await process.communicate()
        return RunOutput(await process.wait(), stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace"))


async def stream_command(
    program: str,
    *args: str,
    timeout: float,
    on_line: LineSink,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
) -> int:
    async with _command(program, args, timeout, env, cwd, stream=True) as process:
        assert process.stdout is not None
        while raw := await process.stdout.readline():
            await on_line(raw.decode("utf-8", "replace").rstrip("\n"))
        return await process.wait()
