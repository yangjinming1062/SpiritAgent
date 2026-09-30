"""backend 服务分层架构检查（规则与例外理由见 backend/README.md「services 依赖边界」）。"""

import ast
import os
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
SKIP_DIRS = {".venv", "__pycache__", ".git", "node_modules", "alembic"}

# rank 越小越底层；导入方向必须 rank(importer) >= rank(target)。
RANK = {
    "services.contracts": 0,
    "services.domains": 1,
    "services.infrastructure": 2,
    "services.application": 3,
    "services.adapters": 4,
    "api": 5,
    "bootstrap": 6,
    "main": 6,
}
# rank 例外：允许向更高 rank 导入。
UPWARD_ALLOWED = {
    ("services.domains", "services.infrastructure"),
}
# application 包级单向流程依赖（理由见 backend/README.md）。
APPLICATION_FLOW_EDGES = {
    ("services.application.automation", "services.application.chat"),
    ("services.application.chat", "services.application.nightly"),
    ("services.application.nightly", "services.application.generation"),
    ("services.application.automation", "services.application.nightly"),
    ("services.application.actions", "services.application.generation"),
    ("services.application.chat", "services.application.actions"),
    ("services.application.nightly", "services.application.actions"),
}
# domains 跨域单向依赖（理由见 backend/README.md「services 依赖边界」）。
DOMAIN_FLOW_EDGES = {
    ("services.domains.backup", "services.domains.actions"),
    ("services.domains.companion", "services.domains.memory"),
    ("services.domains.journal", "services.domains.memory"),
    ("services.domains.companion", "services.domains.actions"),
}
# 各域可单向导入的底座域。
DOMAIN_BASE = "services.domains.conversation"
BOTTOM = ("common", "components", "modules", "prompts")


def pkg_of(module: str) -> str:
    if module.startswith("services."):
        parts = module.split(".")
        if len(parts) >= 3 and parts[1] in ("contracts", "domains", "application", "infrastructure", "adapters"):
            return ".".join(parts[:2])
        return "services"
    if module == "services":
        return "services"
    return module.split(".", maxsplit=1)[0]


def domain_of(module: str) -> str:
    """取 services.domains.X / services.application.X 的域级包名。"""
    parts = module.split(".")
    if len(parts) >= 3 and parts[0] == "services" and parts[1] in ("domains", "application"):
        return ".".join(parts[:3])
    return module


def layer_of(pkg: str) -> str | None:
    if pkg in ("common", "components", "modules", "prompts"):
        return pkg
    if pkg in RANK:
        return pkg
    if pkg == "services":
        return "services.contracts"  # 根包视为词汇层
    if pkg == "api" or pkg.startswith("api."):
        return "api"
    if pkg == "bootstrap" or pkg.startswith("bootstrap."):
        return "bootstrap"
    if pkg == "main":
        return "main"
    return None


def main() -> int:
    modules: dict[str, Path] = {}
    for dirpath, dirnames, filenames in os.walk(BACKEND):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(".py"):
                rel = Path(dirpath, fn).relative_to(BACKEND)
                name = ".".join(rel.with_suffix("").parts)
                if name.endswith(".__init__"):
                    name = name[: -len(".__init__")]
                modules[name] = Path(dirpath, fn)
    top_names = {m.split(".")[0] for m in modules}

    def resolve(name: str) -> str | None:
        parts = name.split(".")
        for i in range(len(parts), 0, -1):
            cand = ".".join(parts[:i])
            if cand in modules:
                return cand
        return None

    edges: dict[tuple[str, str], list[str]] = defaultdict(list)
    unresolved: list[str] = []

    def rel_base(name: str, is_pkg: bool, level: int, module: str | None) -> str | None:
        """相对导入解析为绝对模块字符串（不查存在性）：level L → 所在包上溯 L-1 层。"""
        parts = name.split(".")
        if not is_pkg:
            parts = parts[:-1]
        up = level - 1
        if len(parts) < up:
            return None
        base = parts[: len(parts) - up] if up else parts
        out = ".".join(base)
        if module:
            out = f"{out}.{module}" if out else module
        return out or None

    for name, path in sorted(modules.items()):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            unresolved.append(f"{path}: 语法错误 {exc}")
            continue
        rel = path.relative_to(REPO_ROOT)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    r = resolve(alias.name)
                    if r:
                        edges[(name, r)].append(f"{rel}:{node.lineno}")
                    elif alias.name.split(".")[0] in top_names:
                        unresolved.append(f"{rel}:{node.lineno} 无法解析 import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    target = node.module or ""
                else:
                    target = rel_base(name, path.name == "__init__.py", node.level, node.module) or ""
                if not target or target.split(".")[0] not in top_names:
                    continue
                base = resolve(target)
                if base is None or base != target:
                    unresolved.append(f"{rel}:{node.lineno} 无法解析 from {target}")
                    continue
                edges[(name, base)].append(f"{rel}:{node.lineno}")
                if node.level == 0:
                    for alias in node.names:
                        if alias.name == "*":
                            continue
                        r = resolve(f"{target}.{alias.name}")
                        if r:
                            edges[(name, r)].append(f"{rel}:{node.lineno}")

    errors: list[str] = []

    # 未解析导入
    for u in unresolved:
        errors.append(f"[unresolved] {u}")

    # 包级环（严格边）
    pkg_edges = {(pkg_of(a), pkg_of(b)) for (a, b) in edges if pkg_of(a) != pkg_of(b)}
    sccs = _tarjan(pkg_edges)
    for scc in sccs:
        members = " <-> ".join(sorted(scc))
        errors.append(f"[cycle] 包级依赖环: {members}")
        for a, b in sorted(pkg_edges):
            if a in scc and b in scc:
                evs = [e for (x, y), es in edges.items() if pkg_of(x) == a and pkg_of(y) == b for e in es]
                if evs:
                    errors.append(f"          {a} -> {b}  [{evs[0]}]")

    # 层间白名单与域隔离
    for (a, b), evs in sorted(edges.items()):
        la, lb = layer_of(pkg_of(a)), layer_of(pkg_of(b))
        if la is None or lb is None or a == b:
            continue
        where = f"{evs[0]}"
        if la in BOTTOM and lb not in BOTTOM:
            errors.append(f"[bottom-up] {la} 导入 {b}  [{where}]")
            continue
        if la == "main" and lb != "bootstrap":
            errors.append(f"[thin-main] main 导入 {b}（只允许 bootstrap）  [{where}]")
            continue
        if lb == "bootstrap" and la != "bootstrap" and la != "main":
            errors.append(f"[bootstrap-reverse] {a} 导入 bootstrap  [{where}]")
            continue
        if la == "services.contracts" and lb != "services.contracts":
            errors.append(f"[contracts-impure] contracts 导入 {b}  [{where}]")
            continue
        ra, rb = RANK.get(la), RANK.get(lb)
        if ra is None or rb is None:
            continue
        if rb > ra and (la, lb) not in UPWARD_ALLOWED:
            errors.append(f"[layer] {la} -> {lb} 越层  [{where}]")
            continue
        if la == lb and a != b:
            da, db = domain_of(a), domain_of(b)
            if la == "services.domains" and da != db:
                if db != DOMAIN_BASE and (da, db) not in DOMAIN_FLOW_EDGES:
                    errors.append(f"[domain-isolation] {a} 跨业务域导入 {b}  [{where}]")
            elif la == "services.application" and da != db and (da, db) not in APPLICATION_FLOW_EDGES:
                errors.append(f"[app-flow] {da} -> {db} 未声明的应用流程依赖  [{where}]")

    if errors:
        print(f"{len(errors)} 个架构问题:", file=sys.stderr)
        for e in errors:
            print(e, file=sys.stderr)
        return 1
    print("architecture ok")
    return 0


def _tarjan(edges: set[tuple[str, str]]) -> list[set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for a, b in edges:
        graph[a].add(b)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    onstack: set[str] = set()
    stack: list[str] = []
    sccs: list[set[str]] = []
    counter = 0
    for v in sorted({x for pair in edges for x in pair}):
        if v in index:
            continue
        work = [(v, iter(sorted(graph[v])))]
        index[v] = low[v] = counter
        counter += 1
        stack.append(v)
        onstack.add(v)
        while work:
            node, it = work[-1]
            advanced = False
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    onstack.add(w)
                    work.append((w, iter(sorted(graph[w]))))
                    advanced = True
                    break
                if w in onstack:
                    low[node] = min(low[node], index[w])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                comp = set()
                while True:
                    w = stack.pop()
                    onstack.discard(w)
                    comp.add(w)
                    if w == node:
                        break
                if len(comp) > 1:
                    sccs.append(comp)
    return sccs


if __name__ == "__main__":
    sys.exit(main())
