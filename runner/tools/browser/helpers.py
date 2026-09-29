from pathlib import Path
from typing import Any

from utils import get_spiritagent_dir, redact_sensitive_text

from ..multimodal import capped_image_data_url


def screenshot_multimodal_result(screenshot_path: str, annotation_context: str = "") -> dict[str, Any]:
    """截图文件 → 直注主对话的 _multimodal 信封（Responses 风格部件），附本地路径与脱敏后的 annotate 上下文。"""
    size = Path(screenshot_path).stat().st_size
    text = (
        f"Browser screenshot attached for visual inspection ({size:,} bytes). Local file: {screenshot_path}. "
        "This result does not itself deliver an image to the user.\n" + redact_sensitive_text(annotation_context)
    )
    return {
        "_multimodal": True,
        "content": [
            {"type": "input_text", "text": text},
            {"type": "input_image", "image_url": capped_image_data_url(Path(screenshot_path), "image/png")},
        ],
    }


def _safe_save_name(save_as: str | None, default: str) -> str:
    """仅保留 save_as 的 basename，防 LLM 用绝对路径或 ``..`` 越界写入缓存目录外。"""
    name = Path(save_as or "").name
    return name or default


def _get_downloads_dir() -> Path:
    d = get_spiritagent_dir("cache/downloads")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _truncate_snapshot(snapshot_text: str, max_chars: int = 8000) -> str:
    """按行边界截断可访问性树快照，并在末尾标注被省略的字符数（避免切断 element ref）。"""
    if len(snapshot_text) <= max_chars:
        return snapshot_text
    lines = snapshot_text.split("\n")
    out: list[str] = []
    total = 0
    for line in lines:
        line_len = len(line) + 1
        if total + line_len > max_chars - 200:
            out.append(f"... [{len(snapshot_text) - total} chars truncated]")
            break
        out.append(line)
        total += line_len
    return "\n".join(out)
