from typing import Annotated, Literal

from fastapi import HTTPException, Path, Query
from features.conversation.request_contracts import PromptPresetListResponse, PromptPresetSummary
from features.conversation.slash_commands import list_commands_for_user
from features.memory.admin_contracts import (
    MemoryDeleteResponse,
    MemoryListResponse,
    MemoryProfileRequest,
    MemoryProfileResponse,
    MemoryUpdateRequest,
)
from features.memory.contracts import MemoryConflictError, MemoryListItem
from features.memory.memory_admin import update_memory, update_user_profile
from features.memory.use_cases import (
    forget_manual_memory,
    list_memory_view,
)
from infrastructure.constants import POSTGRES_INT4_MAX, POSTGRES_INT8_MAX
from infrastructure.http_contracts import operation_policy
from pydantic import BaseModel
from runtime.memory_scope import MemoryScope, validate_memory_scope
from runtime.presets import SYSTEM_PRESET_CATALOG

from entrypoints.http.auth import CurrentUser
from entrypoints.http.routing import get_router

router = get_router(prefix="/api", remote=True)


class CommandSummary(BaseModel):
    name: str
    aliases: list[str]
    description: str
    requires_confirmation: bool


class CommandListResponse(BaseModel):
    commands: list[CommandSummary]


@router.get(
    "/presets",
    response_model=PromptPresetListResponse,
)
async def get_presets(user: CurrentUser) -> PromptPresetListResponse:
    return PromptPresetListResponse(
        presets=[
            PromptPresetSummary(id=p.id, name=p.name, description=p.description, icon_key=p.icon_key)
            for p in SYSTEM_PRESET_CATALOG.values()
        ],
    )


@router.get(
    "/commands",
    response_model=CommandListResponse,
)
async def get_commands(user: CurrentUser) -> CommandListResponse:
    return CommandListResponse(commands=[CommandSummary.model_validate(row) for row in list_commands_for_user()])


@router.get(
    "/memories",
    response_model=MemoryListResponse,
)
async def get_memories(
    user: CurrentUser,
    system_preset_id: str,
    kind: str | None = None,
    status: Literal["active", "candidate", "invalidated", "expired"] = "active",
    tag: str | None = None,
    q: str | None = Query(default=None, pattern=r"^[^\x00]*$"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=POSTGRES_INT8_MAX),
) -> MemoryListResponse:
    scope = _scope(user.id, system_preset_id)
    try:
        return await list_memory_view(scope, kind=kind, status=status, tag=tag, query=q, limit=limit, offset=offset)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch(
    "/memories/profile",
    response_model=MemoryProfileResponse,
)
async def patch_memory_profile(user: CurrentUser, body: MemoryProfileRequest) -> MemoryProfileResponse:
    scope = _scope(user.id, body.system_preset_id)
    try:
        return MemoryProfileResponse(memories=await update_user_profile(scope, body.changes))
    except MemoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch(
    "/memories/{memory_id}",
    response_model=MemoryListItem,
    openapi_extra=operation_policy(wait_for_model=True),
)
async def patch_memory(
    user: CurrentUser,
    memory_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
    body: MemoryUpdateRequest,
) -> MemoryListItem:
    scope = _scope(user.id, body.system_preset_id)
    try:
        row = await update_memory(scope, memory_id, content=body.content, expected_version=body.expected_version)
    except MemoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return row


@router.delete(
    "/memories/{memory_id}",
    response_model=MemoryDeleteResponse,
)
async def delete_memory(
    user: CurrentUser,
    memory_id: Annotated[int, Path(gt=0, le=POSTGRES_INT4_MAX)],
    system_preset_id: str,
) -> MemoryDeleteResponse:
    scope = _scope(user.id, system_preset_id)
    if not await forget_manual_memory(scope, memory_id):
        raise HTTPException(status_code=404, detail="Memory not found")
    return MemoryDeleteResponse(deleted=True)


def _scope(user_id: int, preset_id: str) -> MemoryScope:
    scope = MemoryScope(user_id, preset_id)
    try:
        validate_memory_scope(scope)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return scope
