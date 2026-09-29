import contextlib
import json
import time
from collections.abc import Callable
from typing import Any

# 点开自定义下拉后等待展开动画的时长。
_CUSTOM_DROPDOWN_OPEN_DELAY_S = 0.5


def select_option_with_eval(
    eval_fn: Callable[[str], dict[str, Any]],
    ref: str,
    *,
    value: str | None = None,
    label: str | None = None,
    index: int | None = None,
) -> dict[str, Any]:
    """在 <select> 或自定义下拉菜单中选择目标项；eval_fn 为 supervisor.evaluate_runtime。"""
    if value is None and label is None and index is None:
        return {"success": False, "error": "At least one of `value`, `label`, or `index` must be provided."}

    label_query = label or value or ""

    ref_id = ref.removeprefix("@")
    safe_ref = json.dumps(ref_id)

    if value is not None:
        value_str = str(value)
        match_expr = f"o.value==={json.dumps(value_str)}||o.textContent.trim()==={json.dumps(value_str)}"
    elif label is not None:
        match_expr = f"(o.textContent||'').toLowerCase().includes({json.dumps(label.lower())})"
    else:
        match_expr = f"i==={index}"

    select_js = (
        "(function(){"
        f"const el=document.querySelector('[aria-ref=' + {safe_ref} + ']') || document.querySelector('[data-spiritagent-som=' + {safe_ref} + ']');"
        "if(!el)return{_:'not_found'};"
        "if(el.tagName==='SELECT'){"
        "const opts=Array.from(el.options);"
        "let matched=false;"
        f"for(let i=0;i<opts.length;i++){{const o=opts[i];if({match_expr}){{o.selected=true;matched=true;}}}}"
        "if(!matched)return{_:'native_no_match'};"
        "el.dispatchEvent(new Event('change',{bubbles:true}));"
        "return{_:'native',value:el.value,text:el.options[el.selectedIndex]?.text||''};"
        "}"
        "window.__sa_select_trigger=el;"
        "el.click();"
        "return{_:'clicked'};"
        "})()"
    )

    parsed = eval_fn(select_js)
    if not parsed.get("ok"):
        return {"success": False, "error": f"browser_select: CDP eval failed: {parsed.get('error', 'unknown')}"}

    result = parsed.get("result")
    if not isinstance(result, dict):
        return {"success": False, "error": "browser_select: invalid result payload"}

    if result.get("_") == "native":
        return {"success": True, "selected": result.get("value"), "text": result.get("text"), "method": "native"}
    if result.get("_") == "native_no_match":
        # 用真实传入参数构造错误信息：index= 与 value/label 区分开，
        # 模型能区分「index=3 不存在」与「value=foo 没匹配」。
        if value is not None:
            ident = f"value={value!r}"
        elif label is not None:
            ident = f"label={label!r}"
        else:
            ident = f"index={index!r}"
        return {"success": False, "error": f"browser_select: no option matched {ident} in native <select>"}
    if result.get("_") == "not_found":
        return {"success": False, "error": f"browser_select: element {ref} not found. Run browser_snapshot first."}

    # 自定义下拉：等待动画展开并查找 option
    time.sleep(_CUSTOM_DROPDOWN_OPEN_DELAY_S)
    if index is not None and value is None and label is None:
        custom_match_js = f"if(i==={index}&&o.getBoundingClientRect().width>0){{o.click();return{{_:'custom',text:o.textContent.trim()}};}}"
    else:
        custom_match_js = "if((o.textContent||'').toLowerCase().includes(q)&&o.getBoundingClientRect().width>0){o.click();return{_:'custom',text:o.textContent.trim()};}"

    kb_js = (
        "(function(){"
        "const trigger=window.__sa_select_trigger;"
        "if(!trigger)return{_:'no_trigger'};"
        "const ctrlId=trigger.getAttribute('aria-controls')||trigger.getAttribute('aria-owns');"
        "const ctrl=ctrlId?document.getElementById(ctrlId):null;"
        'const container=trigger.closest(\'[role="listbox"],[role="menu"],ul,ol,.dropdown-menu,[class*="dropdown"],[class*="menu"],[class*="select"],[class*="listbox"]\');'
        "let opts=[];"
        'if(ctrl)opts=Array.from(ctrl.querySelectorAll(\'[role="option"], [class*="option"], [class*="item"], li\'));'
        'if(!opts.length&&container)opts=Array.from(container.querySelectorAll(\'[role="option"], [class*="option"], [class*="item"], li\'));'
        'if(!opts.length)opts=Array.from(document.querySelectorAll(\'[role="option"], [class*="option"], [class*="item"], li\'));'
        "const q=" + json.dumps(label_query.lower()) + ";"
        "for(let i=0;i<opts.length;i++){const o=opts[i];" + custom_match_js + "}"
        "return{_:'no_match'};"
        "})()"
    )

    # 所有出口都清掉 window 上的触发元素引用，防止被引用 DOM 节点驻留。
    try:
        kb_parsed = eval_fn(kb_js)
        if not kb_parsed.get("ok"):
            return {"success": False, "error": f"browser_select: CDP eval failed: {kb_parsed.get('error', 'unknown')}"}
        kb_result = kb_parsed.get("result")
        if isinstance(kb_result, dict) and kb_result.get("_") == "custom":
            return {"success": True, "selected": kb_result.get("text"), "method": "custom_click"}
        if isinstance(kb_result, dict) and kb_result.get("_") == "no_trigger":
            return {
                "success": False,
                "error": "browser_select: dropdown trigger reference lost between clicks (page may have reloaded)",
            }

        return {
            "success": False,
            "error": f"browser_select: option matching {label_query!r} not found in custom dropdown",
        }
    finally:
        with contextlib.suppress(Exception):
            eval_fn("delete window.__sa_select_trigger;")
