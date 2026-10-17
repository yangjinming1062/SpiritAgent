"""业务有效性与队列取消由 bootstrap 显式装配。"""

from collections.abc import Awaitable, Callable, Mapping

from .contracts import VideoQueueKind, VideoQueueOwner, VideoQueueReadiness

type OwnerInspector = Callable[[VideoQueueOwner, bool, str | None], Awaitable[VideoQueueReadiness]]
type CancellationHandler = Callable[[str], Awaitable[bool]]

_INSPECTORS: dict[VideoQueueKind, OwnerInspector] = {}
_CANCEL: CancellationHandler | None = None


def configure_video_queue_ports(
    inspectors: Mapping[VideoQueueKind, OwnerInspector],
    cancel: CancellationHandler,
) -> None:
    global _CANCEL
    _INSPECTORS.clear()
    _INSPECTORS.update(inspectors)
    _CANCEL = cancel


async def inspect_owner(
    owner: VideoQueueOwner,
    before_submit: bool = True,
    request_id: str | None = None,
) -> VideoQueueReadiness:
    inspector = _INSPECTORS.get(owner.kind)
    if inspector is None:
        raise RuntimeError("Video queue owner inspector is not configured")
    return await inspector(owner, before_submit, request_id)


async def cancel_queue_request(request_id: str) -> None:
    if _CANCEL is None:
        raise RuntimeError("Video queue cancellation handler is not configured")
    await _CANCEL(request_id)
