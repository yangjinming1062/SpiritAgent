import asyncio

from components import SETTINGS, get_logger, session_scope
from modules.channels import ChannelBinding
from sqlalchemy import select

from .base import ChannelAdapter, ChannelBindingSnapshot, ChannelError
from .bridge import stop_binding_turns
from .registry import resolve
from .state import update_binding_status

logger = get_logger(__name__)


class ChannelManager:
    """绑定任务的进程内生命周期管理：启动加载 + REST 触发启停 + 每绑定守卫重试。无周期对账循环——REST 变更直接调 start/stop_binding，任务死亡由守卫自愈（非 fatal 退避重建）；单 web 进程语义（backend/README.md「设计意图」）下不需要 omp-wechat 那套端口单例锁/failover。"""

    def __init__(self) -> None:
        # 强引用防 GC（沿 connection.py 的任务持有注释）；键 (user_id, channel) 与绑定唯一约束对齐。
        self._tasks: dict[tuple[int, str], asyncio.Task] = {}
        self._adapters: dict[tuple[int, str], ChannelAdapter] = {}
        self._lifecycle_lock = asyncio.Lock()

    def adapter(self, user_id: int, channel: str) -> ChannelAdapter | None:
        return self._adapters.get((user_id, channel))

    async def load_and_start(self) -> None:
        """启动路径：拉起所有未停用的绑定（lifespan 调用，幂等——restart 先停旧任务）。"""
        async with session_scope() as db:
            rows = (await db.execute(select(ChannelBinding).where(ChannelBinding.status != "disabled"))).scalars().all()
            targets = [(r.user_id, r.channel) for r in rows]
        for user_id, channel in targets:
            try:
                await self.restart_binding(user_id, channel)
            except Exception:
                logger.exception(
                    "failed to start channel binding at boot",
                    extra={"user_id": user_id, "channel": channel},
                )

    async def start_binding(self, user_id: int, channel: str) -> None:
        async with self._lifecycle_lock:
            await self._start_binding(user_id, channel)

    async def _start_binding(self, user_id: int, channel: str) -> None:
        """适配器在返回前构造并登记，调用方随后即可通过 ``adapter()`` 取到实例。"""
        key = (user_id, channel)
        existing = self._tasks.get(key)
        if existing is not None and not existing.done():
            return
        snapshot = await self._snapshot(user_id, channel)
        if snapshot is None:
            return
        adapter = await self._build_adapter(snapshot)
        if adapter is None:
            return
        self._tasks[key] = asyncio.create_task(
            self._run_guarded(adapter),
            name=f"channels.binding.{channel}.{user_id}",
        )

    async def stop_binding(self, user_id: int, channel: str) -> None:
        async with self._lifecycle_lock:
            await self._stop_binding(user_id, channel)

    async def _stop_binding(self, user_id: int, channel: str) -> ChannelAdapter | None:
        """取消守卫任务并收尾适配器；守卫尚未开始执行时由这里补做 aclose 与回合清理。"""
        key = (user_id, channel)
        task = self._tasks.pop(key, None)
        adapter = self._adapters.pop(key, None)
        if task is not None:
            task.cancel()
            (result,) = await asyncio.gather(task, return_exceptions=True)
            if isinstance(result, Exception):
                logger.error(
                    "channel binding task exited with error while stopping",
                    extra={"key": key},
                    exc_info=result,
                )
        if adapter is not None:
            await adapter.aclose()
            await stop_binding_turns(adapter.snapshot.id)
        return adapter

    async def restart_binding(self, user_id: int, channel: str) -> None:
        async with self._lifecycle_lock:
            await self._stop_binding(user_id, channel)
            await self._start_binding(user_id, channel)

    async def logout_binding(self, user_id: int, channel: str) -> bool:
        """在绑定生命周期边界内登出：先停止全部旧任务，再清凭据并以新快照重启。"""
        async with self._lifecycle_lock:
            adapter = await self._stop_binding(user_id, channel)
            if adapter is None:
                return False
            await adapter.logout()
            await self._start_binding(user_id, channel)
            return True

    async def pause_user_bindings(self, user_id: int) -> None:
        """维护边界：停止并等待该用户当前注册的全部绑定。"""
        async with self._lifecycle_lock:
            channels = {channel for uid, channel in (*self._tasks, *self._adapters) if uid == user_id}
            for channel in channels:
                await self._stop_binding(user_id, channel)

    async def resume_user_bindings(self, user_id: int) -> None:
        """维护结束：从数据库真源重新拉起该用户全部未停用绑定。"""
        async with session_scope() as db:
            channels = (
                (
                    await db.execute(
                        select(ChannelBinding.channel).where(
                            ChannelBinding.user_id == user_id,
                            ChannelBinding.status != "disabled",
                        ),
                    )
                )
                .scalars()
                .all()
            )
        async with self._lifecycle_lock:
            for channel in channels:
                await self._stop_binding(user_id, channel)
                await self._start_binding(user_id, channel)

    async def drain(self) -> None:
        """lifespan 关闭段：取消并等待全部绑定任务，避免持有连接池的协程逃过 shutdown。"""
        async with self._lifecycle_lock:
            keys = {*self._tasks, *self._adapters}
            results = await asyncio.gather(*(self._stop_binding(*key) for key in keys), return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.error("channel binding cleanup failed during drain", exc_info=result)

    async def _snapshot(self, user_id: int, channel: str) -> ChannelBindingSnapshot | None:
        async with session_scope() as db:
            row = (
                await db.execute(
                    select(ChannelBinding).where(ChannelBinding.user_id == user_id, ChannelBinding.channel == channel),
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return ChannelBindingSnapshot(
                id=row.id,
                user_id=row.user_id,
                channel=row.channel,
                credentials=row.credentials,
            )

    async def _build_adapter(self, snapshot: ChannelBindingSnapshot) -> ChannelAdapter | None:
        """构造并登记适配器；构造失败（渠道未注册等确定性错误）标 error 并返回 None，不影响其他绑定。"""
        try:
            adapter = resolve(snapshot.channel)(snapshot)
        except Exception as e:
            logger.exception(
                "channel adapter construction failed",
                extra={"user_id": snapshot.user_id, "channel": snapshot.channel},
            )
            await update_binding_status(snapshot.id, "error", error=str(e))
            return None
        self._adapters[(snapshot.user_id, snapshot.channel)] = adapter
        return adapter

    async def _run_guarded(self, adapter: ChannelAdapter) -> None:
        """守卫循环：fatal ChannelError → 标 error 停止；其他异常/意外返回 → 退避后重建适配器重试。"""
        snapshot = adapter.snapshot
        key = (snapshot.user_id, snapshot.channel)
        while True:
            try:
                # 需要登录且尚无凭据的渠道停在 login_pending 等扫码；connected 由登录完成路径迁移。
                if adapter.requires_login and not adapter.has_credentials():
                    await update_binding_status(snapshot.id, "login_pending")
                else:
                    await update_binding_status(snapshot.id, "connected")
                await adapter.run()
                logger.warning("channel adapter run() returned unexpectedly; restarting", extra={"key": key})
            except asyncio.CancelledError:
                raise
            except ChannelError as e:
                if e.fatal:
                    logger.error("channel binding fatal error", extra={"key": key, "error": str(e)})
                    await update_binding_status(snapshot.id, "error", error=str(e))
                    return
                logger.warning("channel adapter transient error; backing off", extra={"key": key, "error": str(e)})
            except Exception:
                logger.exception("channel adapter crashed; backing off", extra={"key": key})
            finally:
                if self._adapters.get(key) is adapter:
                    self._adapters.pop(key, None)
                try:
                    await adapter.aclose()
                except Exception:
                    logger.exception("channel adapter cleanup failed", extra={"key": key})
                finally:
                    await stop_binding_turns(snapshot.id)
            await asyncio.sleep(SETTINGS.channels_restart_backoff_seconds)
            # 重启前刷新快照（凭据/配置可能已被 REST 更新）。
            fresh = await self._snapshot(snapshot.user_id, snapshot.channel)
            if fresh is None:
                # 绑定行已删除（DELETE 竞速）：静默退出，不必标状态。
                return
            snapshot = fresh
            rebuilt = await self._build_adapter(snapshot)
            if rebuilt is None:
                return
            adapter = rebuilt


MANAGER = ChannelManager()


async def start_channel_manager() -> None:
    await MANAGER.load_and_start()


async def stop_channel_manager() -> None:
    await MANAGER.drain()
