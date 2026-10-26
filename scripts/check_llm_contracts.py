"""只读检查模型决策字段与实际输出 Schema；不启动服务、连接数据库或调用供应商。"""

import argparse
import ast
import importlib
import os
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CALLS = {"call_llm_json", "vision_chat_json"}
FINITE_PATTERN = re.compile(r"\^\(?(?:[\w -]+\|)+[\w -]+\)?\$")


def resolve(node: ast.expr, module: ModuleType) -> Any:
    if isinstance(node, ast.Name):
        return getattr(module, node.id)
    if isinstance(node, ast.Attribute):
        return getattr(resolve(node.value, module), node.attr)
    raise ValueError(f"无法解析契约来源：{ast.unparse(node)}")


def inspect_file(path: Path, backend: Path) -> tuple[int, list[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    aliases = {
        alias.asname or alias.name: alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name in CALLS
    }
    errors: list[str] = []
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        keywords = {key.arg: key.value for key in node.keywords}
        pattern = keywords.get("pattern", keywords.get("regex"))
        if (
            isinstance(pattern, ast.Constant)
            and isinstance(pattern.value, str)
            and FINITE_PATTERN.fullmatch(pattern.value)
        ):
            errors.append(f"{path}:{node.lineno}: 固定选项须声明为 Literal/枚举，不能使用正则 {pattern.value!r}")
        name = ast.unparse(node.func).rsplit(".", 1)[-1]
        if aliases.get(name, name) not in CALLS or "preserve_choices" not in keywords:
            continue
        try:
            field_node = keywords["preserve_choices"]
            if isinstance(field_node, ast.Name):
                # 唯一公共视觉包装器须原样转交选择、Schema 和解析器；其他动态入口不得漏检。
                owner = next(
                    (f for f in tree.body if isinstance(f, ast.AsyncFunctionDef) and f.name == "vision_chat_json"), None
                )
                if (
                    path == backend / "infrastructure/ai/prompt_engineer.py"
                    and owner is not None
                    and node in ast.walk(owner)
                ):
                    schema_assignments = [
                        n
                        for n in owner.body
                        if isinstance(n, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == "schema" for t in n.targets)
                    ]
                    if (
                        field_node.id == "preserve_choices"
                        and ast.unparse(keywords["schema"]) == "schema"
                        and ast.unparse(keywords["parse_output"]) == "response_model.model_validate"
                        and len(schema_assignments) == 1
                        and ast.unparse(schema_assignments[0].value) == "response_model.model_json_schema()"
                    ):
                        continue
                raise ValueError("动态 preserve_choices 未被公共包装契约覆盖")
            fields = ast.literal_eval(field_node)
            if not isinstance(fields, tuple) or any(not isinstance(field, str) for field in fields):
                raise ValueError("preserve_choices 必须是字段名元组")
            module_name = ".".join(path.relative_to(backend).with_suffix("").parts)
            module = importlib.import_module(module_name)
            if "response_model" in keywords:
                schema = resolve(keywords["response_model"], module).model_json_schema()
            else:
                expression = keywords["schema"]
                if not isinstance(expression, ast.Call) or expression.args or expression.keywords:
                    raise ValueError("无法解析 schema 构造器")
                schema = resolve(expression.func, module)()
            from infrastructure.ai.structured_contracts import validate_preserved_choices

            label = ast.literal_eval(keywords["schema_name"])
            validate_preserved_choices(schema, fields, schema_name=label)
            count += 1
        except Exception as exc:
            errors.append(f"{path}:{node.lineno}: {type(exc).__name__}: {exc}")
    # 手写工具 Schema 的固定选项也应使用 enum。
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "pattern"
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                    and FINITE_PATTERN.fullmatch(value.value)
                ):
                    errors.append(f"{path}:{node.lineno}: 固定选项 Schema 须使用 enum")
    return count, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", type=Path, default=ROOT / "backend")
    args = parser.parse_args()
    backend = args.backend.resolve()
    if not all(
        (backend / path).exists()
        for path in (
            "features",
            "workflows",
            "infrastructure/ai/prompt_engineer.py",
        )
    ):
        parser.error(f"不是有效的 Backend 源码目录：{backend}")
    sys.path.insert(0, str(backend))
    # 导入仅用于生成契约，禁止检查过程意外读取线上数据库或调用供应商。
    os.environ["DATABASE_URL"] = "postgresql+psycopg://contract:contract@127.0.0.1:1/contract"

    def deny_network(event: str, values: tuple[object, ...]) -> None:
        if event in {"socket.connect", "socket.getaddrinfo"}:
            raise RuntimeError("模型契约检查禁止网络访问")

    sys.addaudithook(deny_network)
    count = 0
    errors: list[str] = []
    roots = [
        backend / name for name in ("features", "workflows", "entrypoints", "infrastructure", "runtime", "bootstrap")
    ]
    roots.append(ROOT / "runner/spirit_agent_runner")
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            try:
                found, issues = inspect_file(path, backend)
                count += found
                errors.extend(issues)
            except (OSError, SyntaxError) as exc:
                errors.append(f"{path}: {exc}")
    if not count and not errors:
        errors.append("未找到可验证的决策保留调用，不能确认契约覆盖")
    for error in errors:
        print(error)
    print(f"LLM contracts: {count} decision-preserving call sites, {len(errors)} errors")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
