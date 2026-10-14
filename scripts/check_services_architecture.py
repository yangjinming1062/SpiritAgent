"""Backend 模块边界：导入方向、持久模型所有权、公共返回值和入口 SQL。"""

import argparse
import ast
import os
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
ROOTS = {"bootstrap", "entrypoints", "workflows", "features", "runtime", "infrastructure", "main"}
RETIRED = {"api", "services", "modules", "components", "common", "prompts"}
SQL_OPERATIONS = {
    "execute",
    "scalar",
    "scalars",
    "add",
    "add_all",
    "delete",
    "flush",
    "refresh",
    "commit",
    "rollback",
    "connection",
}


def runtime_nodes(node: ast.AST) -> Iterator[ast.AST]:
    yield node
    if isinstance(node, ast.If) and (
        isinstance(node.test, ast.Name)
        and node.test.id == "TYPE_CHECKING"
        or isinstance(node.test, ast.Attribute)
        and node.test.attr == "TYPE_CHECKING"
    ):
        for child in node.orelse:
            yield from runtime_nodes(child)
        return
    for child in ast.iter_child_nodes(node):
        yield from runtime_nodes(child)


def owner(module: str) -> str:
    parts = module.split(".")
    return ".".join(parts[:2]) if parts[0] in {"features", "workflows", "runtime"} and len(parts) > 1 else parts[0]


def import_target(module: str, path: Path, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    parts = module.split(".") if path.name == "__init__.py" else module.split(".")[:-1]
    parts = parts[: len(parts) - node.level + 1]
    return ".".join([*parts, *([node.module] if node.module else [])])


def annotation_names(annotation: ast.AST | None) -> set[str]:
    if annotation is None:
        return set()
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            annotation = ast.parse(annotation.value, mode="eval")
        except SyntaxError:
            return set()
    if isinstance(annotation, ast.Name):
        return {annotation.id}
    if isinstance(annotation, ast.Attribute):
        return {ast.unparse(annotation), *annotation_names(annotation.value)}
    if isinstance(annotation, ast.Subscript):
        base = ast.unparse(annotation.value).rsplit(".", 1)[-1]
        names = annotation_names(annotation.value)
        if base == "Literal":
            return names
        if base == "Annotated" and isinstance(annotation.slice, ast.Tuple):
            return names | annotation_names(annotation.slice.elts[0])
    return set().union(*(annotation_names(child) for child in ast.iter_child_nodes(annotation)))


def cycles(graph: dict[str, set[str]]) -> list[list[str]]:
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


def check(backend: Path) -> list[str]:
    modules: dict[str, tuple[Path, ast.Module]] = {}
    errors: list[str] = []
    paths: list[Path] = []
    for directory, names, files in os.walk(backend):
        skipped = {".venv", "__pycache__", ".ruff_cache"}
        if Path(directory) == backend:
            skipped |= {"data", "updates", "alembic"}
        names[:] = [name for name in names if name not in skipped]
        paths.extend(Path(directory, name) for name in files if name.endswith(".py"))
    for path in sorted(paths):
        module = ".".join(path.relative_to(backend).with_suffix("").parts).removesuffix(".__init__")
        try:
            modules[module] = (path, ast.parse(path.read_text(encoding="utf-8-sig")))
        except SyntaxError as exc:
            errors.append(f"[syntax] {path}:{exc.lineno}: {exc.msg}")

    models: set[tuple[str, str]] = set()
    imports: dict[str, dict[str, tuple[str, str]]] = defaultdict(dict)
    definitions: dict[tuple[str, str], ast.AST] = {}
    mutated_globals: dict[str, set[str]] = defaultdict(set)
    graph: dict[str, set[str]] = defaultdict(set)
    occurrences: list[tuple[str, str, str, int]] = []
    for module, (path, tree) in modules.items():
        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                definitions[(module, node.name)] = node
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        definitions[(module, target.id)] = node
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                definitions[(module, node.target.id)] = node
            elif isinstance(node, ast.TypeAlias):
                definitions[(module, node.name.id)] = node
            if isinstance(node, ast.ClassDef) and any(
                isinstance(child, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "__tablename__" for target in child.targets)
                for child in node.body
            ):
                models.add((module, node.name))
        for node in runtime_nodes(tree):
            if isinstance(node, ast.Global):
                mutated_globals[module].update(node.names)
            elif (
                isinstance(node, (ast.Subscript, ast.Attribute))
                and isinstance(node.ctx, ast.Store)
                and isinstance(node.value, ast.Name)
            ):
                mutated_globals[module].add(node.value.id)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.attr
                in {
                    "add",
                    "add_done_callback",
                    "append",
                    "clear",
                    "configure",
                    "discard",
                    "extend",
                    "insert",
                    "pop",
                    "register",
                    "remove",
                    "setdefault",
                    "update",
                }
            ):
                mutated_globals[module].add(node.func.value.id)
            if isinstance(node, ast.ImportFrom):
                target = import_target(module, path, node)
                for alias in node.names:
                    imports[module][alias.asname or alias.name] = (target, alias.name)
                root = target.split(".")[0]
                if root in RETIRED:
                    errors.append(f"[retired] {path}:{node.lineno}: {target}")
                if root not in ROOTS:
                    continue
                if target not in modules:
                    errors.append(f"[unresolved] {path}:{node.lineno}: from {target}")
                    continue
                if target != module:
                    graph[module].add(target)
                for alias in node.names:
                    occurrences.append((module, target, alias.name, node.lineno))
                    child = f"{target}.{alias.name}"
                    if child in modules and child != module:
                        graph[module].add(child)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    target = alias.name
                    imports[module][alias.asname or target.split(".")[0]] = (
                        target if alias.asname else target.split(".")[0],
                        "",
                    )
                    if target.split(".")[0] in RETIRED:
                        errors.append(f"[retired] {path}:{node.lineno}: {target}")
                    if target.split(".")[0] not in ROOTS:
                        continue
                    if target not in modules:
                        errors.append(f"[unresolved] {path}:{node.lineno}: import {target}")
                    elif target != module:
                        graph[module].add(target)
                    occurrences.append((module, target, "", node.lineno))

    def resolve_symbol(module: str, symbol: str, visited: set[tuple[str, str]] | None = None) -> tuple[str, str]:
        visited = set() if visited is None else visited
        key = (module, symbol)
        if key in visited:
            return key
        visited.add(key)
        if "." in symbol:
            first, remainder = symbol.split(".", 1)
            target, name = resolve_symbol(module, first, visited)
            if name:
                return key
            full = f"{target}.{remainder}"
            parts = full.split(".")
            for length in range(len(parts), 0, -1):
                prefix = ".".join(parts[:length])
                if prefix in modules:
                    return resolve_symbol(prefix, ".".join(parts[length:]), visited)
            return key
        if symbol in imports[module]:
            target, name = imports[module][symbol]
            child = f"{target}.{name}" if name else target
            if child in modules and (target, name) not in definitions:
                return child, ""
            return resolve_symbol(target, name, visited)
        definition = definitions.get(key)
        if isinstance(definition, (ast.Assign, ast.AnnAssign)) and isinstance(
            definition.value,
            (ast.Name, ast.Attribute),
        ):
            return resolve_symbol(module, ast.unparse(definition.value), visited)
        if definition is None and symbol and f"{module}.{symbol}" in modules:
            return f"{module}.{symbol}", ""
        return key

    def referenced_models(
        module: str,
        expression: ast.AST | None,
        *,
        annotation: bool = False,
        seen: set[tuple[str, str]] | None = None,
    ) -> set[tuple[str, str]]:
        if expression is None:
            return set()
        seen = set() if seen is None else seen
        names = (
            annotation_names(expression)
            if annotation
            else {ast.unparse(node) for node in ast.walk(expression) if isinstance(node, (ast.Name, ast.Attribute))}
        )
        result: set[tuple[str, str]] = set()
        for name in names:
            resolved = resolve_symbol(module, name)
            if resolved in models:
                result.add(resolved)
            elif resolved not in seen:
                definition = definitions.get(resolved)
                if isinstance(definition, (ast.Assign, ast.AnnAssign, ast.TypeAlias)):
                    result |= referenced_models(
                        resolved[0],
                        definition.value,
                        annotation=annotation,
                        seen=seen | {resolved},
                    )
        return result

    def value_class(module: str, definition: ast.ClassDef, seen: set[tuple[str, str]]) -> bool:
        key = module, definition.name
        if key in models or key in seen:
            return False
        for decorator in definition.decorator_list:
            decorated = decorator.func if isinstance(decorator, ast.Call) else decorator
            if resolve_symbol(module, ast.unparse(decorated)) == ("dataclasses", "dataclass"):
                return True
        for base in definition.bases:
            declared_base = base.value if isinstance(base, ast.Subscript) else base
            resolved = resolve_symbol(module, ast.unparse(declared_base))
            if resolved in {
                ("pydantic", "BaseModel"),
                ("pydantic", "RootModel"),
                ("pydantic.main", "BaseModel"),
                ("typing", "Protocol"),
                ("enum", "Enum"),
                ("enum", "StrEnum"),
                ("enum", "IntEnum"),
                ("enum", "IntFlag"),
            }:
                return True
            parent = definitions.get(resolved)
            if isinstance(parent, ast.ClassDef) and value_class(resolved[0], parent, seen | {key}):
                return True
            if (
                parent is None
                and isinstance(base, ast.Name)
                and base.id in {"Exception", "RuntimeError", "ValueError", "TypeError", "KeyError"}
            ):
                return True
        return False

    def type_alias(module: str, definition: ast.AST) -> bool:
        if isinstance(definition, ast.TypeAlias):
            return True
        if isinstance(definition, ast.AnnAssign) and any(
            resolve_symbol(module, name) == ("typing", "TypeAlias") for name in annotation_names(definition.annotation)
        ):
            return True
        if isinstance(definition, (ast.Assign, ast.AnnAssign)) and isinstance(definition.value, ast.Subscript):
            base = resolve_symbol(module, ast.unparse(definition.value.value))
            return (
                base[0] in {"typing", "collections.abc"}
                or isinstance(definition.value.value, ast.Name)
                and definition.value.value.id in {"tuple", "list", "dict", "set", "frozenset", "type"}
            )
        return False

    def static_value(module: str, expression: ast.AST | None, seen: set[tuple[str, str]]) -> bool:
        if expression is None or isinstance(expression, ast.Constant):
            return True
        if isinstance(expression, (ast.Name, ast.Attribute)):
            resolved = resolve_symbol(module, ast.unparse(expression))
            if resolved in seen or resolved in models:
                return False
            definition = definitions.get(resolved)
            if isinstance(definition, ast.ClassDef):
                return value_class(resolved[0], definition, set())
            if isinstance(definition, (ast.Assign, ast.AnnAssign, ast.TypeAlias)):
                return resolved[1] not in mutated_globals[resolved[0]] and static_value(
                    resolved[0],
                    definition.value,
                    seen | {resolved},
                )
            if definition is None:
                return (
                    isinstance(expression, ast.Name)
                    and expression.id
                    in {"str", "int", "float", "bool", "bytes", "object", "tuple", "list", "dict", "set", "frozenset"}
                    or resolved[0] in {"typing", "types"}
                    or resolved == ("datetime", "UTC")
                )
            return False
        if isinstance(expression, ast.Call):
            resolved = resolve_symbol(module, ast.unparse(expression.func))
            definition = definitions.get(resolved)
            constructor = isinstance(definition, ast.ClassDef) and value_class(resolved[0], definition, set())
            constructor |= isinstance(expression.func, ast.Name) and expression.func.id in {
                "tuple",
                "list",
                "dict",
                "set",
                "frozenset",
            }
            constructor |= resolved in {
                ("datetime", "timedelta"),
                ("datetime", "date"),
                ("re", "compile"),
                ("typing", "TypeVar"),
                ("typing", "ParamSpec"),
                ("typing", "NewType"),
                ("pydantic", "TypeAdapter"),
            }
            return (
                constructor
                and all(static_value(module, arg, seen) for arg in expression.args)
                and all(static_value(module, arg.value, seen) for arg in expression.keywords)
            )
        if isinstance(expression, (ast.List, ast.Tuple, ast.Set)):
            return all(static_value(module, item, seen) for item in expression.elts)
        if isinstance(expression, ast.Dict):
            return all(static_value(module, item, seen) for item in [*expression.keys, *expression.values])
        if isinstance(expression, (ast.Subscript, ast.BinOp, ast.UnaryOp)):
            return all(
                static_value(module, child, seen)
                for child in ast.iter_child_nodes(expression)
                if not isinstance(child, (ast.expr_context, ast.operator, ast.unaryop))
            )
        return False

    def public_value(module: str, symbol: str) -> bool:
        resolved = resolve_symbol(module, symbol)
        if resolved in models:
            return False
        definition = definitions.get(resolved)
        if isinstance(definition, ast.ClassDef):
            return value_class(resolved[0], definition, set())
        if isinstance(definition, (ast.Assign, ast.AnnAssign, ast.TypeAlias)):
            if resolved[1] in mutated_globals[resolved[0]]:
                return False
            if type_alias(resolved[0], definition):
                return not referenced_models(resolved[0], definition.value, annotation=True)
            return static_value(resolved[0], definition.value, {resolved})
        return False

    model_modules = {module for module, _ in models}
    feature_graph: dict[str, set[str]] = defaultdict(set)
    feature_origins: dict[tuple[str, str], list[str]] = defaultdict(list)
    for source, target, symbol, lineno in occurrences:
        path = modules[source][0]
        source_root, target_root = source.split(".")[0], target.split(".")[0]
        forbidden = (
            source_root == "infrastructure"
            and target_root in {"features", "workflows", "entrypoints", "bootstrap"}
            or source_root == "runtime"
            and target_root in {"features", "workflows", "entrypoints", "bootstrap"}
            or source_root == "features"
            and target_root in {"workflows", "entrypoints", "bootstrap"}
            or source_root == "workflows"
            and target_root in {"entrypoints", "bootstrap"}
            or source_root == "entrypoints"
            and target_root == "bootstrap"
        )
        if source_root == "infrastructure" and target_root == "runtime" and not public_value(target, symbol):
            forbidden = True
        if forbidden:
            errors.append(f"[direction] {path}:{lineno}: {source} -> {target}")
        if owner(source) == owner(target) or source_root == "bootstrap":
            continue
        resolved = resolve_symbol(target, symbol) if symbol else None
        model_aliases = (
            referenced_models(target, definitions.get(resolved).value)
            if isinstance(definitions.get(resolved), (ast.Assign, ast.AnnAssign, ast.TypeAlias))
            else set()
        )
        if (
            resolved in models
            or model_aliases
            or not symbol
            and target in model_modules
            or resolved is not None
            and not resolved[1]
            and resolved[0] in model_modules
        ):
            errors.append(f"[foreign-orm] {path}:{lineno}: {target}.{symbol}")
        if resolved is not None:
            definition = definitions.get(resolved)
            if isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for value in referenced_models(resolved[0], definition.returns, annotation=True):
                    errors.append(f"[orm-return] {path}:{lineno}: {target}.{symbol} returns {value[1]}")
        if source_root == "features" and target_root == "features" and symbol.startswith("_"):
            errors.append(f"[private-feature] {path}:{lineno}: {target}.{symbol}")
        if source_root == "features" and target_root == "features" and not public_value(target, symbol):
            source_owner, target_owner = owner(source), owner(target)
            feature_graph[source_owner].add(target_owner)
            feature_origins[(source_owner, target_owner)].append(f"{path}:{lineno}: {target}.{symbol}")

    for module, (path, tree) in modules.items():
        for node in runtime_nodes(tree):
            if not isinstance(node, ast.Call):
                continue
            resolved = resolve_symbol(module, ast.unparse(node.func))
            definition = definitions.get(resolved)
            if (
                not isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef))
                or owner(module) == owner(resolved[0])
                or module.startswith("bootstrap.")
            ):
                continue
            for value in referenced_models(resolved[0], definition.returns, annotation=True):
                errors.append(f"[orm-return] {path}:{node.lineno}: {resolved[0]}.{resolved[1]} returns {value[1]}")
        if not module.startswith("entrypoints."):
            continue
        for node in runtime_nodes(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in SQL_OPERATIONS
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"db", "session"}
            ):
                errors.append(f"[entrypoint-sql] {path}:{node.lineno}: {node.func.value.id}.{node.func.attr}")
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "sqlalchemy"
                and any(alias.name in {"select", "update", "delete", "insert", "text"} for alias in node.names)
            ):
                errors.append(f"[entrypoint-sql] {path}:{node.lineno}: SQL constructor import")
    for component in cycles(graph):
        errors.append("[cycle] " + " -> ".join(component))
    for component in cycles(feature_graph):
        members = set(component)
        for (source, target), origins in feature_origins.items():
            if source in members and target in members:
                for origin in origins:
                    errors.append(f"[feature-cycle] {source} -> {target}: {origin}")
    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", type=Path, default=BACKEND)
    args = parser.parse_args()
    errors = check(args.backend.resolve())
    for error in errors:
        print(error)
    if errors:
        print(f"architecture failed: {len(errors)} violation(s)")
        return 1
    print("architecture ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
