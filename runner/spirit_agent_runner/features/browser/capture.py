import base64
import contextlib
import logging
import tempfile
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from spirit_agent_runner.features.browser.engine.som import (
    SOM_INJECT_SCRIPT,
    SOM_REMOVE_SCRIPT,
    format_som_annotation_context,
    parse_som_results,
)
from spirit_agent_runner.features.browser.refs import Refs, SessionIds

logger = logging.getLogger(__name__)


class CaptureOperations:
    def __init__(
        self,
        *,
        send_cdp: Callable[..., dict[str, Any]],
        evaluate_runtime: Callable[..., dict[str, Any]],
        wait_for_page_stable: Callable[..., bool],
        refs: Refs,
        session_ids: Callable[[], SessionIds],
    ) -> None:
        self.send_cdp = send_cdp
        self.evaluate_runtime = evaluate_runtime
        self.wait_for_page_stable = wait_for_page_stable
        self._refs = refs
        self._session_ids = session_ids
        self._som_lock = threading.Lock()

    def screenshot(
        self,
        path: str | Path | None = None,
        *,
        full_page: bool = False,
        annotate: bool = False,
    ) -> dict[str, Any]:
        sid = (sids := self._session_ids()).active or sids.page
        elements: list[dict[str, Any]] = []
        annotation_context = ""

        with self._som_lock if annotate else contextlib.nullcontext():
            try:
                if annotate:
                    self.wait_for_page_stable(timeout_s=1.0)
                    inject_res = self.evaluate_runtime(SOM_INJECT_SCRIPT, timeout=5.0)
                    if not inject_res.get("ok"):
                        return {"ok": False, "error": f"SoM injection failed: {inject_res.get('error')}"}
                    raw_som = inject_res.get("result", "")
                    elements = parse_som_results(raw_som)
                    annotation_context = format_som_annotation_context(elements)
                    self._refs.record_som(elements)

                params: dict[str, Any] = {"format": "png", "captureBeyondViewport": full_page}
                res = self.send_cdp("Page.captureScreenshot", params, session_id=sid, timeout=15.0)

                if not res.get("ok"):
                    return res

                data_b64 = res.get("result", {}).get("data", "")
                if not data_b64:
                    return {"ok": False, "error": "No screenshot data returned from CDP"}

                raw_bytes = base64.b64decode(data_b64)
                name_suffix = uuid.uuid4().hex[:8]
                out_path = Path(path) if path else Path(tempfile.gettempdir()) / f"screenshot_{name_suffix}.png"
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(raw_bytes)

                result: dict[str, Any] = {"ok": True, "path": str(out_path), "bytes": len(raw_bytes)}
                if annotate:
                    result["elements"] = list(elements)
                    result["annotation_context"] = annotation_context
                return result
            finally:
                if annotate and not (cleanup := self.evaluate_runtime(SOM_REMOVE_SCRIPT, timeout=3.0)).get("ok"):
                    logger.warning("SoM cleanup failed; badges may remain on the page: %s", cleanup.get("error"))

    def screenshot_element(self, ref: str, path: str | Path | None = None) -> dict[str, Any]:
        try:
            # 先滚入视口再截。
            _, _, obj_id = self._refs.resolve_ref_center(ref, scroll_into_view=True)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        if not obj_id:
            return {
                "ok": False,
                "error": f"Element '{ref}' could not be resolved to a DOM element (coordinate-based refs or elements no longer in the DOM cannot be used for screenshot_element)",
            }

        sid = (sids := self._session_ids()).active or sids.page
        box = self.send_cdp("DOM.getBoxModel", {"objectId": obj_id}, session_id=sid)
        if not box.get("ok"):
            return {"ok": False, "error": f"Failed to get box model for {ref}: {box.get('error')}"}

        quad = box["result"].get("model", {}).get("border", [])
        if len(quad) < 8:
            return {"ok": False, "error": f"Invalid box model dimensions for {ref}"}
        # clip 是文档坐标，须加滚动偏移。
        metrics = self.send_cdp("Page.getLayoutMetrics", session_id=sid)
        if not metrics.get("ok"):
            return {"ok": False, "error": f"Failed to read page scroll offset: {metrics.get('error')}"}
        viewport = metrics["result"].get("cssVisualViewport", {})

        xs, ys = quad[0::2], quad[1::2]
        if (
            max(xs) <= 0
            or max(ys) <= 0
            or min(xs) >= viewport.get("clientWidth", 0)
            or min(ys) >= viewport.get("clientHeight", 0)
        ):
            return {
                "ok": False,
                "error": f"Element '{ref}' is not visible in the viewport (it may be hidden or clipped)",
            }
        clip = {
            "x": min(xs) + viewport.get("pageX", 0),
            "y": min(ys) + viewport.get("pageY", 0),
            "width": max(1, max(xs) - min(xs)),
            "height": max(1, max(ys) - min(ys)),
            "scale": 1,
        }
        res = self.send_cdp(
            "Page.captureScreenshot",
            {"format": "png", "clip": clip},
            session_id=sid,
            timeout=15.0,
        )
        if not res.get("ok"):
            return res

        data_b64 = res.get("result", {}).get("data", "")
        raw_bytes = base64.b64decode(data_b64)
        out_path = Path(path) if path else Path(tempfile.gettempdir()) / f"element_{uuid.uuid4().hex[:8]}.png"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(raw_bytes)
        return {"ok": True, "path": str(out_path), "bytes": len(raw_bytes)}

    def print_pdf(
        self,
        path: str | Path,
        *,
        landscape: bool = False,
        print_background: bool = True,
        paper_width: float = 8.5,
        paper_height: float = 11.0,
    ) -> dict[str, Any]:
        sid = (sids := self._session_ids()).active or sids.page
        params = {
            "landscape": landscape,
            "printBackground": print_background,
            "paperWidth": paper_width,
            "paperHeight": paper_height,
        }
        res = self.send_cdp("Page.printToPDF", params, session_id=sid, timeout=20.0)
        if not res.get("ok"):
            return res

        data_b64 = res.get("result", {}).get("data", "")
        raw_bytes = base64.b64decode(data_b64)
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(raw_bytes)
        return {"ok": True, "path": str(out_path), "bytes": len(raw_bytes)}
