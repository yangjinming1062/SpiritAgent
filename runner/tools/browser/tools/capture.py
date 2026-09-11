import hashlib
import json
import secrets
from typing import Any

from utils import get_spiritagent_home

from ...registry import registry
from ..camofox import is_camofox_mode
from ..check import check_browser_native_requirements
from ..helpers import _get_downloads_dir, _safe_save_name
from ..schemas import (
    BROWSER_DOWNLOAD_SCHEMA,
    BROWSER_PDF_SCHEMA,
    BROWSER_SCREENSHOT_ELEMENT_SCHEMA,
)
from ._common import browser_session, camofox_unsupported, guard_browser_url, no_supervisor


def browser_screenshot_element(ref: str, save_as: str | None = None, task_id: str | None = None) -> str:
    if is_camofox_mode():
        return camofox_unsupported("browser_screenshot_element")

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        screenshots_dir = get_spiritagent_home() / "browser_screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        filename = _safe_save_name(save_as, f"element_{secrets.token_hex(4)}.png")
        out_path = screenshots_dir / filename

        res = supervisor.screenshot_element(ref, path=out_path)
        if res.get("ok"):
            return json.dumps({"success": True, "path": str(out_path), "ref": ref})
        return json.dumps({"success": False, "error": res.get("error", "Failed to capture element screenshot")})


def browser_pdf(
    save_as: str | None = None,
    landscape: bool = False,
    print_background: bool = True,
    paper_width: float = 8.5,
    paper_height: float = 11.0,
    task_id: str | None = None,
) -> str:
    if is_camofox_mode():
        return camofox_unsupported("browser_pdf")

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        pdf_dir = get_spiritagent_home() / "browser_pdfs"
        pdf_dir.mkdir(parents=True, exist_ok=True)
        filename = _safe_save_name(save_as, f"page_{secrets.token_hex(4)}.pdf")
        out_path = pdf_dir / filename

        res = supervisor.print_pdf(
            path=out_path,
            landscape=landscape,
            print_background=print_background,
            paper_width=paper_width,
            paper_height=paper_height,
        )
        if not res.get("ok"):
            return json.dumps({"success": False, "error": res.get("error", "Failed to generate PDF")})

        sha = hashlib.sha256(out_path.read_bytes()).hexdigest()
        return json.dumps({"success": True, "path": str(out_path), "sha256": sha})


def browser_download(
    ref_or_url: str,
    save_as: str | None = None,
    timeout_s: float = 30.0,
    task_id: str | None = None,
    cancel_token: Any = None,
) -> str:
    """通过点击 ref 链接或导航至下载 URL 触发并等待文件下载。"""
    if is_camofox_mode():
        return camofox_unsupported("browser_download")

    if cancel_token is not None and getattr(cancel_token, "is_set", lambda: False)():
        return json.dumps(
            {"success": False, "error": "Caller cancelled before download", "cancelled": True},
            ensure_ascii=False,
        )

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        if ref_or_url.startswith(("http://", "https://")):
            safe_url, url_err = guard_browser_url(ref_or_url)
            if url_err is not None:
                return url_err
            try:
                supervisor.navigate(safe_url, timeout=5.0)
            except Exception as exc:
                return json.dumps({"success": False, "error": f"Download navigate failed: {exc}"}, ensure_ascii=False)
        else:
            click_res = supervisor.click_ref(ref_or_url)
            if not click_res.get("ok"):
                return json.dumps(
                    {
                        "success": False,
                        "error": f"Failed to click download target {ref_or_url}: {click_res.get('error')}",
                    },
                    ensure_ascii=False,
                )

        dl_res = supervisor.wait_for_download(timeout=timeout_s)
        if not dl_res.get("ok"):
            return json.dumps(
                {"success": False, "error": dl_res.get("error", "Download timed out")},
                ensure_ascii=False,
            )

        orig_filename = dl_res.get("filename", "downloaded_file")
        target_name = _safe_save_name(save_as, orig_filename)
        dest_dir = _get_downloads_dir()
        dest_path = dest_dir / target_name

        return json.dumps(
            {"success": True, "filename": target_name, "path": str(dest_path), "guid": dl_res.get("guid")},
            ensure_ascii=False,
        )


registry.register_tool(
    "browser_screenshot_element",
    check_fn=check_browser_native_requirements,
    schema=BROWSER_SCREENSHOT_ELEMENT_SCHEMA,
)(
    lambda args, **kw: browser_screenshot_element(
        ref=args.get("ref", ""),
        save_as=args.get("save_as"),
        task_id=kw.get("task_id"),
    ),
)

registry.register_tool("browser_pdf", check_fn=check_browser_native_requirements, schema=BROWSER_PDF_SCHEMA)(
    lambda args, **kw: browser_pdf(
        save_as=args.get("save_as"),
        landscape=args.get("landscape", False),
        print_background=args.get("print_background", True),
        paper_width=args.get("paper_width", 8.5),
        paper_height=args.get("paper_height", 11.0),
        task_id=kw.get("task_id"),
    ),
)

registry.register_tool("browser_download", check_fn=check_browser_native_requirements, schema=BROWSER_DOWNLOAD_SCHEMA)(
    lambda args, **kw: browser_download(
        ref_or_url=args.get("ref_or_url", ""),
        save_as=args.get("save_as"),
        timeout_s=args.get("timeout_s", 30.0),
        task_id=kw.get("task_id"),
        cancel_token=kw.get("cancel_token"),
    ),
)
