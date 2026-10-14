import asyncio
from typing import Any


class PageEvents:
    """Navigation and lifecycle waiters, used only on the CDP event loop."""

    def __init__(self) -> None:
        self._frame_navigated_waiters: dict[str, list[asyncio.Future[dict[str, Any]]]] = {}
        self._lifecycle_waiters: dict[tuple[str, str], list[asyncio.Future[dict[str, Any]]]] = {}

    def await_frame_navigated(self, frame_id: str) -> asyncio.Future[dict[str, Any]]:
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        bucket = self._frame_navigated_waiters.setdefault(frame_id, [])
        bucket.append(fut)
        fut.add_done_callback(lambda f, fid=frame_id: self.pop_waiter(self._frame_navigated_waiters, fid, f))
        return fut

    def await_lifecycle(self, loader_id: str, name: str) -> asyncio.Future[dict[str, Any]]:
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        key = (loader_id, name)
        bucket = self._lifecycle_waiters.setdefault(key, [])
        bucket.append(fut)
        fut.add_done_callback(lambda f, k=key: self.pop_waiter(self._lifecycle_waiters, k, f))
        return fut

    def pop_waiter(
        self,
        store: dict[Any, list[asyncio.Future[dict[str, Any]]]],
        key: object,
        fut: asyncio.Future[dict[str, Any]],
    ) -> None:
        bucket = store.get(key)
        if bucket is None:
            return
        if fut in bucket:
            bucket.remove(fut)
        if not bucket:
            store.pop(key, None)

    def dispatch_frame_navigated(self, params: dict[str, Any]) -> None:
        fid = params.get("frame", {}).get("id", "")
        waiters = self._frame_navigated_waiters.pop(fid, ()) or ()
        for fut in waiters:
            if not fut.done():
                fut.set_result(params)

    def dispatch_lifecycle(self, params: dict[str, Any]) -> None:
        waiters = self._lifecycle_waiters.pop((params.get("loaderId", ""), params.get("name", "")), ()) or ()
        for fut in waiters:
            if not fut.done():
                fut.set_result(params)
