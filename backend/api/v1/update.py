import asyncio
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from common import get_or_404, get_router
from components import DbSession, apply_partial, sha512_b64
from fastapi import File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from modules.auth import CurrentAdmin
from modules.system import MessageResponse, ReleaseManifestFileItem, ReleaseManifestResponse
from modules.update import UpdateVersion, UpdateVersionItem, UpdateVersionListResponse, UpdateVersionUpdate
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = get_router()

# 相对工作目录；容器内挂载为 /app/updates。
VERSIONS_DIR = Path("updates/versions")
CHUNK_SIZE = 8192

ALLOWED_ARCHIVE_SUFFIXES = (
    ".exe",
    "RELEASES",
    "-full.nupkg",
    ".blockmap",
    ".zip",
    ".dmg",
    ".whl",
    "server.py",
    "manifest.json",
    "latest-runner.yml",
    "app-update.yml",
)

DOWNLOAD_SUFFIXES = (
    ".exe",
    "-full.nupkg",
    "-full.nupkg.blockmap",
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
    if latest is None:
        raise HTTPException(status_code=404, detail="No active version")
    return latest


def _pick_asset(versions_dir: Path, *patterns: str) -> Path | None:
    """按调用顺序拼接 pattern 后排序，取 versions_dir 中最后一条匹配文件；排除 ``.tmp.*`` 暂存名，避免返回不完整文件。"""
    candidates = [
        match for pattern in patterns for match in versions_dir.glob(pattern) if not match.name.startswith(".tmp.")
    ]
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


def _inspect_release(versions_dir: Path, version: str) -> tuple[Path, Path | None, Path | None]:
    """校验解压结果并返回 (exe, mac 包, runner wheel)。构建脚本（Build-UpdateZip）总会写入 manifest.json，其 version 必须匹配文件名版本，否则视为不同发布。"""
    manifest_path = versions_dir / "manifest.json"
    if not manifest_path.exists():
        raise ValueError("更新包缺少 manifest.json。")
    try:
        manifest_version = json.loads(manifest_path.read_text(encoding="utf-8")).get("version")
    except json.JSONDecodeError as exc:
        raise ValueError(f"manifest.json 无法解析：{exc}") from exc
    if not manifest_version:
        raise ValueError("manifest.json 缺少 version 字段。")
    if manifest_version != version:
        raise ValueError(f"manifest.json 的版本 {manifest_version} 与文件名中的版本 {version} 不一致。")
    exe_file = _pick_asset(versions_dir, "*.exe")
    if exe_file is None:
        raise ValueError("更新包缺少 Windows 安装程序（*.exe）。")
    # Runner 侧：wheel + server.py 解压到 runner/，latest-runner.yml 位于根。
    return exe_file, _pick_asset(versions_dir, "*.zip", "*.dmg"), _pick_asset(versions_dir, "runner/spirit_agent-*.whl")


@router.post("/versions", response_model=UpdateVersionItem, status_code=201)
async def create_version(
    admin: CurrentAdmin,
    db: DbSession,
    file: UploadFile = File(...),
    release_notes: str = Form(""),
) -> UpdateVersionItem:
    # Squirrel 构建产物 zip，必须含 *.exe。
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="上传文件必须是 .zip。")

    # 限定为纯 semver——该值会成为 VERSIONS_DIR 下的路径片段，否则文件名可逃出目录。
    if not (match := re.search(r"\d+\.\d+\.\d+", file.filename)):
        raise HTTPException(status_code=400, detail="文件名须包含 1.2.3 形式的版本号。")
    version = match.group(0)

    if (await db.execute(select(UpdateVersion).where(UpdateVersion.version == version))).scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"版本 {version} 已存在。")

    versions_dir = VERSIONS_DIR / version
    versions_dir.mkdir(parents=True, exist_ok=True)
    # 在进程唯一的临时目录暂存上传，避免两个并发管理上传同一版本时互相覆盖字节。
    with tempfile.TemporaryDirectory(dir=VERSIONS_DIR, prefix=f".upload_{version}_") as tmp_dir:
        zip_path = Path(tmp_dir) / "upload.zip"
        # 上传包可达数百 MB：写盘与解压移出事件循环
        with open(zip_path, "wb") as f:
            while chunk := await file.read(CHUNK_SIZE):
                await asyncio.to_thread(f.write, chunk)

        try:
            await asyncio.to_thread(_extract_archive_entries, zip_path, versions_dir)
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="无效 zip 文件。")

    try:
        exe_file, mac_file, wheel_file = await asyncio.to_thread(_inspect_release, versions_dir, version)
    except ValueError as exc:
        await asyncio.to_thread(shutil.rmtree, versions_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    record = UpdateVersion(
        version=version,
        release_notes=release_notes,
        exe_filename=exe_file.name,
        exe_sha512=await asyncio.to_thread(sha512_b64, exe_file),
        exe_size=exe_file.stat().st_size,
        mac_filename=mac_file.name if mac_file else None,
        mac_sha512=await asyncio.to_thread(sha512_b64, mac_file) if mac_file else None,
        mac_size=mac_file.stat().st_size if mac_file else None,
        runner_filename=f"runner/{wheel_file.name}" if wheel_file else None,
        is_active=True,
        created_by=admin,
    )
    db.add(record)
    await db.commit()
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
    record = await get_or_404(db, UpdateVersion, id=id, detail="版本不存在。")
    versions_dir = VERSIONS_DIR / record.version
    if versions_dir.exists():
        await asyncio.to_thread(shutil.rmtree, versions_dir)
    await db.delete(record)
    await db.commit()
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
