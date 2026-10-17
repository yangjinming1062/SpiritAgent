from typing import Annotated

import features.updates.operations as updates
from fastapi import File, Form, Path, UploadFile
from fastapi.responses import FileResponse
from features.updates.release_contracts import ReleaseManifestResponse
from features.updates.schemas import UpdateVersionItem, UpdateVersionListResponse, UpdateVersionUpdate
from infrastructure.constants import POSTGRES_INT4_MAX
from infrastructure.database import DbSession
from infrastructure.http_contracts import operation_policy
from runtime.responses import MessageResponse

from entrypoints.http.auth import CurrentAdmin
from entrypoints.http.file_results import file_response
from entrypoints.http.routing import get_router

router = get_router(identity="public")
management_router = get_router(prefix="/api/admin/updates", identity="admin")


@router.get(
    "/latest.yml",
    response_model=ReleaseManifestResponse,
)
async def get_latest_yml(db: DbSession) -> ReleaseManifestResponse:
    return await updates.get_latest_yml(db=db)


@router.get(
    "/latest-mac.yml",
    response_model=ReleaseManifestResponse,
)
async def get_latest_mac_yml(db: DbSession) -> ReleaseManifestResponse:
    return await updates.get_latest_mac_yml(db=db)


@router.get("/latest-runner.yml", response_class=FileResponse)
async def get_latest_runner_yml(db: DbSession) -> FileResponse:
    """原样下发构建时签名的 Runner wheel 清单。"""
    result = await updates.get_latest_runner_yml(db=db)
    return file_response(result)


@management_router.get(
    "/versions",
    response_model=UpdateVersionListResponse,
    openapi_extra=operation_policy(identities=("admin",)),
)
async def list_versions(_admin: CurrentAdmin, db: DbSession) -> UpdateVersionListResponse:
    return await updates.list_versions(_admin=_admin, db=db)


@management_router.post(
    "/versions",
    response_model=UpdateVersionItem,
    status_code=201,
    openapi_extra=operation_policy(identities=("admin",), upload="release"),
)
async def create_version(
    admin: CurrentAdmin,
    db: DbSession,
    file: UploadFile = File(...),
    release_notes: Annotated[str, Form(pattern=r"^[^\x00]*$")] = "",
) -> UpdateVersionItem:
    # Build-UpdateZip 产出的更新包，必须含 Windows 安装程序（*.exe）。
    return await updates.create_version(admin=admin, db=db, file=file, release_notes=release_notes)


@management_router.patch(
    "/versions/{id}",
    response_model=UpdateVersionItem,
    openapi_extra=operation_policy(identities=("admin",)),
)
async def update_version(
    id: Annotated[int, Path(ge=1, le=POSTGRES_INT4_MAX)],
    payload: UpdateVersionUpdate,
    _admin: CurrentAdmin,
    db: DbSession,
) -> UpdateVersionItem:
    return await updates.update_version(id=id, payload=payload, _admin=_admin, db=db)


@management_router.delete(
    "/versions/{id}",
    response_model=MessageResponse,
    openapi_extra=operation_policy(identities=("admin",)),
)
async def delete_version(
    id: Annotated[int, Path(ge=1, le=POSTGRES_INT4_MAX)],
    _admin: CurrentAdmin,
    db: DbSession,
) -> MessageResponse:
    return await updates.delete_version(id=id, _admin=_admin, db=db)


@router.get("/{filename:path}", response_class=FileResponse)
async def get_latest_file(filename: str, db: DbSession) -> FileResponse:
    result = await updates.get_latest_file(filename=filename, db=db)
    return file_response(result)
