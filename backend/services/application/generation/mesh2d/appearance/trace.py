"""外观链诊断产物落盘：按任务隔离、容量受限、绝不包含凭据。

产物仅服务分段诊断与回归复核（原图、供应商 PSD、重建 PSD、合成对照、
报告）。保留最新 KEEP_RUNS 份，超出的旧目录直接删除。
"""

import json
import shutil
import tempfile
import time
import uuid
from collections.abc import Mapping
from pathlib import Path

KEEP_RUNS = 20


def save_trace(root: Path, files: Mapping[str, bytes | str]) -> Path:
    """写入一次运行的诊断目录并触发保留期清理；返回目录路径。"""
    root.mkdir(parents=True, exist_ok=True)
    run_dir = root / f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns():020d}-{uuid.uuid4().hex}"
    # 只发布完整目录；并发清理不触碰仍在写入的诊断。
    with tempfile.TemporaryDirectory(prefix=".pending-", dir=root) as staging:
        for name, payload in files.items():
            path = Path(staging) / name
            if isinstance(payload, str):
                path.write_text(payload, encoding="utf-8")
            else:
                path.write_bytes(payload)
        Path(staging).rename(run_dir)
    _prune(root)
    return run_dir


def _prune(root: Path, keep: int = KEEP_RUNS) -> None:
    if not root.is_dir():
        return
    runs = sorted(
        (p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")),
        key=lambda p: p.name,
        reverse=True,
    )
    for stale in runs[keep:]:
        shutil.rmtree(stale, ignore_errors=True)


def report_json(report: Mapping[str, object]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)
