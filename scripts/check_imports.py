import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCAN_ROOTS = [REPO_ROOT / "backend", REPO_ROOT / "runner"]


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None


# A. 禁止 future annotations (Python 3.13 原生支持 PEP 585/604)


def check_future_annotations(path: Path) -> list[str]:
    tree = _parse(path)
    if tree is None:
        return []
    errors: list[str] = []
    for node in tree.body:
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "__future__"
            and any(alias.name == "annotations" for alias in node.names)
        ):
            errors.append(
                f"{path}:{node.lineno}: forbidden 'from __future__ import annotations' (Python 3.13+ does not need future annotations; run check_imports.py --fix to clean)",
            )
    return errors


def clean_future_annotations(path: Path) -> bool:
    """清理文件中的 ``from __future__ import annotations``。"""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return False

    try:
        tree = ast.parse(content)
    except SyntaxError:
        return False

    targets = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
    ]
    if not targets:
        return False

    lines = content.splitlines(keepends=True)
    for node in sorted(targets, key=lambda n: n.lineno, reverse=True):
        start_idx = node.lineno - 1
        end_idx = node.end_lineno if node.end_lineno is not None else node.lineno
        if len(node.names) == 1 and node.names[0].name == "annotations":
            prev_blank = start_idx > 0 and lines[start_idx - 1].strip() == ""
            next_blank = end_idx < len(lines) and lines[end_idx].strip() == ""
            if (prev_blank and next_blank) or (start_idx == 0 and next_blank):
                del lines[start_idx : end_idx + 1]
            else:
                del lines[start_idx:end_idx]
        else:
            stmt_lines = lines[start_idx:end_idx]
            stmt_text = "".join(stmt_lines)
            stmt_text = re.sub(r"\bannotations\s*,\s*", "", stmt_text)
            stmt_text = re.sub(r",\s*\bannotations\b", "", stmt_text)
            stmt_text = re.sub(r"\bannotations\b", "", stmt_text)
            lines[start_idx:end_idx] = [stmt_text]

    new_content = "".join(lines)
    if new_content != content:
        path.write_text(new_content, encoding="utf-8")
        return True
    return False


def _tc_ranges(tree: ast.Module) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for node in tree.body:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            ranges.append((node.lineno, node.end_lineno or node.lineno))
    return ranges


def _in_any(ranges: list[tuple[int, int]], lineno: int) -> bool:
    return any(start <= lineno <= end for start, end in ranges)


def _names_imported_under_tc(tree: ast.Module, tc_ranges: list[tuple[int, int]]) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Import | ast.ImportFrom):
            continue
        if not _in_any(tc_ranges, node.lineno):
            continue
        for alias in node.names:
            out.add(alias.asname or alias.name.split(".")[0])
    return out


def _names_referenced_outside_tc(tree: ast.Module, tc_ranges: list[tuple[int, int]]) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        lineno = getattr(node, "lineno", None)
        if lineno is None or _in_any(tc_ranges, lineno):
            continue
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            root = node
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name):
                out.add(root.id)
    return out


def check_type_checking_leak(path: Path) -> list[str]:
    tree = _parse(path)
    if tree is None:
        return []
    tc_ranges = _tc_ranges(tree)
    if not tc_ranges:
        return []
    tc_imports = _names_imported_under_tc(tree, tc_ranges)
    if not tc_imports:
        return []
    used_outside = _names_referenced_outside_tc(tree, tc_ranges)
    leaks = sorted(tc_imports & used_outside)
    return [
        f"{path}: '{name}' imported under TYPE_CHECKING but used outside it (annotation evaluated at runtime → NameError)"
        for name in leaks
    ]


# B. Facade 一致性


def _facade_exports(init_path: Path) -> set[str] | None:
    """解析 facade 的 ``__init__.py`` 中的对外导出名。

    无任何 re-export 时返回 ``None``，调用方应跳过；否则返回 ``__all__`` 与模块级 ``from .X import Y`` 引入名两者的并集。
    """
    tree = _parse(init_path)
    if tree is None:
        return None

    names: set[str] = set()
    has_module_level_imports = False

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if isinstance(node.value, ast.List | ast.Tuple):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                names.add(elt.value)
                    break
        elif isinstance(node, ast.ImportFrom) and node.level >= 1:
            has_module_level_imports = True
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])

    if not has_module_level_imports and not names:
        return None
    return names


def _importer_scan_root(path: Path) -> Path | None:
    """判断文件所属扫描根（backend/ 或 runner/）。"""
    resolved = path.resolve()
    for root in SCAN_ROOTS:
        try:
            resolved.relative_to(root)
            return root
        except ValueError:
            continue
    return None


def _resolve_facade_init(importer: Path, level: int, module: str | None) -> Path | None:
    """把 ``ast.ImportFrom`` 解析到 facade 的 ``__init__.py`` 路径。

    支持：绝对导入 ``from X import Y``（在 importer 所属扫描根内解析）、单段相对 ``from .X import Y`` / ``from ..X import Y``（沿 importer 的父目录链解析）。多段模块（如 ``from utils.constants import X``）直接跳过——那些指向普通模块，无法静态内省其 facade 状态。
    """
    if level == 0:
        if not module or "." in module:
            return None
        scan_root = _importer_scan_root(importer)
        if scan_root is None:
            return None
        candidate = (scan_root / module / "__init__.py").resolve()
        return candidate if candidate.is_file() else None

    # Handle relative imports (level >= 1)
    if module and "." in module:
        return None
    parent_parts = importer.parent.resolve().parts
    up = level - 1
    if len(parent_parts) < up:
        return None
    base = Path(*parent_parts[: len(parent_parts) - up]) if up else Path(*parent_parts)
    target = base if module is None else base / module
    candidate = (target / "__init__.py").resolve()
    return candidate if candidate.is_file() else None


def check_facade_consistency(path: Path) -> list[str]:
    """对每个解析到带 re-export 的 facade 的本地导入，要求被引入的名字必须出现在 re-export 列表里。

    跳过：facade 自身的 ``__init__.py``、目标无 ``__init__.py``（普通模块）、无 re-export 的 facade。
    """
    if path.name == "__init__.py":
        return []

    tree = _parse(path)
    if tree is None:
        return []

    errors: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue

        init_path = _resolve_facade_init(path, node.level, node.module)
        if init_path is None:
            continue
        exports = _facade_exports(init_path)
        if exports is None:
            continue

        for alias in node.names:
            if alias.name == "*":
                continue
            if alias.name not in exports:
                where = (
                    f"absolute import '{node.module}'"
                    if node.level == 0
                    else f"relative import (level={node.level}, module='{node.module}')"
                )
                rel = init_path.relative_to(REPO_ROOT).parent.as_posix()
                errors.append(
                    f"{path}:{node.lineno}: '{alias.name}' imported via {where} "
                    f"is not re-exported by {rel}/__init__ — "
                    "either add the re-export or import from the underlying module",
                )
    return errors


# main


def main(argv: list[str]) -> int:
    strict = False
    fix = False
    args: list[str] = []
    for a in argv:
        if a == "--strict-imports":
            strict = True
        elif a in ("--fix", "--clean"):
            fix = True
        else:
            args.append(a)

    if args:
        targets = [Path(a).resolve() for a in args]
    else:
        targets = []
        for root in SCAN_ROOTS:
            if not root.is_dir():
                continue
            targets.extend(p for p in root.rglob("*.py") if ".venv" not in p.parts and "__pycache__" not in p.parts)

    if fix:
        fixed_count = 0
        for path in targets:
            if not path.is_file():
                continue
            if clean_future_annotations(path):
                print(f"Fixed: removed 'from __future__ import annotations' from {path}")
                fixed_count += 1
        if fixed_count:
            print(f"\nCleaned {fixed_count} file(s).")

    diagnostics: list[str] = []
    for path in targets:
        if not path.is_file():
            continue
        diagnostics.extend(check_future_annotations(path))
        diagnostics.extend(check_type_checking_leak(path))
        diagnostics.extend(check_facade_consistency(path))

    if diagnostics:
        for d in diagnostics:
            print(d, file=sys.stderr)
        suffix = " (strict)" if strict else " (advisory)"
        print(f"\n{len(diagnostics)} import-shape issue(s){suffix}", file=sys.stderr)
        return 1 if strict else 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
