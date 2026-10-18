"""共享动作编排由 bootstrap 注入，桌景模块只持有制作与呈现职责。"""

from collections.abc import Awaitable, Callable
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from .schemas import DesktopVideoDesignRequest, DesktopVideoProposalResponse


class DesktopActionDesignHandler(Protocol):
    async def __call__(
        self,
        user_id: int,
        request: DesktopVideoDesignRequest,
        *,
        source: str,
        require_desktop: bool,
    ) -> DesktopVideoProposalResponse: ...


_design_handler: DesktopActionDesignHandler | None = None
_proposal_query_handler: Callable[[AsyncSession, int, int], Awaitable[DesktopVideoProposalResponse]] | None = None
_proposal_list_handler: Callable[[AsyncSession, int, int], Awaitable[list[DesktopVideoProposalResponse]]] | None = None
_set_created_handler: Callable[[int, int], Awaitable[None]] | None = None


def set_desktop_action_design_handler(handler: DesktopActionDesignHandler) -> None:
    global _design_handler
    _design_handler = handler


def set_desktop_proposal_query_handler(
    handler: Callable[[AsyncSession, int, int], Awaitable[DesktopVideoProposalResponse]],
) -> None:
    global _proposal_query_handler
    _proposal_query_handler = handler


def set_desktop_proposal_list_handler(
    handler: Callable[[AsyncSession, int, int], Awaitable[list[DesktopVideoProposalResponse]]],
) -> None:
    global _proposal_list_handler
    _proposal_list_handler = handler


def set_desktop_set_created_handler(handler: Callable[[int, int], Awaitable[None]]) -> None:
    global _set_created_handler
    _set_created_handler = handler


async def design_shared_desktop_action(
    user_id: int,
    request: DesktopVideoDesignRequest,
    *,
    source: str,
    require_desktop: bool,
) -> DesktopVideoProposalResponse:
    if _design_handler is None:
        raise RuntimeError("共享动作设计尚未装配")
    return await _design_handler(user_id, request, source=source, require_desktop=require_desktop)


async def shared_desktop_proposal(
    db: AsyncSession,
    user_id: int,
    proposal_id: int,
) -> DesktopVideoProposalResponse:
    if _proposal_query_handler is None:
        raise RuntimeError("共享动作查询尚未装配")
    return await _proposal_query_handler(db, user_id, proposal_id)


async def shared_desktop_proposals(
    db: AsyncSession,
    user_id: int,
    set_id: int,
) -> list[DesktopVideoProposalResponse]:
    return await _proposal_list_handler(db, user_id, set_id) if _proposal_list_handler is not None else []


async def desktop_set_created(user_id: int, set_id: int) -> None:
    if _set_created_handler is None:
        raise RuntimeError("共享动作同步尚未装配")
    await _set_created_handler(user_id, set_id)
