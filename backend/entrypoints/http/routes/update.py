import zipfile
import zlib

import features.updates.operations as updates
from fastapi import File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from features.updates.release_contracts import ReleaseManifestResponse
from features.updates.schemas import UpdateVersionItem, UpdateVersionListResponse, UpdateVersionUpdate
from infrastructure.database import DbSession
from infrastructure.logger import get_logger
from infrastructure.request_errors import RequestError
from runtime.responses import MessageResponse

from entrypoints.http.auth import CurrentAdmin
from entrypoints.http.file_results import file_response
from entrypoints.http.routing import get_router

logger = get_logger(__name__)
router = get_router()


@router.get("/latest.yml", response_model=ReleaseManifestResponse)
async def get_latest_yml(db: DbSession) -> ReleaseManifestResponse:
    try:
        result = await updates.get_latest_yml(db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return result


@router.get("/latest-mac.yml", response_model=ReleaseManifestResponse)
async def get_latest_mac_yml(db: DbSession) -> ReleaseManifestResponse:
    try:
        result = await updates.get_latest_mac_yml(db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return result


@router.get("/latest-runner.yml", response_class=FileResponse)
async def get_latest_runner_yml(db: DbSession) -> FileResponse:
    """原样下发构建时签名的 Runner wheel 清单。"""
    try:
        result = await updates.get_latest_runner_yml(db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return file_response(result)


@router.get("/versions", response_model=UpdateVersionListResponse)
async def list_versions(_admin: CurrentAdmin, db: DbSession) -> UpdateVersionListResponse:
    try:
        result = await updates.list_versions(_admin=_admin, db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return result


# 条目损坏（CRC、压缩流、数据截断）、加密或压缩方式不受支持时，解压抛出的异常。
_BAD_ZIP_ERRORS = (zipfile.BadZipFile, zlib.error, EOFError, RuntimeError, OSError)


@router.post("/versions", response_model=UpdateVersionItem, status_code=201)
async def create_version(
    admin: CurrentAdmin,
    db: DbSession,
    file: UploadFile = File(...),
    release_notes: str = Form(""),
) -> UpdateVersionItem:
    # Build-UpdateZip 产出的更新包，必须含 Windows 安装程序（*.exe）。
    try:
        result = await updates.create_version(admin=admin, db=db, file=file, release_notes=release_notes)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return result


@router.patch("/versions/{id}", response_model=UpdateVersionItem)
async def update_version(
    id: int,
    payload: UpdateVersionUpdate,
    _admin: CurrentAdmin,
    db: DbSession,
) -> UpdateVersionItem:
    try:
        result = await updates.update_version(id=id, payload=payload, _admin=_admin, db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return result


@router.delete("/versions/{id}", response_model=MessageResponse)
async def delete_version(id: int, _admin: CurrentAdmin, db: DbSession) -> MessageResponse:
    try:
        result = await updates.delete_version(id=id, _admin=_admin, db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return result


@router.get("/{filename:path}", response_class=FileResponse)
async def get_latest_file(filename: str, db: DbSession) -> FileResponse:
    try:
        result = await updates.get_latest_file(filename=filename, db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return file_response(result)
