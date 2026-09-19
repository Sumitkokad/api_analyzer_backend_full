from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml
from yaml.events import (
    AliasEvent,
    MappingEndEvent,
    MappingStartEvent,
    SequenceEndEvent,
    SequenceStartEvent,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

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

DEFAULT_MAX_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_DEPTH = 80
DEFAULT_MAX_NODES = 50_000
DEFAULT_MAX_YAML_ALIASES = 256
DEFAULT_MAX_YAML_ANCHORS = 256

# JSON Schema / OpenAPI schema vocabulary that we understand.
SUPPORTED_SCHEMA_KEYS = {
    "type",
    "format",
    "nullable",
    "default",
    "const",
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
    "minProperties",
    "maxProperties",
    "properties",
    "patternProperties",
    "propertyNames",
    "required",
    "items",
    "prefixItems",
    "contains",
    "minContains",
    "maxContains",
    "allOf",
    "anyOf",
    "oneOf",
    "not",
    "if",
    "then",
    "else",
    "dependentRequired",
    "dependentSchemas",
    "additionalProperties",
    "unevaluatedProperties",
    "unevaluatedItems",
    "contentMediaType",
    "contentEncoding",
    "contentSchema",
    "readOnly",
    "writeOnly",
    "deprecated",
    "discriminator",
    "title",
    "description",
}

# Examples are deliberately excluded from the normalized contract because
# they are explanatory payload and can create large, irrelevant diffs.
IGNORED_SCHEMA_KEYS = {
    "examples",
}

INTERNAL_NORMALIZATION_KEYS = {
    "x-source-ref",
    "x-recursive-ref",
}

SECURITY_FUNCTIONAL_KEYS = {
    "type",
    "scheme",
    "bearerFormat",
    "in",
    "name",
    "flows",
    "openIdConnectUrl",
}

SECURITY_DESCRIPTION_KEY = "description"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class OpenAPINormalizationError(ValueError):
    """
    Controlled error raised during ingestion/normalization.

    `code` can be used later by the CI API/gate to produce structured
    reason codes without parsing human-readable exception text.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "SCHEMA_NORMALIZATION_ERROR",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# Normalization report
# ---------------------------------------------------------------------------

@dataclass
class NormalizationReport:
    """
    Counters consumed later by the contract-quality stage.
    """

    dropped_keywords: int = 0
    ignored_keywords: dict[str, int] = field(default_factory=dict)

    ignored_links: int = 0
    ignored_examples: int = 0

    unresolved_refs: int = 0
    recursive_refs: int = 0

    operations_count: int = 0
    operations_without_responses: int = 0

    empty_schemas: int = 0
    untyped_schemas: int = 0

    def increment_ignored_keyword(self, keyword: str) -> None:
        self.dropped_keywords += 1
        self.ignored_keywords[keyword] = (
            self.ignored_keywords.get(keyword, 0) + 1
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Internal context
# ---------------------------------------------------------------------------

@dataclass
class _NormalizationContext:
    spec: dict[str, Any]

    max_depth: int = DEFAULT_MAX_DEPTH
    max_nodes: int = DEFAULT_MAX_NODES

    report: NormalizationReport = field(
        default_factory=NormalizationReport
    )

    node_count: int = 0

    # Fully normalized component schemas.
    memo: dict[str, dict[str, Any]] = field(
        default_factory=dict
    )

    # References currently being expanded.
    resolving_refs: set[str] = field(
        default_factory=set
    )

    # Object IDs visited while checking YAML/Python data structure.
    validation_seen: set[int] = field(
        default_factory=set
    )


# ---------------------------------------------------------------------------
# Generic deterministic helpers
# ---------------------------------------------------------------------------

def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _stable_sort_key(value: Any) -> str:
    return _canonical_json(value)


def _increment_node(
    ctx: _NormalizationContext,
    *,
    depth: int,
) -> None:
    if depth > ctx.max_depth:
        raise OpenAPINormalizationError(
            f"Maximum normalization depth exceeded: {ctx.max_depth}",
            code="SCHEMA_DEPTH_LIMIT",
            details={
                "max_depth": ctx.max_depth,
                "depth": depth,
            },
        )

    ctx.node_count += 1

    if ctx.node_count > ctx.max_nodes:
        raise OpenAPINormalizationError(
            f"Maximum normalization node count exceeded: {ctx.max_nodes}",
            code="SCHEMA_NODE_LIMIT",
            details={
                "max_nodes": ctx.max_nodes,
            },
        )


def _deepcopy(value: Any) -> Any:
    return copy.deepcopy(value)


# ---------------------------------------------------------------------------
# YAML safety
# ---------------------------------------------------------------------------

def _preflight_yaml(
    text: str,
    *,
    max_depth: int,
    max_nodes: int,
    max_aliases: int,
    max_anchors: int,
) -> None:
    """
    Scan YAML parser events before constructing the Python object.

    SafeLoader blocks arbitrary Python object construction, but aliases can
    still create unexpectedly large/recursive structures. This preflight
    puts explicit bounds around that surface.
    """
    depth = 0
    observed_depth = 0
    node_count = 0
    alias_count = 0
    anchor_count = 0

    try:
        events = yaml.parse(
            text,
            Loader=yaml.SafeLoader,
        )

        for event in events:
            if isinstance(event, AliasEvent):
                alias_count += 1

                if alias_count > max_aliases:
                    raise OpenAPINormalizationError(
                        "YAML alias limit exceeded.",
                        code="YAML_ALIAS_LIMIT",
                        details={
                            "max_aliases": max_aliases,
                            "aliases": alias_count,
                        },
                    )

            anchor = getattr(event, "anchor", None)

            if anchor:
                anchor_count += 1

                if anchor_count > max_anchors:
                    raise OpenAPINormalizationError(
                        "YAML anchor limit exceeded.",
                        code="YAML_ANCHOR_LIMIT",
                        details={
                            "max_anchors": max_anchors,
                            "anchors": anchor_count,
                        },
                    )

            if isinstance(
                event,
                (MappingStartEvent, SequenceStartEvent),
            ):
                depth += 1
                observed_depth = max(observed_depth, depth)
                node_count += 1

                if depth > max_depth:
                    raise OpenAPINormalizationError(
                        "Maximum YAML nesting depth exceeded.",
                        code="YAML_DEPTH_LIMIT",
                        details={
                            "max_depth": max_depth,
                            "depth": depth,
                        },
                    )

            elif isinstance(
                event,
                (MappingEndEvent, SequenceEndEvent),
            ):
                depth -= 1

            else:
                # Scalar nodes count toward the total node budget.
                node_count += 1

            if node_count > max_nodes:
                raise OpenAPINormalizationError(
                    "Maximum YAML node count exceeded.",
                    code="YAML_NODE_LIMIT",
                    details={
                        "max_nodes": max_nodes,
                        "nodes": node_count,
                    },
                )

    except OpenAPINormalizationError:
        raise
    except yaml.YAMLError as exc:
        raise OpenAPINormalizationError(
            "Malformed YAML document.",
            code="MALFORMED_YAML",
        ) from exc


def _validate_python_tree(
    value: Any,
    *,
    max_depth: int,
    max_nodes: int,
) -> None:
    """
    Validate the materialized Python object after parsing.

    YAML aliases may produce shared/cyclic Python objects, so recursive
    references are detected before normalization.
    """
    seen: set[int] = set()
    active: set[int] = set()
    nodes = 0

    def visit(current: Any, depth: int) -> None:
        nonlocal nodes

        if depth > max_depth:
            raise OpenAPINormalizationError(
                "Maximum object nesting depth exceeded.",
                code="SCHEMA_DEPTH_LIMIT",
                details={
                    "max_depth": max_depth,
                    "depth": depth,
                },
            )

        if isinstance(current, (dict, list)):
            object_id = id(current)

            if object_id in active:
                raise OpenAPINormalizationError(
                    "Cyclic YAML alias structure detected.",
                    code="YAML_CYCLIC_ALIAS",
                )

            if object_id in seen:
                return

            seen.add(object_id)
            active.add(object_id)

            nodes += 1

            if nodes > max_nodes:
                raise OpenAPINormalizationError(
                    "Maximum object node count exceeded.",
                    code="SCHEMA_NODE_LIMIT",
                    details={
                        "max_nodes": max_nodes,
                        "nodes": nodes,
                    },
                )

            if isinstance(current, dict):
                for key, child in current.items():
                    # OpenAPI object keys should be scalar strings.
                    if not isinstance(key, str):
                        raise OpenAPINormalizationError(
                            "OpenAPI object keys must be strings.",
                            code="INVALID_OBJECT_KEY",
                        )
                    visit(child, depth + 1)

            else:
                for child in current:
                    visit(child, depth + 1)

            active.remove(object_id)

    visit(value, 0)


# ---------------------------------------------------------------------------
# OpenAPI ingestion
# ---------------------------------------------------------------------------

def _parse_openapi_text(
    text: str,
    *,
    filename: str | None = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
    max_aliases: int = DEFAULT_MAX_YAML_ALIASES,
    max_anchors: int = DEFAULT_MAX_YAML_ANCHORS,
) -> dict[str, Any]:
    if not isinstance(text, str):
        raise OpenAPINormalizationError(
            "Specification payload must be text.",
            code="INVALID_SPEC_PAYLOAD",
        )

    if not text.strip():
        raise OpenAPINormalizationError(
            "Specification is empty.",
            code="EMPTY_SPECIFICATION",
        )

    # JSON first, regardless of filename.
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Only if JSON fails do we inspect it as YAML.
        _preflight_yaml(
            text,
            max_depth=max_depth,
            max_nodes=max_nodes,
            max_aliases=max_aliases,
            max_anchors=max_anchors,
        )

        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            source = f" {filename}" if filename else ""

            raise OpenAPINormalizationError(
                f"Malformed JSON/YAML specification{source}.",
                code="MALFORMED_SPECIFICATION",
            ) from exc

    if not isinstance(data, dict):
        raise OpenAPINormalizationError(
            "OpenAPI specification must be a JSON/YAML object.",
            code="INVALID_SPEC_ROOT",
        )

    _validate_python_tree(
        data,
        max_depth=max_depth,
        max_nodes=max_nodes,
    )

    return data


def load_openapi_from_bytes(
    data: bytes,
    *,
    filename: str | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
    max_aliases: int = DEFAULT_MAX_YAML_ALIASES,
    max_anchors: int = DEFAULT_MAX_YAML_ANCHORS,
) -> dict[str, Any]:
    """
    Primary ingestion entry point for future CI uploads.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise OpenAPINormalizationError(
            "Specification input must be bytes.",
            code="INVALID_SPEC_PAYLOAD",
        )

    if len(data) > max_bytes:
        raise OpenAPINormalizationError(
            f"Specification exceeds maximum size of {max_bytes} bytes.",
            code="SCHEMA_TOO_LARGE",
            details={
                "max_bytes": max_bytes,
                "actual_bytes": len(data),
            },
        )

    try:
        text = bytes(data).decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise OpenAPINormalizationError(
            "Specification must be valid UTF-8.",
            code="INVALID_UTF8",
        ) from exc

    return _parse_openapi_text(
        text,
        filename=filename,
        max_depth=max_depth,
        max_nodes=max_nodes,
        max_aliases=max_aliases,
        max_anchors=max_anchors,
    )


def load_openapi_from_dict(
    spec: dict[str, Any],
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> dict[str, Any]:
    """
    Primary ingestion entry point for already-parsed CI/manual requests.
    """
    if not isinstance(spec, dict):
        raise OpenAPINormalizationError(
            "OpenAPI specification must be an object.",
            code="INVALID_SPEC_ROOT",
        )

    copied = copy.deepcopy(spec)

    _validate_python_tree(
        copied,
        max_depth=max_depth,
        max_nodes=max_nodes,
    )

    return copied


def load_openapi_document(
    file_path: str | Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
    max_aliases: int = DEFAULT_MAX_YAML_ALIASES,
    max_anchors: int = DEFAULT_MAX_YAML_ANCHORS,
) -> dict[str, Any]:
    """
    Backwards-compatible file-path API.

    Existing manual uploads/scripts can continue calling this function.
    """
    path = Path(file_path)

    try:
        size = path.stat().st_size
    except OSError as exc:
        raise OpenAPINormalizationError(
            f"Unable to inspect API specification: {path}",
            code="SPEC_READ_ERROR",
        ) from exc

    if size > max_bytes:
        raise OpenAPINormalizationError(
            f"Specification exceeds maximum size of {max_bytes} bytes.",
            code="SCHEMA_TOO_LARGE",
            details={
                "max_bytes": max_bytes,
                "actual_bytes": size,
                "path": str(path),
            },
        )

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise OpenAPINormalizationError(
            f"Unable to read API specification: {path}",
            code="SPEC_READ_ERROR",
        ) from exc

    return load_openapi_from_bytes(
        raw,
        filename=path.name,
        max_bytes=max_bytes,
        max_depth=max_depth,
        max_nodes=max_nodes,
        max_aliases=max_aliases,
        max_anchors=max_anchors,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_openapi_document(
    spec: dict[str, Any],
) -> None:
    if not isinstance(spec, dict):
        raise OpenAPINormalizationError(
            "OpenAPI specification must be an object.",
            code="INVALID_SPEC_ROOT",
        )

    version = spec.get("openapi")

    if not isinstance(version, str):
        raise OpenAPINormalizationError(
            "Missing required 'openapi' version.",
            code="SPEC_VERSION_MISSING",
        )

    if not (
        version.startswith("3.0.")
        or version.startswith("3.1.")
    ):
        raise OpenAPINormalizationError(
            f"Unsupported OpenAPI version: {version}",
            code="SPEC_VERSION_UNSUPPORTED",
            details={"openapi": version},
        )

    paths = spec.get("paths")

    if paths is None:
        raise OpenAPINormalizationError(
            "Missing required 'paths' object.",
            code="PATHS_MISSING",
        )

    if not isinstance(paths, dict):
        raise OpenAPINormalizationError(
            "'paths' must be an object.",
            code="INVALID_PATHS",
        )


# ---------------------------------------------------------------------------
# Path normalization
# ---------------------------------------------------------------------------

def normalize_path(path: str) -> str:
    """
    Preserve the actual path, including trailing slash.

    D8 requires that '/users/' and '/users' remain distinguishable.
    Canonical matching is provided separately by canonical_path_key().
    """
    if not isinstance(path, str) or not path:
        raise OpenAPINormalizationError(
            "API path must be a non-empty string.",
            code="INVALID_PATH",
        )

    if not path.startswith("/"):
        raise OpenAPINormalizationError(
            f"OpenAPI path must start with '/': {path}",
            code="INVALID_PATH",
        )

    if path == "/":
        return "/"

    # Preserve trailing slash and parameter spelling.
    return path


def canonical_path_key(path: str) -> str:
    """
    Canonical route identity used later by api_diff.py.

    /users/{id}
    /users/{pk}

    become:

    /users/{}
    /users/{}

    Trailing slash is ignored for matching here, while normalize_path()
    preserves it so the diff layer can report a slash change.
    """
    path = normalize_path(path)

    if path != "/":
        path = path.rstrip("/")

    segments = path.split("/")

    canonical_segments: list[str] = []

    for segment in segments:
        if (
            segment.startswith("{")
            and segment.endswith("}")
        ):
            canonical_segments.append("{}")
        else:
            canonical_segments.append(segment)

    return "/".join(canonical_segments) or "/"


def path_parameter_names(path: str) -> list[str]:
    names: list[str] = []

    for segment in normalize_path(path).split("/"):
        if (
            segment.startswith("{")
            and segment.endswith("}")
        ):
            names.append(segment[1:-1])

    return names


def path_has_trailing_slash(path: str) -> bool:
    return path != "/" and path.endswith("/")


# ---------------------------------------------------------------------------
# $ref resolution
# ---------------------------------------------------------------------------

def _resolve_pointer(
    spec: dict[str, Any],
    ref: str,
) -> Any:
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise OpenAPINormalizationError(
            f"Unsupported reference: {ref}",
            code="UNSUPPORTED_REF",
            details={"ref": ref},
        )

    current: Any = spec

    for raw_part in ref[2:].split("/"):
        part = (
            raw_part
            .replace("~1", "/")
            .replace("~0", "~")
        )

        if (
            not isinstance(current, dict)
            or part not in current
        ):
            raise OpenAPINormalizationError(
                f"Unresolved reference: {ref}",
                code="UNRESOLVED_REF",
                details={"ref": ref},
            )

        current = current[part]

    return current


def resolve_ref(
    spec: dict[str, Any],
    ref: str,
    visited: set[str] | None = None,
) -> dict[str, Any]:
    """
    Backwards-compatible public resolver.

    Recursive references are returned as placeholders instead of raising a
    circular-reference error.
    """
    visited = set(visited or set())

    current_ref = ref
    chain: set[str] = set(visited)

    while True:
        if current_ref in chain:
            return {
                "x-recursive-ref": current_ref,
            }

        chain.add(current_ref)

        target = _resolve_pointer(
            spec,
            current_ref,
        )

        if not isinstance(target, dict):
            raise OpenAPINormalizationError(
                f"Reference does not resolve to an object: {current_ref}",
                code="INVALID_REF_TARGET",
                details={"ref": current_ref},
            )

        nested_ref = target.get("$ref")

        if not nested_ref:
            result = copy.deepcopy(target)
            result["x-source-ref"] = current_ref
            return result

        current_ref = nested_ref


# ---------------------------------------------------------------------------
# Security normalization
# ---------------------------------------------------------------------------

def _canonicalize_security(
    security: Any,
) -> list[dict[str, list[str]]]:
    if security is None:
        return []

    if not isinstance(security, list):
        return []

    normalized: list[dict[str, list[str]]] = []

    for requirement in security:
        if not isinstance(requirement, dict):
            continue

        normalized_requirement: dict[str, list[str]] = {}

        for scheme_name, scopes in requirement.items():
            if isinstance(scopes, list):
                normalized_scopes = sorted(
                    str(scope)
                    for scope in scopes
                )
            else:
                normalized_scopes = []

            normalized_requirement[str(scheme_name)] = normalized_scopes

        normalized.append(normalized_requirement)

    return sorted(
        normalized,
        key=_stable_sort_key,
    )


def _normalize_security_schemes(
    schemes: Any,
) -> dict[str, Any]:
    if not isinstance(schemes, dict):
        return {}

    normalized: dict[str, Any] = {}

    for name, scheme in sorted(schemes.items()):
        if not isinstance(scheme, dict):
            continue

        # D5: descriptions do not affect functional compatibility.
        normalized_scheme = {
            key: copy.deepcopy(value)
            for key, value in scheme.items()
            if key in SECURITY_FUNCTIONAL_KEYS
        }

        normalized[str(name)] = normalized_scheme

    return normalized


# ---------------------------------------------------------------------------
# Schema normalization
# ---------------------------------------------------------------------------

def _normalize_type_keyword(
    schema: dict[str, Any],
) -> tuple[Any, bool]:
    """
    Canonicalize OpenAPI 3.1:

        type: ["string", "null"]

    into:

        type: "string"
        nullable: true

    Multiple non-null types are retained as a sorted list.
    """
    raw_type = schema.get("type")
    explicit_nullable = bool(
        schema.get("nullable", False)
    )

    if not isinstance(raw_type, list):
        return raw_type, explicit_nullable

    types = sorted(
        {
            str(item)
            for item in raw_type
            if item != "null"
        }
    )

    has_null = "null" in raw_type

    nullable = explicit_nullable or has_null

    if len(types) == 1:
        canonical_type: Any = types[0]
    else:
        canonical_type = types

    return canonical_type, nullable


def _canonicalize_required(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    return sorted(
        str(item)
        for item in value
    )


def _merge_allof(
    normalized: dict[str, Any],
) -> dict[str, Any]:
    """
    Best-effort allOf flattening.

    When branches have conflicting semantic values, retain allOf and add
    x-conflict rather than inventing a merged meaning.
    """
    branches = normalized.get("allOf")

    if not isinstance(branches, list) or not branches:
        return normalized

    if not all(
        isinstance(branch, dict)
        for branch in branches
    ):
        normalized["allOf"] = sorted(
            branches,
            key=_stable_sort_key,
        )
        return normalized

    merged = {
        key: copy.deepcopy(value)
        for key, value in normalized.items()
        if key != "allOf"
    }

    merged_properties: dict[str, Any] = {}

    existing_required = set(
        normalized.get("required") or []
    )

    conflicts: list[str] = []

    merge_scalar_keys = {
        "type",
        "format",
        "nullable",
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
        "minProperties",
        "maxProperties",
        "additionalProperties",
    }

    for branch_index, branch in enumerate(branches):
        properties = branch.get("properties")

        if isinstance(properties, dict):
            for name, value in properties.items():
                if name in merged_properties:
                    if merged_properties[name] != value:
                        conflicts.append(
                            f"properties.{name}"
                        )
                else:
                    merged_properties[name] = copy.deepcopy(value)

        required = branch.get("required")

        if isinstance(required, list):
            existing_required.update(
                str(item)
                for item in required
            )

        for key in merge_scalar_keys:
            if key not in branch:
                continue

            if key not in merged:
                merged[key] = copy.deepcopy(
                    branch[key]
                )
            elif merged[key] != branch[key]:
                conflicts.append(key)

        # Unsupported nested composition means we cannot safely flatten it.
        for key in (
            "anyOf",
            "oneOf",
            "not",
            "if",
            "then",
            "else",
        ):
            if key in branch:
                conflicts.append(
                    f"composition:{key}"
                )

    if conflicts:
        normalized["allOf"] = sorted(
            branches,
            key=_stable_sort_key,
        )
        normalized["x-conflict"] = sorted(
            set(conflicts)
        )
        return normalized

    if merged_properties:
        merged["properties"] = {
            name: merged_properties[name]
            for name in sorted(merged_properties)
        }

    if existing_required:
        merged["required"] = sorted(
            existing_required
        )

    merged.pop("allOf", None)

    return merged


def _normalize_schema_internal(
    schema: Any,
    *,
    ctx: _NormalizationContext,
    depth: int,
) -> dict[str, Any]:
    _increment_node(
        ctx,
        depth=depth,
    )

    if schema is None:
        ctx.report.empty_schemas += 1
        return {}

    if not isinstance(schema, dict):
        raise OpenAPINormalizationError(
            "Schema must be an object.",
            code="INVALID_SCHEMA",
        )

    # ---------------------------------------------------------------
    # $ref
    # ---------------------------------------------------------------

    ref = schema.get("$ref")

    if ref:
        if not isinstance(ref, str):
            raise OpenAPINormalizationError(
                "$ref must be a string.",
                code="INVALID_REF",
            )

        if ref in ctx.resolving_refs:
            ctx.report.recursive_refs += 1

            return {
                "x-recursive-ref": ref,
            }

        memoized = ctx.memo.get(ref)

        if memoized is not None:
            return memoized

        try:
            target = _resolve_pointer(
                ctx.spec,
                ref,
            )
        except OpenAPINormalizationError:
            ctx.report.unresolved_refs += 1

            return {
                "x-unresolved-ref": ref,
            }

        if not isinstance(target, dict):
            raise OpenAPINormalizationError(
                f"Reference does not resolve to an object: {ref}",
                code="INVALID_REF_TARGET",
            )

        ctx.resolving_refs.add(ref)

        try:
            normalized_target = _normalize_schema_internal(
                target,
                ctx=ctx,
                depth=depth + 1,
            )
        finally:
            ctx.resolving_refs.discard(ref)

        # Don't let normalization metadata become semantic noise.
        normalized_target = copy.deepcopy(
            normalized_target
        )

        normalized_target["x-source-ref"] = ref

        ctx.memo[ref] = normalized_target

        # OpenAPI 3.1 permits sibling properties next to $ref.
        siblings = {
            key: value
            for key, value in schema.items()
            if key != "$ref"
        }

        if siblings:
            normalized_siblings = _normalize_schema_internal(
                siblings,
                ctx=ctx,
                depth=depth + 1,
            )

            merged = {
                **normalized_target,
                **normalized_siblings,
            }

            merged["x-source-ref"] = ref

            return _merge_allof(merged)

        return normalized_target

    # ---------------------------------------------------------------
    # Normal object
    # ---------------------------------------------------------------

    normalized: dict[str, Any] = {}

    # Unknown keys are counted, not silently forgotten.
    for key in schema:
        if (
            key not in SUPPORTED_SCHEMA_KEYS
            and key not in IGNORED_SCHEMA_KEYS
            and key not in INTERNAL_NORMALIZATION_KEYS
            and not str(key).startswith("x-")
        ):
            ctx.report.increment_ignored_keyword(
                str(key)
            )

    # Examples intentionally dropped.
    if "examples" in schema:
        ctx.report.ignored_examples += 1
        ctx.report.increment_ignored_keyword(
            "examples"
        )

    # Vendor extensions are preserved except internal fields.
    for key in sorted(schema):
        if (
            str(key).startswith("x-")
            and key not in INTERNAL_NORMALIZATION_KEYS
        ):
            normalized[key] = copy.deepcopy(
                schema[key]
            )

    # ---------------------------------------------------------------
    # Type / nullable
    # ---------------------------------------------------------------

    canonical_type, nullable = _normalize_type_keyword(
        schema
    )

    if canonical_type is not None:
        normalized["type"] = canonical_type

    if nullable:
        normalized["nullable"] = True
    elif "nullable" in schema:
        normalized["nullable"] = False

    # ---------------------------------------------------------------
    # Scalar keywords
    # ---------------------------------------------------------------

    scalar_keys = {
        "format",
        "default",
        "const",
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
        "minProperties",
        "maxProperties",
        "contentMediaType",
        "contentEncoding",
        "readOnly",
        "writeOnly",
        "deprecated",
        "discriminator",
        "title",
        "description",
    }

    for key in sorted(scalar_keys):
        if key in schema:
            normalized[key] = copy.deepcopy(
                schema[key]
            )

    # Enum ordering is not semantic.
    if "enum" in schema:
        enum = schema.get("enum")

        if isinstance(enum, list):
            normalized["enum"] = sorted(
                copy.deepcopy(enum),
                key=_stable_sort_key,
            )
        else:
            normalized["enum"] = copy.deepcopy(
                enum
            )

    # ---------------------------------------------------------------
    # Required
    # ---------------------------------------------------------------

    if "required" in schema:
        normalized["required"] = _canonicalize_required(
            schema.get("required")
        )

    # ---------------------------------------------------------------
    # Properties
    # ---------------------------------------------------------------

    properties = schema.get("properties")

    if properties is not None:
        if not isinstance(properties, dict):
            raise OpenAPINormalizationError(
                "'properties' must be an object.",
                code="INVALID_SCHEMA_PROPERTIES",
            )

        normalized["properties"] = {
            str(name): _normalize_schema_internal(
                property_schema,
                ctx=ctx,
                depth=depth + 1,
            )
            for name, property_schema in sorted(
                properties.items(),
                key=lambda item: str(item[0]),
            )
        }

    # ---------------------------------------------------------------
    # Pattern properties
    # ---------------------------------------------------------------

    pattern_properties = schema.get(
        "patternProperties"
    )

    if pattern_properties is not None:
        if not isinstance(pattern_properties, dict):
            raise OpenAPINormalizationError(
                "'patternProperties' must be an object.",
                code="INVALID_PATTERN_PROPERTIES",
            )

        normalized["patternProperties"] = {
            str(pattern): _normalize_schema_internal(
                value,
                ctx=ctx,
                depth=depth + 1,
            )
            for pattern, value in sorted(
                pattern_properties.items(),
                key=lambda item: str(item[0]),
            )
        }

    # ---------------------------------------------------------------
    # additionalProperties
    # ---------------------------------------------------------------

    if "additionalProperties" in schema:
        additional = schema.get(
            "additionalProperties"
        )

        if isinstance(additional, dict):
            normalized["additionalProperties"] = (
                _normalize_schema_internal(
                    additional,
                    ctx=ctx,
                    depth=depth + 1,
                )
            )
        else:
            normalized["additionalProperties"] = bool(
                additional
            )

    # ---------------------------------------------------------------
    # Other schema-valued keywords
    # ---------------------------------------------------------------

    schema_value_keys = (
        "items",
        "contains",
        "propertyNames",
        "not",
        "if",
        "then",
        "else",
        "unevaluatedProperties",
        "unevaluatedItems",
        "contentSchema",
    )

    for key in schema_value_keys:
        if key not in schema:
            continue

        value = schema.get(key)

        if isinstance(value, dict):
            normalized[key] = _normalize_schema_internal(
                value,
                ctx=ctx,
                depth=depth + 1,
            )

    # ---------------------------------------------------------------
    # Array schema
    # ---------------------------------------------------------------

    prefix_items = schema.get("prefixItems")

    if prefix_items is not None:
        if not isinstance(prefix_items, list):
            raise OpenAPINormalizationError(
                "'prefixItems' must be an array.",
                code="INVALID_PREFIX_ITEMS",
            )

        normalized["prefixItems"] = [
            _normalize_schema_internal(
                item,
                ctx=ctx,
                depth=depth + 1,
            )
            for item in prefix_items
        ]

    # ---------------------------------------------------------------
    # Dependent schemas
    # ---------------------------------------------------------------

    dependent_schemas = schema.get(
        "dependentSchemas"
    )

    if dependent_schemas is not None:
        if not isinstance(dependent_schemas, dict):
            raise OpenAPINormalizationError(
                "'dependentSchemas' must be an object.",
                code="INVALID_DEPENDENT_SCHEMAS",
            )

        normalized["dependentSchemas"] = {
            str(key): _normalize_schema_internal(
                value,
                ctx=ctx,
                depth=depth + 1,
            )
            for key, value in sorted(
                dependent_schemas.items(),
                key=lambda item: str(item[0]),
            )
        }

    # ---------------------------------------------------------------
    # dependentRequired
    # ---------------------------------------------------------------

    dependent_required = schema.get(
        "dependentRequired"
    )

    if dependent_required is not None:
        if not isinstance(dependent_required, dict):
            raise OpenAPINormalizationError(
                "'dependentRequired' must be an object.",
                code="INVALID_DEPENDENT_REQUIRED",
            )

        normalized["dependentRequired"] = {
            str(key): sorted(
                str(item)
                for item in values
            )
            for key, values in sorted(
                dependent_required.items(),
                key=lambda item: str(item[0]),
            )
            if isinstance(values, list)
        }

    # ---------------------------------------------------------------
    # Composition
    # ---------------------------------------------------------------

    for composition_key in (
        "allOf",
        "anyOf",
        "oneOf",
    ):
        values = schema.get(
            composition_key
        )

        if values is None:
            continue

        if not isinstance(values, list):
            raise OpenAPINormalizationError(
                f"'{composition_key}' must be an array.",
                code="INVALID_SCHEMA_COMPOSITION",
            )

        normalized_values = [
            _normalize_schema_internal(
                value,
                ctx=ctx,
                depth=depth + 1,
            )
            for value in values
            if isinstance(value, dict)
        ]

        # Order is canonical for comparison purposes.
        normalized[composition_key] = sorted(
            normalized_values,
            key=_stable_sort_key,
        )

    # Best-effort allOf flattening.
    if "allOf" in normalized:
        normalized = _merge_allof(
            normalized
        )

    # Empty / untyped schema quality counters.
    if not normalized:
        ctx.report.empty_schemas += 1

    meaningful_keys = set(normalized)

    if (
        meaningful_keys <= {
            "description",
            "title",
            "deprecated",
            "readOnly",
            "writeOnly",
            "x-source-ref",
            "x-recursive-ref",
        }
        or (
            normalized.get("type") == "object"
            and not normalized.get("properties")
            and normalized.get("additionalProperties")
            is True
        )
    ):
        ctx.report.untyped_schemas += 1

    return normalized


def normalize_schema(
    schema: dict[str, Any] | None,
    spec: dict[str, Any],
    visited_refs: set[str] | None = None,
) -> dict[str, Any]:
    """
    Backwards-compatible public schema normalizer.

    The third positional argument is retained for existing callers.
    """
    if not isinstance(spec, dict):
        raise OpenAPINormalizationError(
            "OpenAPI document must be an object.",
            code="INVALID_SPEC_ROOT",
        )

    ctx = _NormalizationContext(
        spec=spec,
    )

    if visited_refs:
        ctx.resolving_refs.update(
            visited_refs
        )

    return _normalize_schema_internal(
        schema or {},
        ctx=ctx,
        depth=0,
    )


# ---------------------------------------------------------------------------
# Parameter normalization
# ---------------------------------------------------------------------------

def _normalize_parameter_content(
    content: Any,
    spec: dict[str, Any],
    ctx: _NormalizationContext,
    depth: int,
) -> dict[str, Any]:
    if not isinstance(content, dict):
        return {}

    return _normalize_content_internal(
        content,
        spec,
        ctx=ctx,
        depth=depth,
    )


def normalize_parameter(
    parameter: dict[str, Any],
    spec: dict[str, Any],
) -> dict[str, Any]:
    ctx = _NormalizationContext(
        spec=spec,
    )

    return _normalize_parameter_internal(
        parameter,
        spec,
        ctx=ctx,
        depth=0,
    )


def _normalize_parameter_internal(
    parameter: dict[str, Any],
    spec: dict[str, Any],
    *,
    ctx: _NormalizationContext,
    depth: int,
) -> dict[str, Any]:
    _increment_node(
        ctx,
        depth=depth,
    )

    if not isinstance(parameter, dict):
        raise OpenAPINormalizationError(
            "Parameter must be an object.",
            code="INVALID_PARAMETER",
        )

    if "$ref" in parameter:
        ref = parameter.get("$ref")

        try:
            parameter = resolve_ref(
                spec,
                ref,
            )
        except OpenAPINormalizationError:
            ctx.report.unresolved_refs += 1
            raise

    name = parameter.get("name")
    location = parameter.get("in")

    if not name or not location:
        raise OpenAPINormalizationError(
            "Parameter requires 'name' and 'in'.",
            code="INVALID_PARAMETER",
        )

    required = bool(
        parameter.get(
            "required",
            False,
        )
    )

    # Path parameters are always required by OpenAPI.
    if location == "path":
        required = True

    result: dict[str, Any] = {
        "name": str(name),
        "in": str(location),
        "required": required,
        "description": parameter.get("description"),
        "deprecated": bool(
            parameter.get(
                "deprecated",
                False,
            )
        ),
        "style": parameter.get("style"),
        "explode": parameter.get("explode"),
        "allowReserved": parameter.get("allowReserved"),
    }

    if "schema" in parameter:
        result["schema"] = _normalize_schema_internal(
            parameter.get("schema"),
            ctx=ctx,
            depth=depth + 1,
        )

    # Parameter objects may use content instead of schema.
    if "content" in parameter:
        result["content"] = _normalize_parameter_content(
            parameter.get("content"),
            spec,
            ctx,
            depth + 1,
        )

    return result


# ---------------------------------------------------------------------------
# Media type/content normalization
# ---------------------------------------------------------------------------

def _split_media_type(media_type: str) -> tuple[str, dict[str, str]]:
    parts = [
        part.strip()
        for part in media_type.split(";")
    ]

    base = parts[0].lower()

    parameters: dict[str, str] = {}

    for part in parts[1:]:
        if "=" not in part:
            continue

        key, value = part.split(
            "=",
            1,
        )

        parameters[key.strip().lower()] = (
            value.strip()
        )

    return base, parameters


def _normalize_content_internal(
    content: dict[str, Any],
    spec: dict[str, Any],
    *,
    ctx: _NormalizationContext,
    depth: int,
) -> dict[str, Any]:
    if not isinstance(content, dict):
        return {}

    normalized: dict[str, Any] = {}

    for raw_media_type in sorted(
        content,
        key=str,
    ):
        media = content.get(
            raw_media_type
        ) or {}

        if not isinstance(media, dict):
            continue

        media_type, parameters = _split_media_type(
            str(raw_media_type)
        )

        item: dict[str, Any] = {}

        if "schema" in media:
            item["schema"] = _normalize_schema_internal(
                media.get("schema"),
                ctx=ctx,
                depth=depth + 1,
            )

        # We intentionally do not preserve examples.
        if media.get("examples"):
            ctx.report.ignored_examples += 1
            ctx.report.increment_ignored_keyword(
                "examples"
            )

        # Media-type parameters are kept separately so the semantic media
        # type can still be compared as application/json vs application/json.
        if parameters:
            item["media_type_parameters"] = parameters

        normalized[media_type] = item

    return normalized


def normalize_content(
    content: dict[str, Any] | None,
    spec: dict[str, Any],
) -> dict[str, Any]:
    ctx = _NormalizationContext(
        spec=spec,
    )

    return _normalize_content_internal(
        content or {},
        spec,
        ctx=ctx,
        depth=0,
    )


# ---------------------------------------------------------------------------
# Request body
# ---------------------------------------------------------------------------

def _normalize_request_body_internal(
    request_body: dict[str, Any] | None,
    spec: dict[str, Any],
    *,
    ctx: _NormalizationContext,
    depth: int,
) -> dict[str, Any]:
    if not request_body:
        return {}

    _increment_node(
        ctx,
        depth=depth,
    )

    if not isinstance(request_body, dict):
        raise OpenAPINormalizationError(
            "Request body must be an object.",
            code="INVALID_REQUEST_BODY",
        )

    if "$ref" in request_body:
        try:
            request_body = resolve_ref(
                spec,
                request_body["$ref"],
            )
        except OpenAPINormalizationError:
            ctx.report.unresolved_refs += 1
            raise

    return {
        "required": bool(
            request_body.get(
                "required",
                False,
            )
        ),
        "content": _normalize_content_internal(
            request_body.get(
                "content",
                {},
            ),
            spec,
            ctx=ctx,
            depth=depth + 1,
        ),
        "description": request_body.get(
            "description"
        ),
    }


def normalize_request_body(
    request_body: dict[str, Any] | None,
    spec: dict[str, Any],
) -> dict[str, Any]:
    ctx = _NormalizationContext(
        spec=spec,
    )

    return _normalize_request_body_internal(
        request_body,
        spec,
        ctx=ctx,
        depth=0,
    )


# ---------------------------------------------------------------------------
# Response normalization
# ---------------------------------------------------------------------------

def _normalize_response_internal(
    response: dict[str, Any],
    spec: dict[str, Any],
    *,
    ctx: _NormalizationContext,
    depth: int,
) -> dict[str, Any]:
    _increment_node(
        ctx,
        depth=depth,
    )

    if not isinstance(response, dict):
        raise OpenAPINormalizationError(
            "Response must be an object.",
            code="INVALID_RESPONSE",
        )

    if "$ref" in response:
        try:
            response = resolve_ref(
                spec,
                response["$ref"],
            )
        except OpenAPINormalizationError:
            ctx.report.unresolved_refs += 1
            raise

    headers: dict[str, Any] = {}

    for name, header in sorted(
        (response.get("headers") or {}).items(),
        key=lambda item: str(item[0]).lower(),
    ):
        if not isinstance(header, dict):
            continue

        if "$ref" in header:
            try:
                header = resolve_ref(
                    spec,
                    header["$ref"],
                )
            except OpenAPINormalizationError:
                ctx.report.unresolved_refs += 1
                raise

        headers[str(name).lower()] = {
            "name": str(name),
            "required": bool(
                header.get(
                    "required",
                    False,
                )
            ),
            "schema": _normalize_schema_internal(
                header.get(
                    "schema",
                    {},
                ),
                ctx=ctx,
                depth=depth + 1,
            ),
            "description": header.get(
                "description"
            ),
        }

    # Links are not yet analyzed by the diff engine.
    links = response.get("links")

    if links:
        if isinstance(links, dict):
            ctx.report.ignored_links += len(
                links
            )
        else:
            ctx.report.ignored_links += 1

    return {
        "description": response.get(
            "description"
        ),
        "content": _normalize_content_internal(
            response.get(
                "content",
                {},
            ),
            spec,
            ctx=ctx,
            depth=depth + 1,
        ),
        "headers": headers,
    }


def normalize_response(
    response: dict[str, Any],
    spec: dict[str, Any],
) -> dict[str, Any]:
    ctx = _NormalizationContext(
        spec=spec,
    )

    return _normalize_response_internal(
        response,
        spec,
        ctx=ctx,
        depth=0,
    )


# ---------------------------------------------------------------------------
# Operation normalization
# ---------------------------------------------------------------------------

def _parameter_key(
    parameter: dict[str, Any],
) -> str:
    return (
        f"{parameter['in']}:"
        f"{parameter['name']}"
    )


def _normalize_security(
    security: Any,
) -> list[dict[str, list[str]]]:
    return _canonicalize_security(
        security
    )


def _normalize_operation_internal(
    path: str,
    method: str,
    operation: dict[str, Any],
    spec: dict[str, Any],
    path_parameters: list[dict[str, Any]],
    inherited_security: list[dict[str, Any]],
    *,
    ctx: _NormalizationContext,
    depth: int,
) -> dict[str, Any]:
    _increment_node(
        ctx,
        depth=depth,
    )

    if not isinstance(operation, dict):
        raise OpenAPINormalizationError(
            f"Operation must be an object: {method} {path}",
            code="INVALID_OPERATION",
        )

    operation_parameters = (
        operation.get("parameters") or []
    )

    merged_parameters: dict[str, dict[str, Any]] = {}

    for parameter in (
        path_parameters
        + operation_parameters
    ):
        normalized = _normalize_parameter_internal(
            parameter,
            spec,
            ctx=ctx,
            depth=depth + 1,
        )

        merged_parameters[
            _parameter_key(normalized)
        ] = normalized

    responses_raw = (
        operation.get("responses")
        or {}
    )

    responses: dict[str, Any] = {}

    for status, response in sorted(
        responses_raw.items(),
        key=lambda item: str(item[0]),
    ):
        responses[str(status)] = (
            _normalize_response_internal(
                response or {},
                spec,
                ctx=ctx,
                depth=depth + 1,
            )
        )

    ctx.report.operations_count += 1

    if not responses:
        ctx.report.operations_without_responses += 1

    explicit_security = (
        "security" in operation
    )

    if explicit_security:
        effective_security = _normalize_security(
            operation.get("security")
        )
    else:
        effective_security = _normalize_security(
            inherited_security
        )

    return {
        "path": path,
        "method": method.lower(),
        "endpoint": (
            f"{method.upper()} {path}"
        ),
        "operationId": operation.get(
            "operationId"
        ),
        "summary": operation.get(
            "summary"
        ),
        "description": operation.get(
            "description"
        ),
        "tags": sorted(
            operation.get("tags") or []
        ),
        "deprecated": bool(
            operation.get(
                "deprecated",
                False,
            )
        ),
        "parameters": dict(
            sorted(
                merged_parameters.items()
            )
        ),
        "requestBody": (
            _normalize_request_body_internal(
                operation.get("requestBody"),
                spec,
                ctx=ctx,
                depth=depth + 1,
            )
        ),
        "responses": responses,
        "security": effective_security,
        "x-security-explicit": explicit_security,
        "servers": copy.deepcopy(
            operation.get(
                "servers",
                [],
            )
        ),
        "callbacks": copy.deepcopy(
            operation.get(
                "callbacks",
                {},
            )
        ),
    }


def normalize_operation(
    path: str,
    method: str,
    operation: dict[str, Any],
    spec: dict[str, Any],
    path_parameters: list[dict[str, Any]],
    inherited_security: list[dict[str, Any]],
) -> dict[str, Any]:
    ctx = _NormalizationContext(
        spec=spec,
    )

    return _normalize_operation_internal(
        path,
        method,
        operation,
        spec,
        path_parameters,
        inherited_security,
        ctx=ctx,
        depth=0,
    )


# ---------------------------------------------------------------------------
# Full document normalization
# ---------------------------------------------------------------------------

def normalize_openapi_spec_with_report(
    spec: dict[str, Any],
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> tuple[dict[str, Any], NormalizationReport]:
    """
    Main normalization implementation.

    Returns:
        (normalized_spec, normalization_report)
    """
    validate_openapi_document(
        spec
    )

    ctx = _NormalizationContext(
        spec=spec,
        max_depth=max_depth,
        max_nodes=max_nodes,
    )

    components = (
        spec.get("components")
        or {}
    )

    schemas = (
        components.get("schemas")
        or {}
    )

    if not isinstance(schemas, dict):
        raise OpenAPINormalizationError(
            "'components.schemas' must be an object.",
            code="INVALID_COMPONENT_SCHEMAS",
        )

    normalized_schemas: dict[str, Any] = {}

    # Normalize named components in deterministic order.
    for name, raw_schema in sorted(
        schemas.items(),
        key=lambda item: str(item[0]),
    ):
        ref = (
            f"#/components/schemas/{name}"
        )

        # Mark the component as resolving before recursively processing it.
        ctx.resolving_refs.add(ref)

        try:
            normalized_schema = (
                _normalize_schema_internal(
                    raw_schema,
                    ctx=ctx,
                    depth=0,
                )
            )
        finally:
            ctx.resolving_refs.discard(ref)

        # Store the finished component in memo so references reuse it.
        ctx.memo[ref] = normalized_schema

        normalized_schemas[str(name)] = (
            normalized_schema
        )

    normalized_components = {
        "schemas": normalized_schemas,
        "securitySchemes": (
            _normalize_security_schemes(
                components.get(
                    "securitySchemes",
                    {},
                )
            )
        ),
    }

    # -----------------------------------------------------------------------
    # Global security
    # -----------------------------------------------------------------------

    global_security = _normalize_security(
        spec.get("security", [])
    )

    # -----------------------------------------------------------------------
    # Paths
    # -----------------------------------------------------------------------

    normalized_paths: dict[str, Any] = {}

    raw_paths = (
        spec.get("paths")
        or {}
    )

    for raw_path, path_item in sorted(
        raw_paths.items(),
        key=lambda item: str(item[0]),
    ):
        if not isinstance(path_item, dict):
            raise OpenAPINormalizationError(
                f"Path item must be an object: {raw_path}",
                code="INVALID_PATH_ITEM",
            )

        path = normalize_path(
            str(raw_path)
        )

        canonical_path = (
            canonical_path_key(path)
        )

        path_parameters = (
            path_item.get(
                "parameters"
            )
            or []
        )

        operations: dict[str, Any] = {}

        for method, operation in sorted(
            path_item.items(),
            key=lambda item: str(item[0]).lower(),
        ):
            method_lower = str(
                method
            ).lower()

            if method_lower not in HTTP_METHODS:
                continue

            operations[method_lower] = (
                _normalize_operation_internal(
                    path,
                    method_lower,
                    operation,
                    spec,
                    path_parameters,
                    global_security,
                    ctx=ctx,
                    depth=1,
                )
            )

        normalized_paths[path] = {
            # Metadata consumed later by api_diff.py.
            "x-original-path": path,
            "x-canonical-path": canonical_path,
            "x-has-trailing-slash": (
                path_has_trailing_slash(path)
            ),
            "operations": operations,
        }

    normalized = {
        "openapi": spec["openapi"],
        "info": copy.deepcopy(
            spec.get(
                "info",
                {},
            )
        ),
        "servers": copy.deepcopy(
            spec.get(
                "servers",
                [],
            )
        ),
        "security": global_security,
        "components": normalized_components,
        "paths": normalized_paths,
    }

    return (
        normalized,
        ctx.report,
    )


def normalize_openapi_spec(
    spec: dict[str, Any],
) -> dict[str, Any]:
    """
    Backwards-compatible public API.

    Existing callers still receive only the normalized spec.
    """
    normalized, _report = (
        normalize_openapi_spec_with_report(
            spec
        )
    )

    return normalized