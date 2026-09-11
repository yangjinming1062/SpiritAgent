import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

SOM_INJECT_SCRIPT = r"""
(() => {
  // 清理可能残留的旧角标与命名空间属性
  const oldContainer = document.getElementById('spiritagent-som-container');
  if (oldContainer) oldContainer.remove();
  document.querySelectorAll('.spiritagent-som-badge').forEach(b => b.remove());
  document.querySelectorAll('[data-spiritagent-som]').forEach(el => el.removeAttribute('data-spiritagent-som'));

  const checkVisibleAndGetRect = (el) => {
    if (!el || el.nodeType !== 1) return null;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;

    let curr = el;
    while (curr && curr.nodeType === 1) {
      const style = window.getComputedStyle(curr);
      if (style.display === 'none' || style.visibility === 'hidden') return null;
      const opacity = parseFloat(style.opacity || '1');
      if (opacity === 0 || isNaN(opacity)) return null;

      // 仅当容器具有 overflow 裁剪属性时才计算父容器 bounding rect
      if (curr !== el && (style.overflow === 'hidden' || style.overflowX === 'hidden' || style.overflowY === 'hidden' || style.overflow === 'clip')) {
        const parentRect = curr.getBoundingClientRect();
        if (
          rect.bottom <= parentRect.top ||
          rect.top >= parentRect.bottom ||
          rect.right <= parentRect.left ||
          rect.left >= parentRect.right
        ) {
          return null;
        }
      }
      curr = curr.parentElement;
    }
    return rect;
  };

  const isInteractive = (el) => {
    const tag = el.tagName.toLowerCase();
    if (['button', 'select', 'textarea', 'summary'].includes(tag)) return true;
    if (tag === 'input' && el.type !== 'hidden') return true;
    if (tag === 'a' && el.hasAttribute('href')) return true;
    if (el.isContentEditable) return true;
    if (el.hasAttribute('aria-haspopup') || el.hasAttribute('aria-expanded')) return true;
    if (el.hasAttribute('onclick') || (el.hasAttribute('tabindex') && el.getAttribute('tabindex') !== '-1')) return true;
    const role = (el.getAttribute('role') || '').toLowerCase();
    const interactiveRoles = [
      'button', 'link', 'textbox', 'searchbox', 'combobox', 'checkbox', 'radio',
      'switch', 'slider', 'spinbutton', 'menuitem', 'menuitemcheckbox', 'menuitemradio',
      'option', 'tab', 'treeitem', 'row', 'columnheader', 'rowheader', 'cell', 'gridcell', 'dialog'
    ];
    if (interactiveRoles.includes(role)) return true;
    return false;
  };

  const allElements = Array.from(document.querySelectorAll(
    'button, input:not([type="hidden"]), select, textarea, a[href], [role], [contenteditable="true"], [contenteditable=""], summary, [tabindex]:not([tabindex="-1"]), [onclick], [aria-haspopup], [aria-expanded]'
  ));
  const rawValidItems = [];

  for (const el of allElements) {
    if (isInteractive(el)) {
      const rect = checkVisibleAndGetRect(el);
      if (rect) {
        rawValidItems.push({ el, rect });
      }
    }
  }

  // O(N) 线性祖先查找去重：如果祖先已经是交互元素，则忽略子元素
  const validSet = new Set(rawValidItems.map(item => item.el));
  const validItems = [];
  for (const item of rawValidItems) {
    let ancestor = item.el.parentElement;
    let hasInteractiveAncestor = false;
    while (ancestor) {
      if (validSet.has(ancestor)) {
        hasInteractiveAncestor = true;
        break;
      }
      ancestor = ancestor.parentElement;
    }
    if (!hasInteractiveAncestor) {
      validItems.push(item);
    }
  }

  // 按垂直视口位置从上到下、从左到右排序（复用已获取的 rect，无需重新触发 Layout Reflow）。
  // 阈值 5px（而不是 10）：跨行元素 vertical gap 通常 ≥10px，否则会把相邻行错排到同一行。
  validItems.sort((a, b) => {
    if (Math.abs(a.rect.top - b.rect.top) > 5) return a.rect.top - b.rect.top;
    return a.rect.left - b.rect.left;
  });

  const results = [];
  const container = document.createElement('div');
  container.id = 'spiritagent-som-container';
  // overflow: visible 而非 hidden：full_page 截图模式下，captureBeyondViewport
  // 会渲染视口外内容；保留容器固定 100vw×100vh 仍是定位基准，但 badge
  // 在 top>100vh 时不被裁剪，会出现在正确的文档坐标上。
  container.style.cssText = 'position: fixed !important; top: 0 !important; left: 0 !important; width: 100vw !important; height: 100vh !important; pointer-events: none !important; z-index: 2147483647 !important; overflow: visible !important;';

  const scrollX = window.pageXOffset || document.documentElement.scrollLeft || 0;
  const scrollY = window.pageYOffset || document.documentElement.scrollTop || 0;

  let index = 1;
  for (const { el, rect } of validItems) {
    const refKey = 'v' + index;
    el.setAttribute('data-spiritagent-som', refKey);

    const badge = document.createElement('div');
    badge.className = 'spiritagent-som-badge';
    badge.textContent = String(index);
    badge.style.cssText = [
      'position: absolute',
      `top: ${Math.max(0, Math.round(rect.top))}px`,
      `left: ${Math.max(0, Math.round(rect.left))}px`,
      'background: #ffe600',
      'color: #000000',
      'font-size: 11px',
      'font-weight: 800',
      'font-family: monospace, sans-serif',
      'padding: 0 3px',
      'border: 1px solid #111111',
      'border-radius: 3px',
      'box-shadow: 0 1px 4px rgba(0,0,0,0.6)',
      'z-index: 2147483647',
      'pointer-events: none',
      'user-select: none',
      'line-height: 14px',
      'height: 14px',
      'display: inline-block'
    ].join(' !important;') + ' !important;';


    container.appendChild(badge);

    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role') || '';
    const inputType = (el.type || '').toLowerCase();

    let text = '';
    if (tag === 'input' || tag === 'textarea') {
      if (['submit', 'button', 'reset'].includes(inputType)) {
        text = el.value || el.getAttribute('aria-label') || '';
      } else {
        // 敏感数据安全防范：不读取输入框内预填的敏感值，仅读取标签与提示文本
        text = el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('title') || '';
      }
    } else {
      text = (el.innerText || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('title') || '').slice(0, 80).trim();
    }
    text = text.replace(/\s+/g, ' ');

    results.push({
      index: index,
      ref: '@' + refKey,
      ref_raw: refKey,
      tag: tag,
      role: role,
      text: text,
      cx: Math.round(rect.left + rect.width / 2),
      cy: Math.round(rect.top + rect.height / 2),
      page_cx: Math.round(rect.left + scrollX + rect.width / 2),
      page_cy: Math.round(rect.top + scrollY + rect.height / 2),
      scroll_x: Math.round(scrollX),
      scroll_y: Math.round(scrollY)
    });

    index++;
  }

  document.documentElement.appendChild(container);
  return JSON.stringify(results);
})();
"""

SOM_REMOVE_SCRIPT = r"""
(() => {
  const c = document.getElementById('spiritagent-som-container');
  if (c) c.remove();
  document.querySelectorAll('.spiritagent-som-badge').forEach(b => b.remove());
  document.querySelectorAll('[data-spiritagent-som]').forEach(el => el.removeAttribute('data-spiritagent-som'));
  return true;
})();
"""

DOM_SETTLE_SCRIPT = r"""
(maxWaitMs = 2000, debounceMs = 200) => new Promise((resolve) => {
  let timer = null;
  let maxTimer = null;
  let observer = null;
  let domLoadedHandler = null;

  const done = (isSettled) => {
    if (domLoadedHandler) {
      window.removeEventListener('DOMContentLoaded', domLoadedHandler);
      domLoadedHandler = null;
    }
    if (observer) {
      try { observer.disconnect(); } catch (e) {}
      observer = null;
    }
    if (timer) { clearTimeout(timer); timer = null; }
    if (maxTimer) { clearTimeout(maxTimer); maxTimer = null; }
    resolve(Boolean(isSettled));
  };

  const startObserving = (target) => {
    if (maxTimer) { clearTimeout(maxTimer); maxTimer = null; }
    if (timer) { clearTimeout(timer); timer = null; }
    try {
      observer = new MutationObserver(() => {
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => done(true), debounceMs);
      });
      observer.observe(target, {
        childList: true,
        subtree: true,
        attributes: true,
        characterData: true,
      });
    } catch (e) {
      done(false);
      return;
    }
    // 初始防抖定时器：若页面本身已处于静默稳定状态（无新 mutation），
    // debounceMs 后立即以 true 结算，无需硬等完整个 maxWaitMs。
    timer = setTimeout(() => done(true), debounceMs);
    // 超时看门狗：若 DOM 变动持续不断超过 maxWaitMs，以 false 超时退出。
    maxTimer = setTimeout(() => done(false), maxWaitMs);
  };

  const root = document.documentElement || document.body;
  if (!root) {
    if (document.readyState === 'complete') {
      done(true);
    } else {
      domLoadedHandler = () => {
        domLoadedHandler = null;
        const newRoot = document.documentElement || document.body;
        if (newRoot) {
          startObserving(newRoot);
        } else {
          done(false);
        }
      };
      window.addEventListener('DOMContentLoaded', domLoadedHandler, { once: true });
      maxTimer = setTimeout(() => done(false), maxWaitMs);
    }
    return;
  }

  startObserving(root);
})
"""


def parse_som_results(raw_json: str) -> list[dict[str, Any]]:
    if not raw_json or not isinstance(raw_json, str):
        return []
    try:
        data = json.loads(raw_json)
        if isinstance(data, list):
            return data
    except Exception as exc:
        logger.debug("Failed to parse SoM json payload: %s", exc)
    return []


def format_som_annotation_context(elements: list[dict[str, Any]], max_items: int = 60) -> str:
    if not elements:
        return ""
    lines = ["Visual Element Annotations (badge number [N] -> ref & action):"]
    for el in elements[:max_items]:
        idx = el.get("index", "?")
        ref = el.get("ref", f"@v{idx}")
        tag = el.get("tag", "")
        role = f"role={el.get('role')}" if el.get("role") else ""
        raw_text = re.sub(r"\s+", " ", str(el.get("text", ""))).strip()
        text = json.dumps(raw_text, ensure_ascii=False) if raw_text else ""

        cx, cy = el.get("cx", 0), el.get("cy", 0)
        desc = " ".join(filter(None, [tag, role, text]))
        lines.append(f"[{idx}] {ref}: {desc} at ({cx}, {cy})")
    if len(elements) > max_items:
        leftover = len(elements) - max_items
        lines.append(f"... and {leftover} more element{'s' if leftover != 1 else ''}.")
    return "\n".join(lines)
