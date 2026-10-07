import asyncio
import json
import re
import shutil
import tempfile
import zipfile
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from common import get_or_404, get_router
from components import DbSession, apply_partial, get_logger, sha512_b64
from fastapi import File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from modules.auth import CurrentAdmin
from modules.system import MessageResponse, ReleaseManifestFileItem, ReleaseManifestResponse
from modules.update import UpdateVersion, UpdateVersionItem, UpdateVersionListResponse, UpdateVersionUpdate
from services.domains.update_releases import VERSIONS_DIR
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)
router = get_router()

CHUNK_SIZE = 1024 * 1024
# 版本目录与数据库记录须成对出现：上传与删除在进程内串行（后端单 web 进程部署），避免并发写同一目录或互删。
_VERSION_LOCK = asyncio.Lock()

ALLOWED_ARCHIVE_SUFFIXES = (
    ".exe",
    ".blockmap",
    ".zip",
    ".dmg",
    ".whl",
    "server.py",
    "manifest.json",
    "latest-runner.yml",
)

DOWNLOAD_SUFFIXES = (
    ".exe",
    ".blockmap",
    ".zip",
    ".dmg",
    ".dmg.blockmap",
    ".whl",
    "server.py",
    "latest-runner.yml",
)


async def _get_latest(db: AsyncSession) -> UpdateVersion:
    latest = (
        (
            await db.execute(
                select(UpdateVersion)
                .where(UpdateVersion.is_active.is_(True))
                .order_by(UpdateVersion.created_at.desc()),
            )
        )
        .scalars()
        .first()
    )
    # 请求级会话要到响应（含安装包下载）发送完毕才关闭，读完即提交，不让连接占用到那时。
    await db.commit()
    if latest is None:
        raise HTTPException(status_code=404, detail="No active version")
    return latest


def _pick_asset(versions_dir: Path, *patterns: str) -> Path | None:
    """按调用顺序拼接 pattern 后排序，取 versions_dir 中最后一条匹配文件。"""
    candidates = [match for pattern in patterns for match in versions_dir.glob(pattern)]
    return sorted(candidates)[-1] if candidates else None


def _release_manifest(
    latest: UpdateVersion,
    filename: str | None,
    sha512: str | None,
    size: int | None,
) -> ReleaseManifestResponse:
    """electron-updater 平台清单；文件名、摘要与大小在上传时一并落库。"""
    if not filename or not sha512 or size is None:
        raise HTTPException(status_code=404, detail="No active release for this platform")
    return ReleaseManifestResponse(
        version=latest.version,
        releaseDate=latest.created_at.isoformat(),
        releaseNotes=latest.release_notes,
        path=filename,
        sha512=sha512,
        files=[ReleaseManifestFileItem(url=filename, sha512=sha512, size=size)],
    )


@router.get("/latest.yml", response_model=ReleaseManifestResponse)
async def get_latest_yml(db: DbSession) -> ReleaseManifestResponse:
    latest = await _get_latest(db)
    return _release_manifest(latest, latest.exe_filename, latest.exe_sha512, latest.exe_size)


@router.get("/latest-mac.yml", response_model=ReleaseManifestResponse)
async def get_latest_mac_yml(db: DbSession) -> ReleaseManifestResponse:
    latest = await _get_latest(db)
    return _release_manifest(latest, latest.mac_filename, latest.mac_sha512, latest.mac_size)


@router.get("/latest-runner.yml", response_class=FileResponse)
async def get_latest_runner_yml(db: DbSession) -> FileResponse:
    """提供 Build-UpdateZip 写入的签名 runner manifest：desktop 主进程在重启前读取它、本地暂存 wheel + server.py，校验通过后才允许点 "Restart"；下次启动由新 Electron 跑 installPending 执行 pip install --upgrade 并覆盖 server.py。"""
    latest = await _get_latest(db)
    if not latest.runner_filename:
        raise HTTPException(status_code=404, detail="No active release with a runner asset")
    manifest_path = VERSIONS_DIR / latest.version / "latest-runner.yml"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="latest-runner.yml not found")
    return FileResponse(path=str(manifest_path), media_type="application/yaml", filename="latest-runner.yml")


@router.get("/versions", response_model=UpdateVersionListResponse)
async def list_versions(_admin: CurrentAdmin, db: DbSession) -> UpdateVersionListResponse:
    records = (await db.execute(select(UpdateVersion).order_by(UpdateVersion.created_at.desc()))).scalars().all()
    return UpdateVersionListResponse(items=[UpdateVersionItem.model_validate(record) for record in records])


# 条目损坏（CRC、压缩流、数据截断）、加密或压缩方式不受支持时，解压抛出的异常。
_BAD_ZIP_ERRORS = (zipfile.BadZipFile, zlib.error, EOFError, RuntimeError, OSError)


def _extract_archive_entries(zip_path: Path, versions_dir: Path) -> None:
    """从更新 zip 中解压允许的 desktop + runner 条目。"""
    versions_dir_resolved = versions_dir.resolve()
    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            if not name.endswith(ALLOWED_ARCHIVE_SUFFIXES):
                continue
            target = (versions_dir / name).resolve()
            if not target.is_relative_to(versions_dir_resolved):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)


def _inspect_release(versions_dir: Path, version: str) -> tuple[Path, Path | None, Path]:
    """校验同版本桌面安装包、Runner 资产与清单，返回 (exe, mac 包, runner wheel)。"""
    manifest_path = versions_dir / "manifest.json"
    if not manifest_path.exists():
        raise ValueError("更新包缺少 manifest.json。")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if not isinstance(manifest, dict):
            raise ValueError("manifest must be an object")
        manifest_version = manifest.get("version")
    except (ValueError, UnicodeError) as exc:
        raise ValueError("manifest.json 无法解析。") from exc
    if not manifest_version:
        raise ValueError("manifest.json 缺少 version 字段。")
    if manifest_version != version:
        raise ValueError(f"manifest.json 的版本 {manifest_version} 与文件名中的版本 {version} 不一致。")
    exe_file = _pick_asset(versions_dir, "*.exe")
    if exe_file is None:
        raise ValueError("更新包缺少 Windows 安装程序（*.exe）。")
    # Runner 侧：wheel + server.py 解压到 runner/，latest-runner.yml 位于根。
    wheel_file = _pick_asset(versions_dir, "runner/spirit_agent-*.whl")
    if wheel_file is None:
        raise ValueError("更新包缺少 Runner wheel（runner/spirit_agent-*.whl）。")
    if not (versions_dir / "runner" / "server.py").is_file():
        raise ValueError("更新包缺少 runner/server.py。")
    runner_manifest_path = versions_dir / "latest-runner.yml"
    if not runner_manifest_path.is_file():
        raise ValueError("更新包缺少 latest-runner.yml。")
    try:
        manifest_text = runner_manifest_path.read_text(encoding="utf-8-sig")
        if manifest_text.lstrip().startswith("{"):
            runner_manifest = json.loads(manifest_text)
            runner_version = runner_manifest.get("version") if isinstance(runner_manifest, dict) else None
        else:
            match = re.search(
                r"(?m)^[ \t]*version[ \t]*:[ \t]*['\"]?(\d+\.\d+\.\d+)['\"]?[ \t]*(?:#.*)?$",
                manifest_text,
            )
            runner_version = match.group(1) if match else None
    except (ValueError, UnicodeError) as exc:
        raise ValueError("latest-runner.yml 无法解析。") from exc
    if runner_version != version:
        raise ValueError("latest-runner.yml 的版本与上传版本不一致。")
    return exe_file, _pick_asset(versions_dir, "*.zip", "*.dmg"), wheel_file


def _replace_version_dir(stage: Path, versions_dir: Path) -> None:
    """把校验通过的暂存目录改名为版本目录（同一文件系统，原子）。库中没有该版本，已存在的目录必是中断上传的残留，整体替换。"""
    if versions_dir.exists():
        shutil.rmtree(versions_dir)
    stage.rename(versions_dir)


async def _run_release_io[T](function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """取消仍等文件线程退出，暂存目录与上传流才能安全关闭。"""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise


@router.post("/versions", response_model=UpdateVersionItem, status_code=201)
async def create_version(
    admin: CurrentAdmin,
    db: DbSession,
    file: UploadFile = File(...),
    release_notes: str = Form(""),
) -> UpdateVersionItem:
    # Build-UpdateZip 产出的更新包，必须含 Windows 安装程序（*.exe）。
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="上传文件必须是 .zip。")

    # 限定为纯 semver——该值会成为 VERSIONS_DIR 下的路径片段，否则文件名可逃出目录。
    if not (match := re.search(r"\d+\.\d+\.\d+", file.filename)):
        raise HTTPException(status_code=400, detail="文件名须包含 1.2.3 形式的版本号。")
    version = match.group(0)

    versions_dir = VERSIONS_DIR / version
    async with _VERSION_LOCK:
        if (await db.execute(select(UpdateVersion).where(UpdateVersion.version == version))).scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"版本 {version} 已存在。")
        # 上传、解压与摘要计算耗时较长，查重后先结束读事务。
        await db.commit()

        VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
        # upload.zip 在请求私有的临时目录暂存，解压与校验也在其中完成，通过后才原子换入版本目录。
        with tempfile.TemporaryDirectory(dir=VERSIONS_DIR, prefix=f".upload_{version}_") as tmp_dir:
            zip_path = Path(tmp_dir) / "upload.zip"
            stage = Path(tmp_dir) / "stage"
            stage.mkdir()
            # 上传包可达数百 MB：写盘与解压移出事件循环
            with open(zip_path, "wb") as f:
                while chunk := await file.read(CHUNK_SIZE):
                    await _run_release_io(f.write, chunk)

            try:
                await _run_release_io(_extract_archive_entries, zip_path, stage)
            except _BAD_ZIP_ERRORS as exc:
                if isinstance(exc, OSError) and exc.errno is not None:
                    logger.exception("release extraction storage failed")
                    raise HTTPException(status_code=503, detail="更新包存储暂时不可用。") from exc
                raise HTTPException(status_code=400, detail="无效 zip 文件。") from exc
            try:
                exe_file, mac_file, wheel_file = await _run_release_io(_inspect_release, stage, version)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            # 摘要与大小在换入前算好：换入后 stage 下的路径失效。
            record = UpdateVersion(
                version=version,
                release_notes=release_notes,
                exe_filename=exe_file.name,
                exe_sha512=await _run_release_io(sha512_b64, exe_file),
                exe_size=exe_file.stat().st_size,
                mac_filename=mac_file.name if mac_file else None,
                mac_sha512=await _run_release_io(sha512_b64, mac_file) if mac_file else None,
                mac_size=mac_file.stat().st_size if mac_file else None,
                runner_filename=f"runner/{wheel_file.name}",
                is_active=True,
                created_by=admin,
            )
            # 提交失败时删除刚换入的目录；被取消时提交结果未知，目录保留：已入库即为发布，否则是残留，下次同版本上传会替换。
            try:
                await _run_release_io(_replace_version_dir, stage, versions_dir)
                db.add(record)
                await db.commit()
            except Exception:
                try:
                    await _run_release_io(shutil.rmtree, versions_dir)
                except OSError:
                    logger.warning(
                        "failed to clean rejected release directory",
                        extra={"version": version},
                        exc_info=True,
                    )
                raise
    await db.refresh(record)
    return UpdateVersionItem.model_validate(record)


@router.patch("/versions/{id}", response_model=UpdateVersionItem)
async def update_version(
    id: int,
    payload: UpdateVersionUpdate,
    _admin: CurrentAdmin,
    db: DbSession,
) -> UpdateVersionItem:
    record = await get_or_404(db, UpdateVersion, id=id, detail="版本不存在。")
    apply_partial(record, payload)
    await db.commit()
    return UpdateVersionItem.model_validate(record)


@router.delete("/versions/{id}", response_model=MessageResponse)
async def delete_version(id: int, _admin: CurrentAdmin, db: DbSession) -> MessageResponse:
    async with _VERSION_LOCK:
        record = await get_or_404(db, UpdateVersion, id=id, detail="版本不存在。")
        versions_dir = VERSIONS_DIR / record.version
        # 先删记录再删目录：中途失败只留下无记录的残留目录，同版本再次上传时会整体替换。
        await db.delete(record)
        await db.commit()
        if versions_dir.exists():
            try:
                await asyncio.to_thread(shutil.rmtree, versions_dir)
            except OSError:
                logger.warning("update version files removal failed", extra={"path": str(versions_dir)}, exc_info=True)
    return MessageResponse(message="Version deleted")


@router.get("/{filename:path}", response_class=FileResponse)
async def get_latest_file(filename: str, db: DbSession) -> FileResponse:
    latest = await _get_latest(db)
    if not any(filename.endswith(s) for s in DOWNLOAD_SUFFIXES):
        raise HTTPException(status_code=400, detail="Invalid filename")
    base_dir = (VERSIONS_DIR / latest.version).resolve()
    file_path = (base_dir / filename).resolve()
    if not file_path.is_relative_to(base_dir):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path=str(file_path), media_type="application/octet-stream", filename=filename)
