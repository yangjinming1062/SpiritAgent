#!/usr/bin/env python3
"""Runner 静态架构门禁：依赖方向、能力边界、导入环与资源装配归属。"""

import argparse
import ast
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path

PACKAGE = "spirit_agent_runner"
RUNNER = Path(__file__).resolve().parent.parent / "runner" / PACKAGE
FORBIDDEN_ROOTS = {"tools", "envs", "utils", "server", "runner_version"}
FORBIDDEN_LAYERS = {
    "contracts": {"infrastructure", "runtime", "features", "transport", "bootstrap", "__main__"},
    "infrastructure": {"runtime", "features", "transport", "bootstrap", "__main__"},
    "runtime": {"features", "transport", "bootstrap", "__main__"},
    "features": {"runtime", "transport", "bootstrap", "__main__"},
    "transport": {"features", "bootstrap", "__main__"},
}
MUTATORS = {"add", "append", "clear", "discard", "extend", "insert", "pop", "remove", "setdefault", "update"}
IMPLICIT_CONFIG = {f"{PACKAGE}.contracts.config.load_config", f"{PACKAGE}.contracts.context.current_context"}


def qualified_name(node: ast.AST, bindings: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return bindings.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = qualified_name(node.value, bindings)
        return f"{parent}.{node.attr}" if parent else ""
    return ""


def import_base(module: str, path: Path, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    package = module.split(".") if path.name == "__init__.py" else module.split(".")[:-1]
    if node.level > len(package):
        return ""
    return ".".join([*package[: len(package) - node.level + 1], *([node.module] if node.module else [])])


def imported_names(module: str, path: Path, tree: ast.Module) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bindings[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom):
            base = import_base(module, path, node)
            for alias in node.names:
                bindings[alias.asname or alias.name] = f"{base}.{alias.name}"
    return bindings


def import_nodes(node: ast.AST, bindings: dict[str, str], type_only: bool = False) -> Iterator[tuple[ast.AST, bool]]:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        yield node, type_only
    if isinstance(node, ast.If) and qualified_name(node.test, bindings) in {"TYPE_CHECKING", "typing.TYPE_CHECKING"}:
        for child in node.body:
            yield from import_nodes(child, bindings, True)
        for child in node.orelse:
            yield from import_nodes(child, bindings, type_only)
        return
    for child in ast.iter_child_nodes(node):
        yield from import_nodes(child, bindings, type_only)


def owner(module: str) -> str:
    parts = module.split(".")
    return ".".join(parts[:3] if len(parts) > 2 and parts[1] == "features" else parts[:2])


def layer(module: str) -> str:
    parts = module.split(".")
    return parts[1] if len(parts) > 1 else ""


def import_time_nodes(node: ast.AST, bindings: dict[str, str]) -> Iterator[ast.AST]:
    """函数体延后执行；装饰器、默认值与类体仍在导入时求值。"""
    yield node
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for child in [*node.decorator_list, *node.args.defaults, *(value for value in node.args.kw_defaults if value)]:
            yield from import_time_nodes(child, bindings)
        return
    if isinstance(node, ast.TypeAlias):
        return
    if isinstance(node, ast.If) and qualified_name(node.test, bindings) in {"TYPE_CHECKING", "typing.TYPE_CHECKING"}:
        for child in node.orelse:
            yield from import_time_nodes(child, bindings)
        return
    for child in ast.iter_child_nodes(node):
        yield from import_time_nodes(child, bindings)


def module_assignments(tree: ast.Module) -> dict[str, ast.AST]:
    assignments: dict[str, ast.AST] = {}

    def visit(node: ast.AST) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.TypeAlias)):
            return
        targets = (
            node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
        for target in targets:
            if isinstance(target, ast.Name) and value is not None:
                assignments[target.id] = value
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return assignments


def mutated_collections(tree: ast.Module, candidates: set[str]) -> set[str]:
    mutated: set[str] = set()

    def function_nodes(node: ast.AST, function: ast.AST) -> Iterator[ast.AST]:
        yield node
        if node is not function and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return
        for child in ast.iter_child_nodes(node):
            yield from function_nodes(child, function)

    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        nodes = list(function_nodes(function, function))
        local = {node.id for node in nodes if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)}
        local.update(node.arg for node in ast.walk(function.args) if isinstance(node, ast.arg))
        declared = {name for node in nodes if isinstance(node, ast.Global) for name in node.names}
        visible = candidates.difference(local.difference(declared))
        for node in nodes:
            target: ast.AST | None = None
            if isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)):
                target = node.value
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in MUTATORS:
                target = node.func.value
            if isinstance(target, ast.Name) and target.id in visible:
                mutated.add(target.id)
        mutated.update(visible.intersection(declared))
    return mutated


def dependency_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    indexes: dict[str, int] = {}
    lows: dict[str, int] = {}
    stack: list[str] = []
    active: set[str] = set()
    found: list[list[str]] = []

    def visit(module: str) -> None:
        indexes[module] = lows[module] = len(indexes)
        stack.append(module)
        active.add(module)
        for target in sorted(graph.get(module, ())):
            if target not in indexes:
                visit(target)
                lows[module] = min(lows[module], lows[target])
            elif target in active:
                lows[module] = min(lows[module], indexes[target])
        if lows[module] == indexes[module]:
            component: list[str] = []
            while True:
                target = stack.pop()
                active.remove(target)
                component.append(target)
                if target == module:
                    break
            if len(component) > 1:
                found.append(sorted(component))

    for module in sorted(graph):
        if module not in indexes:
            visit(module)
    return found


def check(runner: Path) -> list[str]:
    errors: list[str] = []
    if not runner.is_dir():
        return [f"[package] Runner package directory not found: {runner}"]
    modules: dict[str, tuple[Path, ast.Module]] = {}
    for path in sorted(runner.rglob("*.py")):
        if any(part in {"__pycache__", ".venv", "build", "dist"} for part in path.relative_to(runner).parts):
            continue
        relative = path.relative_to(runner).with_suffix("")
        parts = relative.parts[:-1] if relative.name == "__init__" else relative.parts
        module = ".".join([PACKAGE, *parts])
        try:
            modules[module] = (path, ast.parse(path.read_text(encoding="utf-8-sig")))
        except (OSError, SyntaxError) as exc:
            errors.append(f"[syntax] {path}:{getattr(exc, 'lineno', 1)}: {exc}")
    for root_name in sorted(FORBIDDEN_ROOTS):
        file = runner.parent / f"{root_name}.py"
        directory = runner.parent / root_name
        sources = [file] if file.is_file() else []
        if directory.is_dir():
            sources.extend(path for path in directory.rglob("*.py") if "__pycache__" not in path.parts)
        if sources:
            errors.append(f"[package-layout] source must be inside {PACKAGE}: {sources[0]}")

    graph: dict[str, set[str]] = defaultdict(set)
    for module, (path, tree) in modules.items():
        bindings = imported_names(module, path, tree)

        def report(rule: str, line: int, message: str) -> None:
            errors.append(f"[{rule}] {path}:{line}: {message}")

        for node, type_only in import_nodes(tree, bindings):
            if isinstance(node, ast.Import):
                imports = [(alias.name, "") for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = import_base(module, path, node)
                if node.level and not base:
                    report("relative-import", node.lineno, "relative import escapes Runner package")
                imports = [
                    (f"{base}.{alias.name}" if f"{base}.{alias.name}" in modules else base, alias.name)
                    for alias in node.names
                ]
            else:
                continue
            for target, symbol in imports:
                if target.split(".")[0] in FORBIDDEN_ROOTS:
                    report("top-level-import", node.lineno, f"import must use the {PACKAGE} package: {target}")
                if target != PACKAGE and not target.startswith(f"{PACKAGE}."):
                    continue
                if layer(module) == "infrastructure" and (
                    f"{target}.{symbol}" in IMPLICIT_CONFIG
                    or symbol == "*"
                    and target in {f"{PACKAGE}.contracts.config", f"{PACKAGE}.contracts.context"}
                ):
                    report("config-boundary", node.lineno, "pass configuration explicitly")
                if target not in modules:
                    report("missing-module", node.lineno, f"internal module does not exist: {target}")
                if layer(target) in FORBIDDEN_LAYERS.get(layer(module), set()):
                    report("layer", node.lineno, f"{layer(module)} cannot import {target}")
                if (
                    layer(module) == layer(target) == "features"
                    and owner(module) != owner(target)
                    and not (
                        owner(module) == f"{PACKAGE}.features.files" and target == f"{PACKAGE}.features.execution.api"
                    )
                ):
                    report("feature", node.lineno, f"cross-feature import must use an injected port: {target}")
                if symbol.startswith("_") and not symbol.startswith("__") and owner(module) != owner(target):
                    report("private", node.lineno, f"private name crosses an ownership boundary: {target}.{symbol}")
                if not type_only and target in modules and target != module:
                    graph[module].add(target)

        evaluated = list(import_time_nodes(tree, bindings))
        bootstrap = module == f"{PACKAGE}.bootstrap"
        assignments = module_assignments(tree)
        thread_names = {
            name
            for name, value in assignments.items()
            if isinstance(value, ast.Call) and qualified_name(value.func, bindings) == "threading.Thread"
        }
        collection_names = {
            name
            for name, value in assignments.items()
            if isinstance(value, (ast.Dict, ast.List, ast.Set))
            or isinstance(value, ast.Call)
            and qualified_name(value.func, bindings)
            in {"dict", "list", "set", "collections.defaultdict", "collections.deque"}
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                target = qualified_name(node.value, bindings)
                if layer(module) == "infrastructure" and qualified_name(node, bindings) in IMPLICIT_CONFIG:
                    report("config-boundary", node.lineno, "pass configuration explicitly")
                if (
                    target in modules
                    and node.attr.startswith("_")
                    and not node.attr.startswith("__")
                    and owner(module) != owner(target)
                ):
                    report(
                        "private",
                        node.lineno,
                        f"private module member crosses an ownership boundary: {target}.{node.attr}",
                    )
            if isinstance(node, ast.Call):
                name = qualified_name(node.func, bindings)
                if name == "pkgutil.walk_packages":
                    report("discovery", node.lineno, "tool discovery must use explicit feature definitions")
                if name == "atexit.register" and not bootstrap:
                    report("lifecycle", node.lineno, "atexit fallback belongs to bootstrap")
        if bootstrap:
            continue
        for name, value in assignments.items():
            constructor = qualified_name(value.func, bindings).rsplit(".", 1)[-1] if isinstance(value, ast.Call) else ""
            if constructor.endswith(("Manager", "Registry", "Supervisor", "Runtime", "Store")):
                report(
                    "ownership",
                    value.lineno,
                    f"resource owner {name} must be constructed by bootstrap or an owning service",
                )
        for name in sorted(mutated_collections(tree, collection_names)):
            report(
                "ownership",
                assignments[name].lineno,
                f"mutable global collection {name} must belong to an owning service",
            )
        for node in evaluated:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    if qualified_name(decorator, bindings).rsplit(".", 1)[-1] == "register_tool":
                        report("registration", decorator.lineno, "import-time tool registration is forbidden")
            if not isinstance(node, ast.Call):
                continue
            name = qualified_name(node.func, bindings)
            if name.rsplit(".", 1)[-1] == "register_tool":
                report("registration", node.lineno, "import-time tool registration is forbidden")
            starts_thread = (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "start"
                and (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id in thread_names
                    or isinstance(node.func.value, ast.Call)
                    and qualified_name(node.func.value.func, bindings) == "threading.Thread"
                )
            )
            if starts_thread or name in {"asyncio.create_task", "asyncio.ensure_future"}:
                report("lifecycle", node.lineno, "background work must start through an owning service lifecycle")
    for component in dependency_cycles(graph):
        errors.append(f"[cycle] mutually dependent modules: {', '.join(component)}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner", type=Path, default=RUNNER, help="Runner package directory")
    errors = check(parser.parse_args().runner.resolve())
    if errors:
        print("\n".join(errors))
        print(f"Runner architecture check failed: {len(errors)} issue(s)")
        return 1
    print("Runner architecture check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
