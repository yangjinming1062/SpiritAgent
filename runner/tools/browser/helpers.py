import logging
from pathlib import Path
from typing import Any

from utils import call_llm_sync, get_spiritagent_dir, redact_sensitive_text

from ..multimodal import capped_image_data_url

logger = logging.getLogger(__name__)

SNAPSHOT_SUMMARIZE_THRESHOLD = 8000


def screenshot_multimodal_result(screenshot_path: str, annotation_context: str = "") -> dict[str, Any]:
    """截图文件 → 直注主对话的 _multimodal 信封；text 部分携带 MEDIA: 引用与脱敏后的 annotate 上下文。"""
    size = Path(screenshot_path).stat().st_size
    text = (
        f"Browser screenshot attached ({size:,} bytes). Share it with the user by including MEDIA:{screenshot_path} in your response."
        + redact_sensitive_text(
            annotation_context,
        )
    )
    return {
        "_multimodal": True,
        "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": capped_image_data_url(Path(screenshot_path), "image/png")}},
        ],
        "meta": {"screenshot_path": screenshot_path, "image_size_bytes": size},
    }


def _safe_save_name(save_as: str | None, default: str) -> str:
    """仅保留 save_as 的 basename，防 LLM 用绝对路径或 ``..`` 越界写入缓存目录外。"""
    name = Path(save_as or "").name
    return name or default


def _get_downloads_dir() -> Path:
    d = get_spiritagent_dir("cache/downloads", "browser_downloads")
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


def _extract_relevant_content(snapshot_text: str, user_task: str | None = None) -> str:
    """调用 LLM 按 user_task 抽取快照里与任务相关的内容；不可达 reverse-RPC 时回退到按行截断。"""
    if user_task:
        extraction_prompt = (
            f"You are a content extractor for a browser automation agent.\n\n"
            f"The user's task is: {user_task}\n\n"
            f"Given the following page snapshot (accessibility tree representation), "
            f"extract and summarize the most relevant information for completing this task. Focus on:\n"
            f"1. Interactive elements (buttons, links, inputs) that might be needed\n"
            f"2. Text content relevant to the task (prices, descriptions, headings, important info)\n"
            f"3. Navigation structure if relevant\n\n"
            f"Keep ref IDs (like [ref=e5]) for interactive elements so the agent can use them.\n\n"
            f"Page Snapshot:\n{snapshot_text}\n\n"
            f"Provide a concise summary that preserves actionable information and relevant content."
        )
    else:
        extraction_prompt = (
            f"Summarize this page snapshot, preserving:\n"
            f"1. All interactive elements with their ref IDs (like [ref=e5])\n"
            f"2. Key text content and headings\n"
            f"3. Important information visible on the page\n\n"
            f"Page Snapshot:\n{snapshot_text}\n\n"
            f"Provide a concise summary focused on interactive elements and key content."
        )

    extraction_prompt = redact_sensitive_text(extraction_prompt)

    try:
        response = call_llm_sync(
            task="web_extract",
            messages=[{"role": "user", "content": extraction_prompt}],
            max_tokens=4000,
            temperature=0.1,
            timeout=30.0,
        )
        extracted = (response or "").strip() or _truncate_snapshot(snapshot_text)
        return redact_sensitive_text(extracted)
    except Exception:
        return _truncate_snapshot(snapshot_text)
