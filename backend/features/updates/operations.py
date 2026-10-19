"""update resource operations; transaction and failure semantics belong to the owning capability."""

import asyncio
import json
import re
import shutil
import tempfile
import zipfile
import zlib
from pathlib import Path

from infrastructure.async_io import run_blocking
from infrastructure.file_results import FileResult
from infrastructure.hashing import sha512_b64
from infrastructure.logger import get_logger
from infrastructure.queries import get_or_404
from infrastructure.request_errors import RequestError
from infrastructure.text import apply_partial
from infrastructure.uploads import UploadSource
from runtime.responses import MessageResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.updates.models import UpdateVersion
from features.updates.release_contracts import ReleaseManifestFileItem, ReleaseManifestResponse
from features.updates.releases import VERSIONS_DIR
from features.updates.schemas import UpdateVersionItem, UpdateVersionListResponse, UpdateVersionUpdate

logger = get_logger(__name__)


CHUNK_SIZE = 1024 * 1024


_VERSION_LOCK = asyncio.Lock()


ALLOWED_ARCHIVE_SUFFIXES = (
    ".exe",
    ".blockmap",
    ".zip",
    ".dmg",
    ".whl",
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
    "latest-runner.yml",
)


_BAD_ZIP_ERRORS = (zipfile.BadZipFile, zlib.error, EOFError, RuntimeError, OSError)


async def _get_latest(db: AsyncSession) -> UpdateVersion:
    latest = await db.scalar(
        select(UpdateVersion)
        .where(UpdateVersion.is_active.is_(True))
        .order_by(UpdateVersion.created_at.desc())
        .limit(1),
    )
    # 请求级会话要到响应（含安装包下载）发送完毕才关闭，读完即提交，不让连接占用到那时。
    await db.commit()
    if latest is None:
        raise RequestError(status_code=404, message="No active version")
    return latest


def _pick_asset(versions_dir: Path, *patterns: str) -> Path | None:
    """取 versions_dir 中按路径排序最后一条匹配文件。"""
    return max((match for pattern in patterns for match in versions_dir.glob(pattern)), default=None)


def _release_manifest(
    latest: UpdateVersion,
    filename: str | None,
    sha512: str | None,
    size: int | None,
) -> ReleaseManifestResponse:
    """electron-updater 平台清单；文件名、摘要与大小在上传时一并落库。"""
    if not filename or not sha512 or size is None:
        raise RequestError(status_code=404, message="No active release for this platform")
    return ReleaseManifestResponse(
        version=latest.version,
        releaseDate=latest.created_at.isoformat(),
        releaseNotes=latest.release_notes,
        path=filename,
        sha512=sha512,
        files=[ReleaseManifestFileItem(url=filename, sha512=sha512, size=size)],
    )


async def get_latest_yml(db: AsyncSession) -> ReleaseManifestResponse:
    latest = await _get_latest(db)
    return _release_manifest(latest, latest.exe_filename, latest.exe_sha512, latest.exe_size)


async def get_latest_mac_yml(db: AsyncSession) -> ReleaseManifestResponse:
    latest = await _get_latest(db)
    return _release_manifest(latest, latest.mac_filename, latest.mac_sha512, latest.mac_size)


async def get_latest_runner_yml(db: AsyncSession) -> FileResult:
    """提供构建时签名的 Runner wheel 清单，供 Client 预取、验签与重启后安装。"""
    latest = await _get_latest(db)
    if not latest.runner_filename:
        raise RequestError(status_code=404, message="No active release with a runner asset")
    manifest_path = VERSIONS_DIR / latest.version / "latest-runner.yml"
    if not manifest_path.exists():
        raise RequestError(status_code=404, message="latest-runner.yml not found")
    return FileResult(path=str(manifest_path), media_type="application/yaml", filename="latest-runner.yml")


async def list_versions(_admin: str, db: AsyncSession) -> UpdateVersionListResponse:
    records = (await db.execute(select(UpdateVersion).order_by(UpdateVersion.created_at.desc()))).scalars().all()
    return UpdateVersionListResponse(items=[UpdateVersionItem.model_validate(record) for record in records])


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
    # Runner wheel 包含入口；latest-runner.yml 位于更新包根目录。
    wheel_files = sorted(versions_dir.glob("runner/*.whl"))
    if len(wheel_files) != 1 or not wheel_files[0].name.startswith(f"spirit_agent-{version}-"):
        raise ValueError("更新包须包含唯一的同版本 Runner wheel（runner/spirit_agent-<version>-*.whl）。")
    wheel_file = wheel_files[0]
    if manifest.get("runner_wheel") != f"runner/{wheel_file.name}":
        raise ValueError("manifest.json 的 runner_wheel 与更新包中的 wheel 不一致。")
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


async def create_version(admin: str, db: AsyncSession, file: UploadSource, release_notes: str) -> UpdateVersionItem:
    if not file.filename or not file.filename.endswith(".zip"):
        raise RequestError(status_code=400, message="上传文件必须是 .zip。")

    # 限定为纯 semver——该值会成为 VERSIONS_DIR 下的路径片段，否则文件名可逃出目录。
    if not (match := re.search(r"\d+\.\d+\.\d+", file.filename)):
        raise RequestError(status_code=400, message="文件名须包含 1.2.3 形式的版本号。")
    version = match.group(0)

    versions_dir = VERSIONS_DIR / version
    async with _VERSION_LOCK:
        if (await db.execute(select(UpdateVersion).where(UpdateVersion.version == version))).scalar_one_or_none():
            raise RequestError(status_code=400, message=f"版本 {version} 已存在。")
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
                await run_blocking(shutil.copyfileobj, file.file, f, CHUNK_SIZE)

            try:
                await run_blocking(_extract_archive_entries, zip_path, stage)
            except _BAD_ZIP_ERRORS as exc:
                if isinstance(exc, OSError) and exc.errno is not None:
                    logger.exception("release extraction storage failed")
                    raise RequestError(status_code=503, message="更新包存储暂时不可用。") from exc
                raise RequestError(status_code=400, message="无效 zip 文件。") from exc
            try:
                exe_file, mac_file, wheel_file = await run_blocking(_inspect_release, stage, version)
            except ValueError as exc:
                raise RequestError(status_code=400, message=str(exc)) from exc

            # 摘要与大小在换入前算好：换入后 stage 下的路径失效。
            record = UpdateVersion(
                version=version,
                release_notes=release_notes,
                exe_filename=exe_file.name,
                exe_sha512=await run_blocking(sha512_b64, exe_file),
                exe_size=exe_file.stat().st_size,
                mac_filename=mac_file.name if mac_file else None,
                mac_sha512=await run_blocking(sha512_b64, mac_file) if mac_file else None,
                mac_size=mac_file.stat().st_size if mac_file else None,
                runner_filename=f"runner/{wheel_file.name}",
                is_active=True,
                created_by=admin,
            )
            # 提交失败时删除刚换入的目录；被取消时提交结果未知，目录保留：已入库即为发布，否则是残留，下次同版本上传会替换。
            try:
                await run_blocking(_replace_version_dir, stage, versions_dir)
                db.add(record)
                await db.commit()
            except Exception:
                try:
                    await run_blocking(shutil.rmtree, versions_dir)
                except OSError:
                    logger.warning(
                        "failed to clean rejected release directory",
                        extra={"version": version},
                        exc_info=True,
                    )
                raise
    await db.refresh(record)
    return UpdateVersionItem.model_validate(record)


async def update_version(id: int, payload: UpdateVersionUpdate, _admin: str, db: AsyncSession) -> UpdateVersionItem:
    record = await get_or_404(db, UpdateVersion, id=id, message="版本不存在。")
    apply_partial(record, payload)
    await db.commit()
    return UpdateVersionItem.model_validate(record)


async def delete_version(id: int, _admin: str, db: AsyncSession) -> MessageResponse:
    async with _VERSION_LOCK:
        record = await get_or_404(db, UpdateVersion, id=id, message="版本不存在。")
        versions_dir = VERSIONS_DIR / record.version
        # 先删记录再删目录：中途失败只留下无记录的残留目录，同版本再次上传时会整体替换。
        await db.delete(record)
        await db.commit()
        if versions_dir.exists():
            try:
                await run_blocking(shutil.rmtree, versions_dir)
            except OSError:
                logger.warning("update version files removal failed", extra={"path": str(versions_dir)}, exc_info=True)
    return MessageResponse(message="Version deleted")


async def get_latest_file(filename: str, db: AsyncSession) -> FileResult:
    latest = await _get_latest(db)
    if "\x00" in filename or not filename.endswith(DOWNLOAD_SUFFIXES):
        raise RequestError(status_code=400, message="Invalid filename")
    base_dir = (VERSIONS_DIR / latest.version).resolve()
    try:
        file_path = (base_dir / filename).resolve()
    except (OSError, ValueError) as exc:
        raise RequestError(status_code=400, message="Invalid filename") from exc
    if not file_path.is_relative_to(base_dir):
        raise RequestError(status_code=400, message="Invalid filename")
    if not file_path.is_file():
        raise RequestError(status_code=404, message="File not found")
    return FileResult(path=str(file_path), media_type="application/octet-stream", filename=filename)
