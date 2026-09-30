"""refs/ref-cache；_lock 保护 _last_refs/_generation，禁止跨越 CDP I/O（锁内快照→释放→I/O→写回）。"""

import asyncio
import copy
import json
import logging
import re
import threading
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from utils import safe_schedule_threadsafe

from .dialog_manager import DialogBlockedError
from .engine import build_snapshot_text

logger = logging.getLogger(__name__)

AX_REF_PATTERN = re.compile(r"^@?e\d+$")
COORD_REF_PATTERN = re.compile(r"^@?(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)$")
_SCROLL_OFFSET_EXPR = "({x: window.pageXOffset||0, y: window.pageYOffset||0})"
# StaticText 无 scrollIntoView，滚其父元素。
_SCROLL_INTO_VIEW_FN = (
    "function() { const el = this.nodeType === 1 ? this : this.parentElement;"
    " if (el) el.scrollIntoView({block: 'center', inline: 'center'}); }"
)


@dataclass(frozen=True)
class SessionIds:
    active: str | None
    page: str | None
    root_frame: str


CdpSendAsync = Callable[..., Awaitable[dict[str, Any]]]
CdpSendSync = Callable[..., dict[str, Any]]
EvaluateRuntimeSync = Callable[..., dict[str, Any]]
LoopProvider = Callable[[], asyncio.AbstractEventLoop | None]
SessionIdsProvider = Callable[[], SessionIds]


class Refs:
    def __init__(
        self,
        *,
        cdp_send_async: CdpSendAsync,
        send_cdp_sync: CdpSendSync,
        evaluate_runtime: EvaluateRuntimeSync,
        loop_provider: LoopProvider,
        session_ids_provider: SessionIdsProvider,
    ) -> None:
        self._cdp_async = cdp_send_async
        self._send_cdp = send_cdp_sync
        self._evaluate_runtime = evaluate_runtime
        self._loop_provider = loop_provider
        self._session_ids_provider = session_ids_provider

        self._lock = threading.Lock()
        self._last_refs: dict[str, dict[str, Any]] = {}
        # 导航自增代次；写回时代次已变则丢弃。
        self._generation = 0

    def note_root_navigation(self) -> None:
        """主帧导航（含 reload）清空 ref 缓存；旧 DOM 已销毁，须重新快照。"""
        with self._lock:
            self._generation += 1
            self._last_refs.clear()

    def record_som(self, elements: list[dict[str, Any]]) -> None:
        """写入 SoM ref（覆盖 is_visual）；不用裸数字键，避免与 eN/@eN 命名空间冲突。"""
        with self._lock:
            self._last_refs = {
                k: v for k, v in self._last_refs.items() if not (isinstance(v, dict) and v.get("is_visual"))
            }
            for el in elements:
                if isinstance(el, dict) and "ref_raw" in el and "ref" in el:
                    for key in (el["ref_raw"], el["ref"]):
                        entry = copy.deepcopy(el)
                        entry["is_visual"] = True
                        self._last_refs[key] = entry

    def snapshot_axtree(
        self,
        *,
        interactive_only: bool = False,
        max_depth: int = 50,
    ) -> dict[str, Any]:
        loop = self._loop_provider()
        if loop is None or not loop.is_running():
            return {"ok": False, "error": "Supervisor loop is not running"}

        async def _do_snapshot() -> dict[str, Any]:
            sids = self._session_ids_provider()
            sid = sids.active or sids.page
            # 导航冲突重抓一次，仍冲突则报错。
            for _ in range(2):
                with self._lock:
                    generation = self._generation
                ax_resp = await self._cdp_async("Accessibility.getFullAXTree", {}, session_id=sid, timeout=15.0)
                nodes = ax_resp.get("result", {}).get("nodes", [])
                text, refs = build_snapshot_text(nodes, interactive_only=interactive_only, max_depth=max_depth)
                with self._lock:
                    if generation == self._generation:
                        self._last_refs = {k: v for k, v in self._last_refs.items() if not AX_REF_PATTERN.match(str(k))}
                        self._last_refs.update(refs)
                        break
            else:
                return {"ok": False, "error": "Page kept navigating while taking the snapshot; try again."}

            await self._inject_aria_refs_async(refs, session_id=sid)

            return {"ok": True, "snapshot": text, "refs": refs, "element_count": len(refs) // 2 if refs else 0}

        fut = safe_schedule_threadsafe(_do_snapshot(), loop)
        if fut is None:
            return {"ok": False, "error": "Supervisor loop unavailable"}
        try:
            return fut.result(timeout=20.0)
        except DialogBlockedError as exc:
            return {"ok": False, "error": str(exc), "dialog": exc.dialog.to_dict()}
        except Exception as exc:
            fut.cancel()
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    async def _inject_aria_refs_async(self, refs_map: dict[str, dict[str, Any]], session_id: str | None) -> None:
        """给快照节点写入 ``aria-ref`` 属性并缓存中心坐标，供 DOM 查询与坐标兜底解析 ref；逐元素失败不影响其余。"""
        sem = asyncio.Semaphore(16)
        try:
            scroll_res = await self._cdp_async(
                "Runtime.evaluate",
                {"expression": _SCROLL_OFFSET_EXPR, "returnByValue": True},
                session_id=session_id,
                timeout=2.0,
            )
            scroll_obj = scroll_res.get("result", {}).get("result", {}).get("value", {})
            scroll_x, scroll_y = int(scroll_obj.get("x", 0)), int(scroll_obj.get("y", 0))
        except Exception as e:
            logger.debug("scroll offset for aria-ref coordinates unavailable: %s", e)
            scroll_x = scroll_y = None

        async def _inject_one(ref_key: str, backend_id: int) -> None:
            async with sem:
                try:
                    obj = await self._cdp_async(
                        "DOM.resolveNode",
                        {"backendNodeId": backend_id},
                        session_id=session_id,
                        timeout=2.0,
                    )
                    object_id = obj.get("result", {}).get("object", {}).get("objectId")
                    if not object_id:
                        return
                    await self._cdp_async(
                        "Runtime.callFunctionOn",
                        {
                            "objectId": object_id,
                            "functionDeclaration": f"function() {{ this.setAttribute('aria-ref', {json.dumps(ref_key)}); }}",
                            "returnByValue": True,
                        },
                        session_id=session_id,
                        timeout=2.0,
                    )
                    if scroll_x is None or scroll_y is None:
                        return
                    box = await self._cdp_async(
                        "DOM.getBoxModel",
                        {"objectId": object_id},
                        session_id=session_id,
                        timeout=1.0,
                    )
                except Exception as e:
                    logger.debug("aria-ref inject failed for %s: %s", ref_key, e)
                    return
                content = box.get("result", {}).get("model", {}).get("content", [])
                if len(content) < 8:
                    return
                # cx 与 page_cx 取整须一致。
                cx = round((content[0] + content[2]) / 2.0)
                cy = round((content[1] + content[5]) / 2.0)
                with self._lock:
                    for key in (ref_key, f"@{ref_key}"):
                        if (entry := self._last_refs.get(key)) is not None:
                            entry.update(
                                cx=cx,
                                cy=cy,
                                page_cx=cx + scroll_x,
                                page_cy=cy + scroll_y,
                                scroll_x=scroll_x,
                                scroll_y=scroll_y,
                            )

        seen_backends: set[int] = set()
        jobs: list[Coroutine[Any, Any, None]] = []
        for ref_key, info in refs_map.items():
            if ref_key.startswith("@"):
                continue
            backend_id = info.get("backendNodeId")
            if not backend_id or backend_id in seen_backends:
                continue
            seen_backends.add(backend_id)
            jobs.append(_inject_one(ref_key, backend_id))

        if jobs:
            await asyncio.gather(*jobs)

    @staticmethod
    def _raise_if_blocked(res: dict[str, Any]) -> None:
        """待决弹窗阻塞页面时直接报出弹窗，而不是继续走坐标兜底得到无关错误。"""
        if res.get("dialog"):
            raise ValueError(res.get("error", "The page is blocked by a JavaScript dialog"))

    def _object_center(self, obj_id: str, sid: str | None, *, scroll_into_view: bool) -> tuple[float, float] | None:
        if scroll_into_view:
            self._send_cdp(
                "Runtime.callFunctionOn",
                {"objectId": obj_id, "functionDeclaration": _SCROLL_INTO_VIEW_FN, "returnByValue": True},
                session_id=sid,
            )
        box = self._send_cdp("DOM.getBoxModel", {"objectId": obj_id}, session_id=sid)
        self._raise_if_blocked(box)
        if not box.get("ok"):
            return None
        content = box["result"].get("model", {}).get("content", [])
        if len(content) < 8:
            return None
        return (content[0] + content[2]) / 2.0, (content[1] + content[5]) / 2.0

    def resolve_ref_center(self, ref: str, *, scroll_into_view: bool = True) -> tuple[float, float, str | None]:
        """解析 ref 为视口坐标（backendNodeId → aria-ref/SoM → 坐标兜底）；失败抛 ValueError。"""
        ref_str = str(ref).strip()
        if not ref_str:
            raise ValueError("Empty ref string")

        coord_m = COORD_REF_PATTERN.match(ref_str)
        if coord_m:
            return max(0.0, float(coord_m.group(1))), max(0.0, float(coord_m.group(2))), None

        normalized = ref_str[1:] if ref_str.startswith("@") else ref_str
        with self._lock:
            info = (
                self._last_refs.get(ref_str) or self._last_refs.get(normalized) or self._last_refs.get(f"@{normalized}")
            )
        cached_cx = info.get("cx") if info else None
        cached_cy = info.get("cy") if info else None
        backend_id = info.get("backendNodeId") if info else None
        sids = self._session_ids_provider()
        sid = sids.active or sids.page

        if backend_id:
            res = self._send_cdp("DOM.resolveNode", {"backendNodeId": backend_id}, session_id=sid, timeout=3.0)
            self._raise_if_blocked(res)
            obj_id = res["result"].get("object", {}).get("objectId") if res.get("ok") else None
            if obj_id and (center := self._object_center(obj_id, sid, scroll_into_view=scroll_into_view)):
                return center[0], center[1], obj_id

        safe_ref = json.dumps(normalized)
        eval_res = self._send_cdp(
            "Runtime.evaluate",
            {
                "expression": f"document.querySelector('[aria-ref=' + {safe_ref} + ']') || document.querySelector('[data-spiritagent-som=' + {safe_ref} + ']')",
                "returnByValue": False,
            },
            session_id=sid,
            timeout=3.0,
        )
        self._raise_if_blocked(eval_res)
        if eval_res.get("ok"):
            res_val = eval_res.get("result", {}).get("result", {})
            obj_id = res_val.get("objectId")
            if (
                obj_id
                and res_val.get("subtype") != "null"
                and (center := self._object_center(obj_id, sid, scroll_into_view=scroll_into_view))
            ):
                return center[0], center[1], obj_id

        cur_sx, cur_sy = 0.0, 0.0
        scroll_eval_ok = False
        try:
            scroll_res = self._evaluate_runtime(_SCROLL_OFFSET_EXPR)
            if isinstance(scroll_res, dict) and scroll_res.get("ok") and isinstance(scroll_res.get("result"), dict):
                cur_sx = float(scroll_res["result"].get("x", 0))
                cur_sy = float(scroll_res["result"].get("y", 0))
                scroll_eval_ok = True
        except Exception as exc:
            logger.debug("Scroll adjustment evaluation error: %s", exc)

        if info and "page_cx" in info and "page_cy" in info:
            if not scroll_eval_ok:
                raise ValueError(f"Element '{ref}' coordinate fallback failed: unable to evaluate page scroll offset")
            adj_cx = float(info["page_cx"]) - cur_sx
            adj_cy = float(info["page_cy"]) - cur_sy
            logger.debug(
                "Self-healing fallback: using page coordinates (%s, %s) with scroll (%s, %s) -> (%s, %s) for %s",
                info["page_cx"],
                info["page_cy"],
                cur_sx,
                cur_sy,
                adj_cx,
                adj_cy,
                ref_str,
            )
            return adj_cx, adj_cy, None

        if cached_cx is not None and cached_cy is not None:
            if not scroll_eval_ok:
                raise ValueError(f"Element '{ref}' coordinate fallback failed: unable to evaluate page scroll offset")
            init_sx = float(info.get("scroll_x", 0)) if info else 0.0
            init_sy = float(info.get("scroll_y", 0)) if info else 0.0
            adj_cx = float(cached_cx) + (init_sx - cur_sx)
            adj_cy = float(cached_cy) + (init_sy - cur_sy)
            logger.debug(
                "Self-healing fallback: using cached viewport coordinates (%s, %s) adjusted to (%s, %s) for %s",
                cached_cx,
                cached_cy,
                adj_cx,
                adj_cy,
                ref_str,
            )
            return adj_cx, adj_cy, None

        raise ValueError(f"Element {ref} not found in DOM or coordinate cache")

    def find_by_text(self, query: str, *, ref_only: bool = True, cap: int = 200) -> dict[str, Any]:
        safe_q = json.dumps(query.lower())
        # getBoundingClientRect 覆盖 fixed/in-flow；offsetParent 对 fixed/hidden 返回 null。
        js = (
            "(function(){"
            f"const q = {safe_q};"
            "const results = [];"
            "const treeWalker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);"
            "while(treeWalker.nextNode()) {"
            "  const el = treeWalker.currentNode;"
            "  const r = el.getBoundingClientRect();"
            "  if (r.width > 0 && r.height > 0 && (el.textContent || '').toLowerCase().includes(q)) {"
            "    const ref = el.getAttribute('aria-ref') || '';"
            "    results.push({ref: ref, tag: el.tagName.toLowerCase(), text: (el.textContent || '').trim().slice(0, 100)});"
            f"   if (results.length >= {cap}) break;"
            "  }"
            "}"
            "return results;"
            "})()"
        )
        res = self._evaluate_runtime(js)
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error", "DOM search failed")}
        items = res.get("result", [])
        if ref_only and isinstance(items, list):
            items = [item.get("ref") for item in items if isinstance(item, dict) and item.get("ref")]
        return {"ok": True, "matches": items}

    def get_images(self) -> dict[str, Any]:
        js = (
            "(function(){"
            "return Array.from(document.querySelectorAll('img')).map(img => ({"
            "  src: img.src || '',"
            "  alt: img.alt || '',"
            "  width: img.naturalWidth || img.width || 0,"
            "  height: img.naturalHeight || img.height || 0,"
            "  ref: img.getAttribute('aria-ref') || ''"
            "}));"
            "})()"
        )
        return self._evaluate_runtime(js)
