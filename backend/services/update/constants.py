from pathlib import Path

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
