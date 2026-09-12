"""backend 服务分层架构检查。

检查项（与 backend/README.md §3 架构地图及 RULES 模块独立原则对应）：
1. 站内导入必须可解析（不含 .venv / site-packages / 标准库）。
2. 包级（模块级导入）不允许出现依赖环。
3. 层间白名单：contracts 纯净；domains 不跨业务域、不依赖 application/adapters；
   infrastructure 不认识业务（禁止导入 domains/application/adapters）；
   application 不导入 adapters；除 bootstrap 外不得导入 bootstrap；
   common/components/modules 不得反向导入服务实现。
4. application 内只允许显式声明的单向流程依赖。
"""

import ast
import os
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
SKIP_DIRS = {".venv", "__pycache__", ".git", "node_modules", "alembic"}

# rank 越小越底层；导入方向必须 rank(importer) >= rank(target)（向更底层导入）。
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
# rank 规则之外的例外：允许向更高 rank 导入。
UPWARD_ALLOWED = {
    ("services.domains", "services.infrastructure"),
}
# application 内显式声明的单向流程依赖（包级）。
APPLICATION_FLOW_EDGES = {
    ("services.application.automation", "services.application.chat"),
    ("services.application.chat", "services.application.nightly"),
    ("services.application.nightly", "services.application.generation"),
    ("services.application.automation", "services.application.nightly"),
}
BOTTOM = ("common", "components", "modules")


def pkg_of(module: str) -> str:
    if module.startswith("services."):
        parts = module.split(".")
        if len(parts) >= 3 and parts[1] in ("contracts", "domains", "application", "infrastructure", "adapters"):
            return ".".join(parts[:2])
        return "services"
    if module == "services":
        return "services"
    return module.split(".", maxsplit=1)[0]


def layer_of(pkg: str) -> str | None:
    if pkg in ("common", "components", "modules"):
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
        """把相对导入解析为绝对模块字符串（不查存在性）。相对导入相对“所在包 P”解析：level L → P 上溯 L-1 层。"""
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

    # 1. 未解析导入
    for u in unresolved:
        errors.append(f"[unresolved] {u}")

    # 2. 包级环（严格边）
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

    # 3/4. 层间白名单
    for (a, b), evs in sorted(edges.items()):
        la, lb = layer_of(pkg_of(a)), layer_of(pkg_of(b))
        if la is None or lb is None or a == b:
            continue
        where = f"{evs[0]}"
        if la in ("common", "components", "modules") and lb not in ("common", "components", "modules"):
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
            pa, pb = pkg_of(a), pkg_of(b)
            if la == "services.domains" and pa != pb:
                errors.append(f"[domain-isolation] {a} 跨业务域导入 {b}  [{where}]")
            elif la == "services.application" and pa != pb and (pa, pb) not in APPLICATION_FLOW_EDGES:
                errors.append(f"[app-flow] {pa} -> {pb} 未声明的应用流程依赖  [{where}]")

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
