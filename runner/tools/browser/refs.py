"""refs/ref-cache 管理：AXTree snapshot、SoM 注入、ref 解析、refs 代际清空。

锁 #3：Refs._lock（last_refs / _root_doc_generation / _refs_doc_generation /
_last_navigated_url）。本锁仅保护字典读写，**禁止跨越 CDP I/O**，每个方法
遵循「锁内快照 → 释放 → I/O → 锁内写回」模式。
"""

import asyncio
import copy
import json
import logging
import re
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .engine import (
    build_snapshot_text,
)

logger = logging.getLogger(__name__)


AX_REF_PATTERN = re.compile(r"^@?e\d+$")
COORD_REF_PATTERN = re.compile(r"^@?(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)$")


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

        self._lock = threading.Lock()  # 锁 #3
        self._last_refs: dict[str, dict[str, Any]] = {}
        self._root_doc_generation: int = 0
        self._refs_doc_generation: int = -1
        self._last_navigated_url: str = ""

    def note_navigation(self, url: str, *, is_main_root: bool) -> bool:
        """记录一次 root 帧导航，返回是否需要清空 _last_refs。

        与旧内联块 (L1580-1592) 等价：URL 变化且不是 about:blank 时记录 normalized URL，
        并在 main root 时推进 _root_doc_generation。任何 main root 导航都清空 ref 缓存：
        - 真实 URL 变化：旧 ref 指向已销毁 DOM
        - 同 URL reload (F5)：SoM `data-spiritagent-som` 属性是 JS 注入、随 reload 消失；
          AXTree `aria-ref` 同理；cached `page_cx` 是 reload 前的旧位置。self-healing 兜底
          会用旧坐标点击新 DOM，逻辑上"对"像素也大概率错位。强制要求调用方 reload 后重 snap。
        """
        normalized = url.split("#", 1)[0] if url else ""
        with self._lock:
            previous = self._last_navigated_url
            url_changed = bool(normalized) and normalized != "about:blank" and normalized != previous
            if not is_main_root:
                return False
            self._root_doc_generation += 1
            if url_changed:
                self._last_navigated_url = normalized
            if self._root_doc_generation != self._refs_doc_generation:
                self._refs_doc_generation = self._root_doc_generation
                self._last_refs.clear()
                return True
            return False

    def record_som(self, elements: list[dict[str, Any]]) -> None:
        """screenshot(annotate=True) 路径写入 SoM ref 条目（覆盖现有 is_visual 条目）。

        不再写裸数字键 `str(index)`：会与 AXTree 的 eN / @eN 命名空间冲突，
        导致 AXTree 清空后裸数字 ref 仍解析到旧的 SoM 视觉 ref。
        """
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
            self._refs_doc_generation = self._root_doc_generation

    def snapshot_axtree(
        self,
        *,
        full: bool = False,
        interactive_only: bool = False,
        max_depth: int = 50,
    ) -> dict[str, Any]:
        loop = self._loop_provider()
        if loop is None or not loop.is_running():
            return {"ok": False, "error": "Supervisor loop is not running"}

        async def _do_snapshot() -> dict[str, Any]:
            sids = self._session_ids_provider()
            sid = sids.active or sids.page
            ax_resp = await self._cdp_async("Accessibility.getFullAXTree", {}, session_id=sid, timeout=15.0)
            nodes = ax_resp.get("result", {}).get("nodes", [])

            text, refs = build_snapshot_text(nodes, interactive_only=interactive_only, max_depth=max_depth)
            with self._lock:
                self._last_refs = {k: v for k, v in self._last_refs.items() if not AX_REF_PATTERN.match(str(k))}
                self._last_refs.update(refs)
                self._refs_doc_generation = self._root_doc_generation

            await self._inject_aria_refs_async(refs, session_id=sid)

            return {"ok": True, "snapshot": text, "refs": refs, "element_count": len(refs) // 2 if refs else 0}

        try:
            from utils import safe_schedule_threadsafe  # late import

            fut = safe_schedule_threadsafe(_do_snapshot(), loop)
            if fut is None:
                return {"ok": False, "error": "Supervisor loop unavailable"}
            return fut.result(timeout=20.0)
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    async def _inject_aria_refs_async(self, refs_map: dict[str, dict[str, Any]], session_id: str | None) -> None:
        seen_backends: set[int] = set()
        tasks: list[asyncio.Task] = []
        sem = asyncio.Semaphore(16)

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
                    if object_id:
                        safe_ref = json.dumps(ref_key)
                        await self._cdp_async(
                            "Runtime.callFunctionOn",
                            {
                                "objectId": object_id,
                                "functionDeclaration": f"function() {{ this.setAttribute('aria-ref', {safe_ref}); }}",
                                "returnByValue": True,
                            },
                            session_id=session_id,
                            timeout=2.0,
                        )
                        try:
                            box = await self._cdp_async(
                                "DOM.getBoxModel",
                                {"objectId": object_id},
                                session_id=session_id,
                                timeout=1.0,
                            )
                            scroll_res = await self._cdp_async(
                                "Runtime.evaluate",
                                {
                                    "expression": "({x: window.pageXOffset||0, y: window.pageYOffset||0})",
                                    "returnByValue": True,
                                },
                                session_id=session_id,
                                timeout=1.0,
                            )
                            content = box.get("result", {}).get("model", {}).get("content", [])
                            if len(content) >= 8:
                                # cx 与 page_cx 必须使用一致的取整方式，否则
                                # self-healing 回退 (page_cx - cur_sx) 会与
                                # 原始 (cx) 出现 1px 偏差。统一用 round。
                                cx = round((content[0] + content[2]) / 2.0)
                                cy = round((content[1] + content[5]) / 2.0)
                                scroll_obj = scroll_res.get("result", {}).get("result", {}).get("value", {})
                                page_cx = round((content[0] + content[2]) / 2.0) + int(scroll_obj.get("x", 0))
                                page_cy = round((content[1] + content[5]) / 2.0) + int(scroll_obj.get("y", 0))
                                with self._lock:
                                    for key in (ref_key, f"@{ref_key}"):
                                        if key in self._last_refs:
                                            entry = self._last_refs[key]
                                            entry["cx"] = cx
                                            entry["cy"] = cy
                                            entry["page_cx"] = page_cx
                                            entry["page_cy"] = page_cy
                                            entry["scroll_x"] = int(scroll_obj.get("x", 0))
                                            entry["scroll_y"] = int(scroll_obj.get("y", 0))
                        except Exception:
                            pass
                except Exception as e:
                    logger.debug("aria-ref inject failed for %s: %s", ref_key, e)

        for ref_key, info in refs_map.items():
            if ref_key.startswith("@"):
                continue
            backend_id = info.get("backendNodeId")
            if not backend_id or backend_id in seen_backends:
                continue
            seen_backends.add(backend_id)
            tasks.append(_inject_one(ref_key, backend_id))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _resolve_ref_center(self, ref: str, *, scroll_into_view: bool = True) -> tuple[float, float, str | None]:
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
            if res.get("ok"):
                obj_id = res["result"].get("object", {}).get("objectId")
                if obj_id:
                    if scroll_into_view:
                        self._send_cdp(
                            "Runtime.callFunctionOn",
                            {
                                "objectId": obj_id,
                                "functionDeclaration": "function() { try { this.scrollIntoView({block: 'center', inline: 'center'}); } catch(e) {} }",
                                "returnByValue": True,
                            },
                            session_id=sid,
                        )
                    box = self._send_cdp("DOM.getBoxModel", {"objectId": obj_id}, session_id=sid)
                    if box.get("ok"):
                        content = box["result"].get("model", {}).get("content", [])
                        if len(content) >= 8:
                            cx = (content[0] + content[2]) / 2.0
                            cy = (content[1] + content[5]) / 2.0
                            return cx, cy, obj_id

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
        if eval_res.get("ok"):
            res_val = eval_res.get("result", {}).get("result", {})
            obj_id = res_val.get("objectId")
            subtype = res_val.get("subtype")
            if obj_id and subtype != "null":
                if scroll_into_view:
                    self._send_cdp(
                        "Runtime.callFunctionOn",
                        {
                            "objectId": obj_id,
                            "functionDeclaration": "function() { try { this.scrollIntoView({block: 'center', inline: 'center'}); } catch(e) {} }",
                            "returnByValue": True,
                        },
                        session_id=sid,
                    )
                box = self._send_cdp("DOM.getBoxModel", {"objectId": obj_id}, session_id=sid)
                if box.get("ok"):
                    content = box["result"].get("model", {}).get("content", [])
                    if len(content) >= 8:
                        cx = (content[0] + content[2]) / 2.0
                        cy = (content[1] + content[5]) / 2.0
                        return cx, cy, obj_id

        cur_sx, cur_sy = 0.0, 0.0
        scroll_eval_ok = False
        try:
            scroll_res = self._evaluate_runtime("({x: window.pageXOffset||0, y: window.pageYOffset||0})")
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
        # 用 getBoundingClientRect 替代 offsetParent：
        # offsetParent 对 position: fixed / display:none 的元素返回 null，
        # 会把 tooltips / 浮层按钮等可见 fixed 元素错误排除。
        # getBoundingClientRect() 同时覆盖 fixed 与 in-flow，display:none 时返回零尺寸。
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
