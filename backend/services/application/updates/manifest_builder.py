from components import normalize_sha512
from fastapi import HTTPException
from modules.update import UpdateVersion

from .constants import VERSIONS_DIR


def build_manifest(latest: UpdateVersion, filename: str | None, sha512: str | None, size: int | None) -> dict:
    if not filename or not sha512:
        raise HTTPException(status_code=404, detail="No active release for this platform")
    file_path = VERSIONS_DIR / latest.version / filename
    actual_size = size if size is not None else 0
    if actual_size == 0:
        try:
            actual_size = file_path.stat().st_size
        except OSError:
            actual_size = 0
    return {
        "version": latest.version,
        "releaseDate": latest.created_at.isoformat(),
        "releaseNotes": latest.release_notes,
        "path": filename,
        "sha512": normalize_sha512(sha512),
        "files": [{"url": filename, "sha512": normalize_sha512(sha512), "size": actual_size}],
    }
