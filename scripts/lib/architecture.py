"""Backend 与 Runner 架构门禁共用的依赖环检测。"""


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
