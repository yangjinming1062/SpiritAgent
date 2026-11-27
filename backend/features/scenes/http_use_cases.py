"""场景 HTTP 请求用例；持久状态、错误与交付 DTO 由场景模块维护。"""

import base64

from sqlalchemy.ext.asyncio import AsyncSession

from features.companion.schemas import CompanionOperationResponse
from features.scenes.models import SceneOrigin
from features.scenes.queries import get_scene, get_scene_state, list_scenes, response_for_scene
from features.scenes.schemas import (
    SceneDescriptionRequest,
    SceneGenerateRequest,
    SceneImportRequest,
    SceneListResponse,
    ScenePolicyRequest,
    ScenePolicyResponse,
    ScenePromptRequest,
    SceneResponse,
    SceneStateResponse,
)
from features.scenes.service import (
    SceneError,
    SceneNotFoundError,
    adopt_scene,
    delete_scene,
    discard_scene,
    edit_scene_description,
    regenerate_scene,
    retry_scene_description,
    schedule_scene_generation,
    schedule_scene_prompt,
    set_scene_policy,
)


def _decode_image(image_b64: str) -> bytes:
    """图片格式由场景服务按实际字节校验。"""
    try:
        return base64.b64decode(image_b64, validate=True)
    except ValueError:
        raise SceneError("图片数据无效") from None


async def read_scene_state(user_id: int, db: AsyncSession, *, tasks_only: bool = False) -> SceneStateResponse:
    state = await get_scene_state(db, user_id, tasks_only=tasks_only)
    rows = {row.id: row for row in (*state.ready, state.pending, state.regenerating) if row is not None}
    return SceneStateResponse(
        policy=state.policy,
        materials=[response_for_scene(row) for row in rows.values()],
        generation_pending=state.pending is not None or state.regenerating is not None,
    )


async def create_scene(user_id: int, body: SceneGenerateRequest | SceneImportRequest) -> SceneResponse:
    if isinstance(body, SceneImportRequest):
        return await _adopt_scene_image(user_id, body.scene_id, body)
    row = await schedule_scene_generation(
        user_id,
        origin=SceneOrigin.USER_REQUEST.value,
        notes=body.notes,
        reference_image=_decode_image(body.image) if body.image is not None else None,
    )
    return response_for_scene(row)


async def post_scene_prompt(user_id: int, body: ScenePromptRequest) -> SceneResponse:
    row = await schedule_scene_prompt(user_id, notes=body.notes)
    return response_for_scene(row)


async def _adopt_scene_image(user_id: int, scene_id: int | None, body: SceneImportRequest) -> SceneResponse:
    data = _decode_image(body.image)
    row = await adopt_scene(user_id, scene_id, data=data)
    return response_for_scene(row)


async def post_scene_discard(user_id: int, scene_id: int) -> SceneResponse:
    row = await discard_scene(user_id, scene_id)
    return response_for_scene(row)


async def post_scene_regenerate(user_id: int, scene_id: int) -> SceneResponse:
    row = await regenerate_scene(user_id, scene_id)
    return response_for_scene(row)


async def delete_scene_route(user_id: int, scene_id: int) -> CompanionOperationResponse:
    await delete_scene(user_id, scene_id)
    return CompanionOperationResponse(ok=True)


async def patch_scene_policy(user_id: int, db: AsyncSession, body: ScenePolicyRequest) -> ScenePolicyResponse:
    policy = await set_scene_policy(db, user_id, body.policy)
    return ScenePolicyResponse(policy=policy)


async def scene_list_route(
    user_id: int, db: AsyncSession, q: str = "", cursor: int | None = None, include_total: bool = False, limit: int = 30
) -> SceneListResponse:
    rows, total, next_cursor = await list_scenes(
        db, user_id, query=q, cursor=cursor, limit=limit, include_total=include_total
    )
    return SceneListResponse(
        scenes=[response_for_scene(row) for row in rows],
        total=total,
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
        limit=limit,
    )


async def scene_detail_route(user_id: int, db: AsyncSession, scene_id: int) -> SceneResponse:
    row = await get_scene(db, user_id, scene_id)
    if row is None:
        raise SceneNotFoundError("找不到对应场景")
    return response_for_scene(row)


async def scene_edit_route(user_id: int, scene_id: int, body: SceneDescriptionRequest) -> SceneResponse:
    row = await edit_scene_description(user_id, scene_id, body)
    return response_for_scene(row)


async def scene_analyze_route(user_id: int, scene_id: int) -> SceneResponse:
    row = await retry_scene_description(user_id, scene_id)
    return response_for_scene(row)
