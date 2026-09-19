import copy
import json
from pathlib import Path
from typing import Any

import yaml


HTTP_METHODS = {
    "get",
    "put",
    "post",
    "delete",
    "options",
    "head",
    "patch",
    "trace",
}

SCHEMA_KEYS = {
    "type",
    "format",
    "nullable",
    "default",
    "enum",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "pattern",
    "minItems",
    "maxItems",
    "uniqueItems",
    "additionalProperties",
    "readOnly",
    "writeOnly",
    "discriminator",
    "description",
    "deprecated",
}


class OpenAPINormalizationError(ValueError):
    pass


def load_openapi_document(file_path: str | Path) -> dict[str, Any]:
    path = Path(file_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OpenAPINormalizationError(f"Unable to read API specification: {path}") from exc

    try:
        if path.suffix.lower() == ".json":
            data = json.loads(text)
        else:
            data = yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise OpenAPINormalizationError(f"Malformed API specification: {path}") from exc

    if not isinstance(data, dict):
        raise OpenAPINormalizationError("OpenAPI specification must be an object")

    return data


def validate_openapi_document(spec: dict[str, Any]) -> None:
    version = spec.get("openapi")
    if not isinstance(version, str):
        raise OpenAPINormalizationError("Missing required 'openapi' version")

    if not (version.startswith("3.0.") or version.startswith("3.1.")):
        raise OpenAPINormalizationError(f"Unsupported OpenAPI version: {version}")

    paths = spec.get("paths")
    if paths is None:
        raise OpenAPINormalizationError("Missing required 'paths' object")
    if not isinstance(paths, dict):
        raise OpenAPINormalizationError("'paths' must be an object")


def normalize_path(path: str) -> str:
    if path == "/":
        return path
    normalized = "/" + path.strip("/")
    return normalized.rstrip("/") or "/"


def resolve_ref(spec: dict[str, Any], ref: str, visited: set[str] | None = None) -> dict[str, Any]:
    visited = visited or set()
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise OpenAPINormalizationError(f"Unsupported reference: {ref}")
    if ref in visited:
        raise OpenAPINormalizationError(f"Circular reference detected: {ref}")

    current: Any = spec
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            raise OpenAPINormalizationError(f"Unresolved reference: {ref}")
        current = current[part]

    if not isinstance(current, dict):
        raise OpenAPINormalizationError(f"Reference does not resolve to an object: {ref}")

    if "$ref" in current:
        return resolve_ref(spec, current["$ref"], visited | {ref})

    resolved = copy.deepcopy(current)
    resolved["x-source-ref"] = ref
    return resolved


def _normalize_required(schema: dict[str, Any]) -> list[str]:
    required = schema.get("required", [])
    if not isinstance(required, list):
        return []
    return sorted(str(item) for item in required)


def normalize_schema(
    schema: dict[str, Any] | None,
    spec: dict[str, Any],
    visited_refs: set[str] | None = None,
) -> dict[str, Any]:
    if not schema:
        return {}
    if not isinstance(schema, dict):
        raise OpenAPINormalizationError("Schema must be an object")

    visited_refs = visited_refs or set()
    original_ref = schema.get("$ref")
    if original_ref:
        resolved = resolve_ref(spec, original_ref, visited_refs)
        merged = {**resolved, **{k: v for k, v in schema.items() if k != "$ref"}}
        normalized = normalize_schema(merged, spec, visited_refs | {original_ref})
        normalized.setdefault("x-source-ref", original_ref)
        return normalized

    normalized: dict[str, Any] = {}
    for key in sorted(SCHEMA_KEYS):
        if key in schema:
            normalized[key] = copy.deepcopy(schema[key])

    if "required" in schema:
        normalized["required"] = _normalize_required(schema)

    properties = schema.get("properties", {})
    if isinstance(properties, dict):
        normalized["properties"] = {
            name: normalize_schema(properties[name], spec, visited_refs)
            for name in sorted(properties)
        }

    if "items" in schema:
        normalized["items"] = normalize_schema(schema.get("items"), spec, visited_refs)

    for composition_key in ("allOf", "anyOf", "oneOf"):
        values = schema.get(composition_key)
        if isinstance(values, list):
            normalized[composition_key] = [
                normalize_schema(value, spec, visited_refs)
                for value in values
                if isinstance(value, dict)
            ]

    if "not" in schema and isinstance(schema["not"], dict):
        normalized["not"] = normalize_schema(schema["not"], spec, visited_refs)

    return normalized


def normalize_parameter(parameter: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in parameter:
        parameter = resolve_ref(spec, parameter["$ref"])
    if not isinstance(parameter, dict):
        raise OpenAPINormalizationError("Parameter must be an object")

    name = parameter.get("name")
    location = parameter.get("in")
    if not name or not location:
        raise OpenAPINormalizationError("Parameter requires 'name' and 'in'")

    required = parameter.get("required", False)
    if location == "path":
        required = True

    return {
        "name": str(name),
        "in": str(location),
        "required": bool(required),
        "schema": normalize_schema(parameter.get("schema", {}), spec),
        "description": parameter.get("description"),
        "deprecated": bool(parameter.get("deprecated", False)),
    }


def normalize_content(content: dict[str, Any] | None, spec: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(content, dict):
        return {}
    normalized: dict[str, Any] = {}
    for media_type in sorted(content):
        media = content.get(media_type) or {}
        normalized[media_type.lower()] = {
            "schema": normalize_schema(media.get("schema", {}), spec),
            "examples": copy.deepcopy(media.get("examples", {})),
        }
    return normalized


def normalize_request_body(request_body: dict[str, Any] | None, spec: dict[str, Any]) -> dict[str, Any]:
    if not request_body:
        return {}
    if "$ref" in request_body:
        request_body = resolve_ref(spec, request_body["$ref"])
    return {
        "required": bool(request_body.get("required", False)),
        "content": normalize_content(request_body.get("content", {}), spec),
        "description": request_body.get("description"),
    }


def normalize_response(response: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in response:
        response = resolve_ref(spec, response["$ref"])

    headers: dict[str, Any] = {}
    for name, header in sorted((response.get("headers") or {}).items()):
        if "$ref" in header:
            header = resolve_ref(spec, header["$ref"])
        headers[name.lower()] = {
            "name": name,
            "required": bool(header.get("required", False)),
            "schema": normalize_schema(header.get("schema", {}), spec),
            "description": header.get("description"),
        }

    return {
        "description": response.get("description"),
        "content": normalize_content(response.get("content", {}), spec),
        "headers": headers,
    }


def _parameter_key(parameter: dict[str, Any]) -> str:
    return f"{parameter['in']}:{parameter['name']}"


def normalize_operation(
    path: str,
    method: str,
    operation: dict[str, Any],
    spec: dict[str, Any],
    path_parameters: list[dict[str, Any]],
    inherited_security: list[dict[str, Any]],
) -> dict[str, Any]:
    operation_parameters = operation.get("parameters") or []
    merged_parameters: dict[str, dict[str, Any]] = {}
    for parameter in path_parameters + operation_parameters:
        normalized = normalize_parameter(parameter, spec)
        merged_parameters[_parameter_key(normalized)] = normalized

    responses: dict[str, Any] = {}
    for status, response in sorted((operation.get("responses") or {}).items()):
        responses[str(status)] = normalize_response(response or {}, spec)

    return {
        "path": path,
        "method": method.lower(),
        "endpoint": f"{method.upper()} {path}",
        "operationId": operation.get("operationId"),
        "summary": operation.get("summary"),
        "description": operation.get("description"),
        "tags": sorted(operation.get("tags") or []),
        "deprecated": bool(operation.get("deprecated", False)),
        "parameters": dict(sorted(merged_parameters.items())),
        "requestBody": normalize_request_body(operation.get("requestBody"), spec),
        "responses": responses,
        "security": operation.get("security", inherited_security),
        "servers": copy.deepcopy(operation.get("servers", [])),
        "callbacks": copy.deepcopy(operation.get("callbacks", {})),
    }


def normalize_openapi_spec(spec: dict[str, Any]) -> dict[str, Any]:
    validate_openapi_document(spec)

    components = spec.get("components") or {}
    schemas = components.get("schemas") or {}
    normalized_components = {
        "schemas": {
            name: normalize_schema(schema, spec)
            for name, schema in sorted(schemas.items())
        },
        "securitySchemes": copy.deepcopy(components.get("securitySchemes", {})),
    }

    normalized_paths: dict[str, Any] = {}
    global_security = copy.deepcopy(spec.get("security", []))
    for raw_path, path_item in sorted((spec.get("paths") or {}).items()):
        if not isinstance(path_item, dict):
            raise OpenAPINormalizationError(f"Path item must be an object: {raw_path}")
        path = normalize_path(raw_path)
        path_parameters = path_item.get("parameters") or []
        operations: dict[str, Any] = {}
        for method, operation in sorted(path_item.items()):
            method_lower = method.lower()
            if method_lower not in HTTP_METHODS:
                continue
            if not isinstance(operation, dict):
                raise OpenAPINormalizationError(f"Operation must be an object: {method} {path}")
            operations[method_lower] = normalize_operation(
                path,
                method_lower,
                operation,
                spec,
                path_parameters,
                global_security,
            )
        normalized_paths[path] = {"operations": operations}

    return {
        "openapi": spec["openapi"],
        "info": copy.deepcopy(spec.get("info", {})),
        "servers": copy.deepcopy(spec.get("servers", [])),
        "security": global_security,
        "components": normalized_components,
        "paths": normalized_paths,
    }
