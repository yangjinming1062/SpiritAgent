from typing import Any

SKIP_ROLES = frozenset({"none", "presentation", "generic", "InlineTextBox", "LineBreak", "Divider"})

INTERACTIVE_ROLES = frozenset(
    {
        "button",
        "link",
        "textbox",
        "searchbox",
        "combobox",
        "checkbox",
        "radio",
        "switch",
        "slider",
        "spinbutton",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "option",
        "tab",
        "treeitem",
        "row",
        "columnheader",
        "rowheader",
        "cell",
        "gridcell",
    },
)


def _get_ax_value(val_obj: Any) -> Any:
    return val_obj.get("value") if isinstance(val_obj, dict) else val_obj


def _get_node_property(node: dict[str, Any], prop_name: str) -> Any:
    props = node.get("properties", [])
    if isinstance(props, list):
        for p in props:
            if isinstance(p, dict) and p.get("name") == prop_name:
                return _get_ax_value(p.get("value"))
    return None


def build_snapshot_text(
    nodes: list[dict[str, Any]],
    *,
    interactive_only: bool = False,
    max_depth: int = 50,
) -> tuple[str, dict[str, dict[str, Any]]]:
    """遍历 AXTree 节点列表，构建紧凑或完整的可访问性树文本快照，并返回 ref 映射表。"""
    if not nodes:
        return "", {}

    node_by_id: dict[str, dict[str, Any]] = {}
    child_to_parent: dict[str, str] = {}

    for n in nodes:
        nid = str(n.get("nodeId", ""))
        node_by_id[nid] = n
        for cid in n.get("childIds", []):
            child_to_parent[str(cid)] = nid

    root_ids = [nid for nid in node_by_id if nid not in child_to_parent]
    if not root_ids and node_by_id:
        root_ids = [next(iter(node_by_id))]

    ref_counter = 0
    refs_map: dict[str, dict[str, Any]] = {}
    lines: list[str] = []
    effective_max_depth = max_depth

    def _traverse(node_id: str, depth: int) -> None:
        nonlocal ref_counter
        node = node_by_id.get(node_id)
        if not node:
            return

        is_ignored = bool(node.get("ignored", False))
        role = str(_get_ax_value(node.get("role")) or "")
        name = str(_get_ax_value(node.get("name")) or "").strip()
        backend_id = node.get("backendDOMNodeId")

        # ignored 节点本身不出现在快照中，但继续递归其子节点
        if is_ignored:
            for child_id in node.get("childIds", []):
                _traverse(str(child_id), depth)
            return

        is_interactive = role in INTERACTIVE_ROLES or any(
            role.startswith(prefix) for prefix in ("menuitem", "button", "link", "input", "select")
        )

        should_skip_role = role in SKIP_ROLES
        if should_skip_role and not name and not is_interactive:
            for child_id in node.get("childIds", []):
                _traverse(str(child_id), depth)
            return

        if interactive_only and not is_interactive:
            for child_id in node.get("childIds", []):
                _traverse(str(child_id), depth)
            return

        if depth > effective_max_depth:
            return

        ref_id: str | None = None
        if is_interactive or (not interactive_only and name and not should_skip_role):
            ref_counter += 1
            ref_num = f"e{ref_counter}"
            ref_id = ref_num
            info = {
                "ref": ref_num,
                "backendNodeId": backend_id,
                "role": role,
                "name": name,
            }
            refs_map[ref_num] = info
            refs_map[f"@{ref_num}"] = info

        prop_parts: list[str] = []

        if role == "link":
            url = _get_node_property(node, "url") or _get_node_property(node, "href")
            if url:
                prop_parts.append(f'href="{url}"')

        if role in ("textbox", "searchbox"):
            val = _get_node_property(node, "value")
            if val is not None:
                prop_parts.append(f'value="{val}"')
            if _get_node_property(node, "required"):
                prop_parts.append("required")

        if role == "button":
            exp = _get_node_property(node, "expanded")
            if exp is not None:
                prop_parts.append(f"expanded={str(exp).lower()}")
            pressed = _get_node_property(node, "pressed")
            if pressed is not None:
                prop_parts.append(f"pressed={str(pressed).lower()}")

        if role in ("checkbox", "switch", "radio", "menuitemcheckbox", "menuitemradio"):
            checked = _get_node_property(node, "checked")
            if checked is not None:
                prop_parts.append(f"checked={str(checked).lower()}")

        if role == "combobox":
            exp = _get_node_property(node, "expanded")
            if exp is not None:
                prop_parts.append(f"expanded={str(exp).lower()}")

        indent = "  " * depth
        escaped_name = name.replace("\\", "\\\\").replace('"', '\\"')
        ref_prefix = f"[ref={ref_id}] " if ref_id else ""
        props_suffix = f"  [{' '.join(prop_parts)}]" if prop_parts else ""

        line = (
            f'{indent}{ref_prefix}{role} "{escaped_name}"{props_suffix}'
            if name
            else f"{indent}{ref_prefix}{role}{props_suffix}"
        )
        lines.append(line)

        for child_id in node.get("childIds", []):
            _traverse(str(child_id), depth + 1)

    for root_id in root_ids:
        _traverse(root_id, 0)

    return "\n".join(lines), refs_map
