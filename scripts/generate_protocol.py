"""从实际路由与消息模型生成跨端契约；--check 只检查是否与源码一致。"""

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "runner"))


def literal(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def ts_type(schema: dict[str, Any]) -> str:
    if "$ref" in schema:
        return f"ApiSchemas[{literal(schema['$ref'].rsplit('/', 1)[-1])}]"
    if "const" in schema:
        return literal(schema["const"])
    if "enum" in schema:
        return " | ".join(literal(value) for value in schema["enum"]) or "never"
    for key, operator in (("anyOf", " | "), ("oneOf", " | "), ("allOf", " & ")):
        if key in schema:
            return "(" + operator.join(ts_type(item) for item in schema[key]) + ")"
    kind = schema.get("type")
    if isinstance(kind, list):
        return " | ".join(ts_type(schema | {"type": value}) for value in kind)
    if kind in {"integer", "number"}:
        return "number"
    if kind == "boolean":
        return "boolean"
    if kind == "null":
        return "null"
    if kind == "string":
        return "Uint8Array" if schema.get("format") == "binary" else "string"
    if kind == "array":
        if "prefixItems" in schema:
            return "[" + ", ".join(ts_type(item) for item in schema["prefixItems"]) + "]"
        return f"Array<{ts_type(schema.get('items', {}))}>"
    if kind == "object" or "properties" in schema:
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        fields = [
            f"{literal(name)}{'' if name in required else '?'}: {ts_type(value)}" for name, value in properties.items()
        ]
        extra = schema.get("additionalProperties", not bool(properties))
        if extra:
            fields.append(f"[key: string]: {ts_type(extra) if isinstance(extra, dict) else 'unknown'}")
        return "{ " + "; ".join(fields) + " }" if fields else "Record<string, never>"
    return "unknown"


def add_model(schemas: dict[str, Any], model: type, namespace: str = "") -> str:
    schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
    definitions = schema.pop("$defs", {}) | {model.__name__: schema}

    def project(value: Any) -> Any:
        if isinstance(value, list):
            return [project(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: project(item) for key, item in value.items()}
        if "$ref" in result:
            name = result["$ref"].rsplit("/", 1)[-1]
            if name in definitions:
                result["$ref"] = "#/components/schemas/" + namespace + name
        return result

    for name, definition in definitions.items():
        schemas.setdefault(name, definition)
        if namespace:
            schemas[namespace + name] = project(definition)
    return f"ApiSchemas[{literal(namespace + model.__name__)}]"


def object_parameters(parameters: list[dict[str, Any]], location: str) -> str:
    properties = {item["name"]: item.get("schema", {}) for item in parameters if item.get("in") == location}
    required = [item["name"] for item in parameters if item.get("in") == location and item.get("required")]
    return ts_type({"type": "object", "properties": properties, "required": required, "additionalProperties": False})


def schema_refs(value: object) -> set[str]:
    if isinstance(value, list):
        return set().union(*(schema_refs(item) for item in value))
    if not isinstance(value, dict):
        return set()
    refs = {value["$ref"].rsplit("/", 1)[-1]} if "$ref" in value else set()
    return refs | set().union(*(schema_refs(item) for item in value.values()))


def response_schemas(document: dict, schemas: dict) -> set[str]:
    """HTTP 响应包含默认值；输入保留可省略性，exclude_if 继续按实际输出标为可选。"""
    from pydantic import BaseModel

    inputs: set[str] = set()
    outputs: set[str] = set()
    for item in document["paths"].values():
        for operation in item.values():
            if not isinstance(operation, dict):
                continue
            inputs |= schema_refs(operation.get("requestBody", {})) | schema_refs(operation.get("parameters", []))
            outputs |= schema_refs(operation.get("responses", {}))

    def closure(names: set[str]) -> set[str]:
        result = names.copy()
        while extra := set().union(*(schema_refs(schemas.get(name, {})) for name in result)) - result:
            result |= extra
        return result

    inputs, outputs = closure(inputs), closure(outputs)
    shared = inputs & outputs
    originals = schemas.copy()
    for name in shared:
        schemas[name + "Input"] = input_schema(originals[name], shared)
    for name in inputs - outputs:
        schemas[name] = input_schema(originals[name], shared)
    omitted: dict[str, set[str]] = {}
    pending = list(BaseModel.__subclasses__())
    seen: set[type] = set()
    while pending:
        model = pending.pop()
        if model in seen:
            continue
        seen.add(model)
        pending.extend(model.__subclasses__())
        omitted.setdefault(model.__name__, set()).update(
            field.serialization_alias or field.alias or name
            for name, field in model.model_fields.items()
            if field.exclude or field.exclude_if is not None
        )
    for name in outputs:
        schema = schemas.get(name, {})
        if "properties" in schema:
            schema["required"] = [
                key for key in schema["properties"] if key not in omitted.get(schema.get("title", name), set())
            ]
    return shared


def input_schema(value: object, shared: set[str]) -> Any:
    if isinstance(value, list):
        return [input_schema(item, shared) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: input_schema(item, shared) for key, item in value.items()}
    if "$ref" in result and result["$ref"].rsplit("/", 1)[-1] in shared:
        result["$ref"] += "Input"
    return result


def generate() -> str:
    from bootstrap.app import app
    from entrypoints.desktop.contracts import RPC_CONTRACTS
    from entrypoints.desktop.event_contracts import EVENT_CONTRACTS
    from infrastructure.api_errors import ApiErrorResponse
    from spirit_agent_runner.contracts.rpc import RUNNER_CONTRACTS, RUNNER_EVENTS

    document = app.openapi()
    schemas = copy.deepcopy(document.get("components", {}).get("schemas", {}))
    add_model(schemas, ApiErrorResponse)
    shared = response_schemas(document, schemas)
    operations = []
    policies = []
    for path, item in document["paths"].items():
        for method, operation in item.items():
            if method not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                continue
            parameters = input_schema(item.get("parameters", []) + operation.get("parameters", []), shared)
            request_content = operation.get("requestBody", {}).get("content", {})
            request_schema = next(iter(request_content.values()), {}).get("schema")
            body = ts_type(input_schema(request_schema, shared)) if request_schema else "undefined"
            if "multipart/form-data" in request_content:
                body = "FormData"
            responses = operation.get("responses", {})
            success = next((value for code, value in responses.items() if code.startswith("2")), {})
            content = success.get("content", {})
            response_schema = next(iter(content.values()), {}).get("schema")
            response = ts_type(response_schema) if response_schema is not None else "undefined"
            response_kind = "json" if "application/json" in content else "binary"
            if not content and operation.get("responses", {}).get("204") is not None:
                response_kind = "empty"
            key = method.upper() + " " + path
            operations.append(
                f"  {literal(key)}: {{ path: {object_parameters(parameters, 'path')}; "
                f"query: {object_parameters(parameters, 'query')}; body: {body}; response: {response} }}",
            )
            policy = operation.get(
                "x-spiritagent",
                {"identities": ["public"], "upload": "json", "wait_for_model": False},
            )
            policies.append({"key": key, "path": path, "method": method.upper(), "response": response_kind, **policy})
    rpc_operations = []
    rpc_policies = {}
    for name, contract in sorted(RPC_CONTRACTS.items()):
        request = add_model(schemas, contract.request, "Rpc")
        response = add_model(schemas, contract.response, "Rpc")
        rpc_operations.append(f"  {literal(name)}: {{ request: {request}; response: {response} }}")
        rpc_policies[name] = {
            "remote": contract.remote,
            "concurrent": contract.concurrent,
            "wait_for_model": contract.wait_for_model,
        }
    events = [
        f"  {literal(name)}: {add_model(schemas, model, 'Rpc')}" for name, model in sorted(EVENT_CONTRACTS.items())
    ]
    runner_operations = [
        f"  {literal(name)}: {{ request: {add_model(schemas, contract.request, 'Runner')}; response: {add_model(schemas, contract.response, 'Runner')} }}"
        for name, contract in sorted(RUNNER_CONTRACTS.items())
    ]
    runner_events = [
        f"  {literal(name)}: {add_model(schemas, model, 'Runner')}" for name, model in sorted(RUNNER_EVENTS.items())
    ]
    lines = ["// 由 scripts/generate_protocol.py 从服务端公开契约生成。", "", "export interface ApiSchemas {"]
    lines.extend(f"  {literal(name)}: {ts_type(value)}" for name, value in sorted(schemas.items()))
    lines.extend(
        [
            "}",
            "",
            "export interface ApiOperations {",
            *operations,
            "}",
            "",
            "export interface RpcOperations {",
            *rpc_operations,
            "}",
            "",
            "export interface GatewayEventPayloads {",
            *events,
            "}",
            "",
            "export interface RunnerOperations {",
            *runner_operations,
            "}",
            "",
            "export interface RunnerEventPayloads {",
            *runner_events,
            "}",
            "",
        ],
    )
    lines.append("export const API_OPERATIONS = " + json.dumps(policies, ensure_ascii=False, indent=2) + " as const")
    lines.append("export const RPC_POLICIES = " + json.dumps(rpc_policies, ensure_ascii=False, indent=2) + " as const")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = ROOT / "shared" / "api.generated.ts"
    result = generate()
    if args.check:
        if not target.exists() or target.read_text(encoding="utf-8") != result:
            raise SystemExit("跨端契约已变化，请运行 scripts/generate_protocol.py")
        print("protocol contracts match")
    else:
        target.write_text(result, encoding="utf-8", newline="\n")
        print(f"generated {target.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
