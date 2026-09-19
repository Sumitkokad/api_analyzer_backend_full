from __future__ import annotations

import hashlib
import json
import math
from typing import Any


# ---------------------------------------------------------------------------
# Schema keywords
# ---------------------------------------------------------------------------

CONSTRAINT_KEYS = (
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
)

SCALAR_SCHEMA_KEYS = (
    "type",
    "format",
    "nullable",
    "default",
    "const",
    "readOnly",
    "writeOnly",
    "deprecated",
    "discriminator",
    "title",
    "description",
    "examples",
    "contentMediaType",
    "contentEncoding",
)

SCHEMA_VALUE_KEYS = (
    "additionalProperties",
    "not",
    "contains",
    "propertyNames",
    "if",
    "then",
    "else",
    "unevaluatedProperties",
    "unevaluatedItems",
    "contentSchema",
)

SCHEMA_MAP_KEYS = (
    "patternProperties",
    "dependentSchemas",
)

SCHEMA_LIST_KEYS = (
    "allOf",
    "anyOf",
    "oneOf",
)

LIST_VALUE_KEYS = (
    "required",
    "dependentRequired",
)

# These are added by normalization and are not contract semantics.
IGNORED_COMPARISON_KEYS = {
    "x-source-ref",
    "x-recursive-ref",
}


# ---------------------------------------------------------------------------
# Basic helpers
# ---------------------------------------------------------------------------

def _prefix(direction: str) -> str | None:
    """
    Never silently treat unknown direction as request.
    """
    if direction == "request":
        return "request"
    if direction == "response":
        return "response"
    return None


def _field_name(schema_path: str) -> str:
    return schema_path[2:] if schema_path.startswith("$.") else schema_path


def _prefix_for_path(schema_path: str, name: str) -> str:
    if schema_path == "$":
        return f"$.{name}"
    return f"{schema_path}.{name}"


def _strip_ignored(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_ignored(item)
            for key, item in value.items()
            if key not in IGNORED_COMPARISON_KEYS
        }

    if isinstance(value, list):
        return [_strip_ignored(item) for item in value]

    return value


def _stable_json(value: Any) -> str:
    return json.dumps(
        _strip_ignored(value),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(
        _stable_json(value).encode("utf-8")
    ).hexdigest()


def _canonicalize_composition(value: Any) -> Any:
    """
    Canonicalize composition ordering.

    Full semantic allOf flattening belongs in normalization. This layer
    ensures that equivalent branch ordering does not create a false diff.
    """
    if isinstance(value, dict):
        result = {
            key: _canonicalize_composition(item)
            for key, item in value.items()
            if key not in IGNORED_COMPARISON_KEYS
        }

        for key in SCHEMA_LIST_KEYS:
            branches = result.get(key)
            if isinstance(branches, list):
                result[key] = sorted(branches, key=_stable_hash)

        return result

    if isinstance(value, list):
        return [_canonicalize_composition(item) for item in value]

    return value


def _canonical(value: Any) -> Any:
    return _canonicalize_composition(value)


def _values_equal(old_value: Any, new_value: Any) -> bool:
    return _canonical(old_value) == _canonical(new_value)


def _safe_gt(left: Any, right: Any) -> bool:
    try:
        return left > right
    except (TypeError, ValueError):
        return False


def _safe_lt(left: Any, right: Any) -> bool:
    try:
        return left < right
    except (TypeError, ValueError):
        return False


def _is_positive_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value > 0
    )


def _is_near_integer(value: float) -> bool:
    return math.isclose(
        value,
        round(value),
        rel_tol=1e-9,
        abs_tol=1e-9,
    )


# ---------------------------------------------------------------------------
# Relation inference
# ---------------------------------------------------------------------------

def _type_relation(old_value: Any, new_value: Any) -> str | None:
    def as_set(value: Any) -> set[str] | None:
        if isinstance(value, str):
            return {value}

        if isinstance(value, list) and all(
            isinstance(item, str) for item in value
        ):
            return set(value)

        return None

    old_set = as_set(old_value)
    new_set = as_set(new_value)

    if old_set is None or new_set is None:
        return None

    if old_set == new_set:
        return "changed"

    if old_set < new_set:
        return "widened"

    if new_set < old_set:
        return "narrowed"

    if old_set == {"integer"} and new_set == {"number"}:
        return "widened"

    return None


def _multiple_of_relation(
    old_value: Any,
    new_value: Any,
) -> str | None:
    if not _is_positive_number(old_value):
        return None

    if not _is_positive_number(new_value):
        return None

    old_value = float(old_value)
    new_value = float(new_value)

    if math.isclose(old_value, new_value):
        return "changed"

    new_over_old = new_value / old_value
    old_over_new = old_value / new_value

    if _is_near_integer(new_over_old):
        return "tightened"

    if _is_near_integer(old_over_new):
        return "relaxed"

    return None


def _make_relation(
    keyword: str,
    old_value: Any,
    new_value: Any,
) -> str:
    """
    Relation vocabulary consumed by breaking_change_rules.py.

    Possible relations:
        added
        removed
        tightened
        relaxed
        widened
        narrowed
        changed
    """
    if old_value is None and new_value is not None:
        return "added"

    if old_value is not None and new_value is None:
        return "removed"

    if _values_equal(old_value, new_value):
        return "changed"

    if isinstance(old_value, bool) and isinstance(new_value, bool):
        if old_value is False and new_value is True:
            return "tightened"

        if old_value is True and new_value is False:
            return "relaxed"

    if keyword in {
        "minimum",
        "exclusiveMinimum",
        "minLength",
        "minItems",
        "minProperties",
    }:
        if _safe_gt(new_value, old_value):
            return "tightened"

        if _safe_lt(new_value, old_value):
            return "relaxed"

    if keyword in {
        "maximum",
        "exclusiveMaximum",
        "maxLength",
        "maxItems",
        "maxProperties",
    }:
        if _safe_lt(new_value, old_value):
            return "tightened"

        if _safe_gt(new_value, old_value):
            return "relaxed"

    if keyword == "multipleOf":
        relation = _multiple_of_relation(
            old_value,
            new_value,
        )

        if relation:
            return relation

    if keyword == "type":
        relation = _type_relation(
            old_value,
            new_value,
        )

        if relation:
            return relation

    if keyword == "nullable":
        if old_value is False and new_value is True:
            return "relaxed"

        if old_value is True and new_value is False:
            return "tightened"

    return "changed"


# ---------------------------------------------------------------------------
# Change creation
# ---------------------------------------------------------------------------

def make_change(
    change_type: str,
    endpoint: str,
    *,
    method: str | None = None,
    direction: str = "unknown",
    location: str | None = None,
    parameter: str | None = None,
    schema_path: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    category: str = "contract",
    confidence: float = 1.0,
    source: dict[str, Any] | None = None,
    keyword: str | None = None,
    relation: str | None = None,
) -> dict[str, Any]:
    """
    Creates a semantic change.

    New fields:
        keyword
        relation
    """
    result = {
        "change_type": change_type,
        "category": category,
        "endpoint": endpoint,
        "method": method,
        "direction": direction,
        "location": location,
        "parameter": parameter,
        "schema_path": schema_path,
        "old_value": _strip_ignored(old_value),
        "new_value": _strip_ignored(new_value),
        "keyword": keyword,
        "relation": relation,
        "confidence": confidence,
        "source": source or {},
    }

    # Preserve compatibility with the current rules implementation.
    if change_type == "schema_constraint_changed":
        old_payload = result["old_value"]
        new_payload = result["new_value"]

        if not isinstance(old_payload, dict) or "constraint" not in old_payload:
            old_payload = {
                "constraint": keyword,
                "value": result["old_value"],
            }

        if not isinstance(new_payload, dict) or "constraint" not in new_payload:
            new_payload = {
                "constraint": keyword,
                "value": result["new_value"],
            }

        if relation:
            new_payload["relation"] = relation

        result["old_value"] = old_payload
        result["new_value"] = new_payload

    return result


# ---------------------------------------------------------------------------
# Enum comparison
# ---------------------------------------------------------------------------

def compare_enums(
    old_schema: dict[str, Any],
    new_schema: dict[str, Any],
    endpoint: str,
    *,
    method: str | None,
    direction: str,
    location: str,
    schema_path: str,
) -> list[dict[str, Any]]:
    old_enum = old_schema.get("enum")
    new_enum = new_schema.get("enum")

    if _values_equal(old_enum, new_enum):
        return []

    if isinstance(old_enum, list) and isinstance(new_enum, list):
        # Enum values do not have to be hashable. Stable JSON representation
        # works for strings, numbers, objects, arrays, etc.
        old_map = {
            _stable_json(value): value
            for value in old_enum
        }

        new_map = {
            _stable_json(value): value
            for value in new_enum
        }

        removed_keys = sorted(
            set(old_map) - set(new_map)
        )

        added_keys = sorted(
            set(new_map) - set(old_map)
        )

        changes: list[dict[str, Any]] = []

        if removed_keys:
            removed = [
                old_map[key]
                for key in removed_keys
            ]

            changes.append(
                make_change(
                    "enum_values_removed",
                    endpoint,
                    method=method,
                    direction=direction,
                    location=location,
                    parameter=_field_name(schema_path),
                    schema_path=schema_path,
                    old_value=removed,
                    new_value=new_enum,
                    keyword="enum",
                    relation="removed",
                )
            )

        if added_keys:
            added = [
                new_map[key]
                for key in added_keys
            ]

            changes.append(
                make_change(
                    "enum_values_added",
                    endpoint,
                    method=method,
                    direction=direction,
                    location=location,
                    parameter=_field_name(schema_path),
                    schema_path=schema_path,
                    old_value=old_enum,
                    new_value=added,
                    keyword="enum",
                    relation="added",
                )
            )

        return changes

    return [
        make_change(
            "enum_changed",
            endpoint,
            method=method,
            direction=direction,
            location=location,
            parameter=_field_name(schema_path),
            schema_path=schema_path,
            old_value=old_enum,
            new_value=new_enum,
            keyword="enum",
            relation="changed",
        )
    ]


# ---------------------------------------------------------------------------
# Scalar / constraint comparisons
# ---------------------------------------------------------------------------

def _compare_scalar_keyword(
    old_schema: dict[str, Any],
    new_schema: dict[str, Any],
    key: str,
    endpoint: str,
    *,
    method: str | None,
    direction: str,
    location: str,
    schema_path: str,
) -> list[dict[str, Any]]:
    old_value = old_schema.get(key)
    new_value = new_schema.get(key)

    if _values_equal(old_value, new_value):
        return []

    relation = _make_relation(
        key,
        old_value,
        new_value,
    )

    change_type_map = {
        "type": "schema_type_changed",
        "format": "schema_format_changed",
        "nullable": "schema_nullable_changed",
        "default": "schema_default_changed",
        "readOnly": "schema_readOnly_changed",
        "writeOnly": "schema_writeOnly_changed",
        "discriminator": "schema_discriminator_changed",
        "title": "schema_title_changed",
        "description": "schema_description_changed",
        "examples": "schema_examples_changed",
    }

    change_type = change_type_map.get(
        key,
        f"schema_{key}_changed",
    )

    return [
        make_change(
            change_type,
            endpoint,
            method=method,
            direction=direction,
            location=location,
            parameter=_field_name(schema_path),
            schema_path=schema_path,
            old_value=old_value,
            new_value=new_value,
            keyword=key,
            relation=relation,
        )
    ]


def _compare_constraint_keyword(
    old_schema: dict[str, Any],
    new_schema: dict[str, Any],
    key: str,
    endpoint: str,
    *,
    method: str | None,
    direction: str,
    location: str,
    schema_path: str,
) -> list[dict[str, Any]]:
    old_value = old_schema.get(key)
    new_value = new_schema.get(key)

    if _values_equal(old_value, new_value):
        return []

    relation = _make_relation(
        key,
        old_value,
        new_value,
    )

    return [
        make_change(
            "schema_constraint_changed",
            endpoint,
            method=method,
            direction=direction,
            location=location,
            parameter=_field_name(schema_path),
            schema_path=schema_path,
            old_value={
                "constraint": key,
                "value": old_value,
            },
            new_value={
                "constraint": key,
                "value": new_value,
                "relation": relation,
            },
            keyword=key,
            relation=relation,
        )
    ]


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------

def _compare_composition_keyword(
    old_schema: dict[str, Any],
    new_schema: dict[str, Any],
    key: str,
    endpoint: str,
    *,
    method: str | None,
    direction: str,
    location: str,
    schema_path: str,
) -> list[dict[str, Any]]:
    old_value = _canonical(
        old_schema.get(key)
    )

    new_value = _canonical(
        new_schema.get(key)
    )

    if old_value == new_value:
        return []

    relation = _make_relation(
        key,
        old_value,
        new_value,
    )

    return [
        make_change(
            "schema_composition_changed",
            endpoint,
            method=method,
            direction=direction,
            location=location,
            parameter=_field_name(schema_path),
            schema_path=schema_path,
            old_value={key: old_value},
            new_value={key: new_value},
            keyword=key,
            relation=relation,
        )
    ]


# ---------------------------------------------------------------------------
# Generic schema-valued keywords
# ---------------------------------------------------------------------------

def _compare_generic_schema_value(
    old_schema: dict[str, Any],
    new_schema: dict[str, Any],
    key: str,
    endpoint: str,
    *,
    method: str | None,
    direction: str,
    location: str,
    schema_path: str,
) -> list[dict[str, Any]]:
    old_value = _canonical(
        old_schema.get(key)
    )

    new_value = _canonical(
        new_schema.get(key)
    )

    if old_value == new_value:
        return []

    relation = _make_relation(
        key,
        old_value,
        new_value,
    )

    return [
        make_change(
            f"schema_{key}_changed",
            endpoint,
            method=method,
            direction=direction,
            location=location,
            parameter=_field_name(schema_path),
            schema_path=schema_path,
            old_value=old_value,
            new_value=new_value,
            keyword=key,
            relation=relation,
        )
    ]


def _compare_schema_map(
    old_schema: dict[str, Any],
    new_schema: dict[str, Any],
    key: str,
    endpoint: str,
    *,
    method: str | None,
    direction: str,
    location: str,
    schema_path: str,
) -> list[dict[str, Any]]:
    old_value = old_schema.get(key) or {}
    new_value = new_schema.get(key) or {}

    if _values_equal(old_value, new_value):
        return []

    return [
        make_change(
            f"schema_{key}_changed",
            endpoint,
            method=method,
            direction=direction,
            location=location,
            parameter=_field_name(schema_path),
            schema_path=schema_path,
            old_value=_canonical(old_value),
            new_value=_canonical(new_value),
            keyword=key,
            relation="changed",
        )
    ]


# ---------------------------------------------------------------------------
# Main schema comparison
# ---------------------------------------------------------------------------

def compare_schema(
    old_schema: dict[str, Any] | None,
    new_schema: dict[str, Any] | None,
    endpoint: str,
    *,
    method: str | None,
    direction: str,
    location: str,
    schema_path: str = "$",
    visited: set[tuple[int, int, str]] | None = None,
) -> list[dict[str, Any]]:
    """
    Compare two schemas.

    Every emitted schema-level change contains:
        keyword
        relation
        direction

    Existing callers can continue using the same function signature.
    """
    old_schema = old_schema or {}
    new_schema = new_schema or {}

    if not isinstance(old_schema, dict) or not isinstance(new_schema, dict):
        if _values_equal(old_schema, new_schema):
            return []

        return [
            make_change(
                "schema_changed",
                endpoint,
                method=method,
                direction=direction,
                location=location,
                parameter=_field_name(schema_path),
                schema_path=schema_path,
                old_value=old_schema,
                new_value=new_schema,
                keyword="schema",
                relation=_make_relation(
                    "schema",
                    old_schema,
                    new_schema,
                ),
            )
        ]

    visited = visited if visited is not None else set()

    # Perform recursion detection using the original object identities.
    marker = (
        id(old_schema),
        id(new_schema),
        schema_path,
    )

    if marker in visited:
        return []

    visited.add(marker)

    # Ignore normalization-only metadata while preserving the original
    # identity used by the recursion guard above.
    old_schema = {
        key: value
        for key, value in old_schema.items()
        if key not in IGNORED_COMPARISON_KEYS
    }

    new_schema = {
        key: value
        for key, value in new_schema.items()
        if key not in IGNORED_COMPARISON_KEYS
    }

    changes: list[dict[str, Any]] = []
    field = _field_name(schema_path)

    # -----------------------------------------------------------------------
    # Scalar keywords
    # -----------------------------------------------------------------------
    for key in SCALAR_SCHEMA_KEYS:
        changes.extend(
            _compare_scalar_keyword(
                old_schema,
                new_schema,
                key,
                endpoint,
                method=method,
                direction=direction,
                location=location,
                schema_path=schema_path,
            )
        )

    # -----------------------------------------------------------------------
    # Enum
    # -----------------------------------------------------------------------
    changes.extend(
        compare_enums(
            old_schema,
            new_schema,
            endpoint,
            method=method,
            direction=direction,
            location=location,
            schema_path=schema_path,
        )
    )

    # -----------------------------------------------------------------------
    # Constraints
    # -----------------------------------------------------------------------
    for key in CONSTRAINT_KEYS:
        changes.extend(
            _compare_constraint_keyword(
                old_schema,
                new_schema,
                key,
                endpoint,
                method=method,
                direction=direction,
                location=location,
                schema_path=schema_path,
            )
        )

    # -----------------------------------------------------------------------
    # Composition
    # -----------------------------------------------------------------------
    for key in SCHEMA_LIST_KEYS:
        changes.extend(
            _compare_composition_keyword(
                old_schema,
                new_schema,
                key,
                endpoint,
                method=method,
                direction=direction,
                location=location,
                schema_path=schema_path,
            )
        )

    # -----------------------------------------------------------------------
    # Other schema-valued keywords
    # -----------------------------------------------------------------------
    for key in SCHEMA_VALUE_KEYS:
        # additionalProperties already handled as a constraint.
        if key == "additionalProperties":
            continue

        changes.extend(
            _compare_generic_schema_value(
                old_schema,
                new_schema,
                key,
                endpoint,
                method=method,
                direction=direction,
                location=location,
                schema_path=schema_path,
            )
        )

    # -----------------------------------------------------------------------
    # Schema maps
    # -----------------------------------------------------------------------
    for key in SCHEMA_MAP_KEYS:
        changes.extend(
            _compare_schema_map(
                old_schema,
                new_schema,
                key,
                endpoint,
                method=method,
                direction=direction,
                location=location,
                schema_path=schema_path,
            )
        )

    # -----------------------------------------------------------------------
    # List-valued schema keywords
    # -----------------------------------------------------------------------
    for key in LIST_VALUE_KEYS:
        old_value = old_schema.get(key)
        new_value = new_schema.get(key)

        if _values_equal(old_value, new_value):
            continue

        relation = _make_relation(
            key,
            old_value,
            new_value,
        )

        changes.append(
            make_change(
                f"schema_{key}_changed",
                endpoint,
                method=method,
                direction=direction,
                location=location,
                parameter=field,
                schema_path=schema_path,
                old_value=old_value,
                new_value=new_value,
                keyword=key,
                relation=relation,
            )
        )

    # -----------------------------------------------------------------------
    # Object properties
    # -----------------------------------------------------------------------
    old_properties = old_schema.get("properties") or {}
    new_properties = new_schema.get("properties") or {}

    if not isinstance(old_properties, dict):
        old_properties = {}

    if not isinstance(new_properties, dict):
        new_properties = {}

    old_required = set(old_schema.get("required") or [])
    new_required = set(new_schema.get("required") or [])

    prefix = _prefix(direction)

    added_properties = sorted(
        set(new_properties) - set(old_properties),
        key=str,
    )

    removed_properties = sorted(
        set(old_properties) - set(new_properties),
        key=str,
    )

    common_properties = sorted(
        set(old_properties) & set(new_properties),
        key=str,
    )

    # Added fields
    for name in added_properties:
        path = _prefix_for_path(
            schema_path,
            name,
        )

        if prefix is None:
            change_type = "schema_field_added"
        else:
            change_type = (
                f"{prefix}_field_added_required"
                if name in new_required
                else f"{prefix}_field_added_optional"
            )

        changes.append(
            make_change(
                change_type,
                endpoint,
                method=method,
                direction=direction,
                location=location,
                parameter=_field_name(path),
                schema_path=path,
                old_value=None,
                new_value=new_properties[name],
                keyword="properties",
                relation="added",
            )
        )

    # Removed fields
    for name in removed_properties:
        path = _prefix_for_path(
            schema_path,
            name,
        )

        if prefix is None:
            change_type = "schema_field_removed"
        else:
            change_type = f"{prefix}_field_removed"

        changes.append(
            make_change(
                change_type,
                endpoint,
                method=method,
                direction=direction,
                location=location,
                parameter=_field_name(path),
                schema_path=path,
                old_value=old_properties[name],
                new_value=None,
                keyword="properties",
                relation="removed",
            )
        )

    # Existing fields
    for name in common_properties:
        path = _prefix_for_path(
            schema_path,
            name,
        )

        old_is_required = name in old_required
        new_is_required = name in new_required

        if old_is_required != new_is_required:
            if prefix is None:
                change_type = "schema_field_required_changed"
            else:
                change_type = f"{prefix}_field_required_changed"

            relation = (
                "tightened"
                if old_is_required is False and new_is_required is True
                else "relaxed"
            )

            changes.append(
                make_change(
                    change_type,
                    endpoint,
                    method=method,
                    direction=direction,
                    location=location,
                    parameter=_field_name(path),
                    schema_path=path,
                    old_value=old_is_required,
                    new_value=new_is_required,
                    keyword="required",
                    relation=relation,
                )
            )

        changes.extend(
            compare_schema(
                old_properties[name],
                new_properties[name],
                endpoint,
                method=method,
                direction=direction,
                location=location,
                schema_path=path,
                visited=visited,
            )
        )

    # -----------------------------------------------------------------------
    # Array items
    # -----------------------------------------------------------------------
    if "items" in old_schema or "items" in new_schema:
        old_items = old_schema.get("items", {})
        new_items = new_schema.get("items", {})

        if (
            isinstance(old_items, dict)
            and isinstance(new_items, dict)
        ):
            changes.extend(
                compare_schema(
                    old_items,
                    new_items,
                    endpoint,
                    method=method,
                    direction=direction,
                    location=location,
                    schema_path=f"{schema_path}[*]",
                    visited=visited,
                )
            )
        elif not _values_equal(old_items, new_items):
            changes.append(
                make_change(
                    "schema_items_changed",
                    endpoint,
                    method=method,
                    direction=direction,
                    location=location,
                    parameter=field,
                    schema_path=f"{schema_path}[*]",
                    old_value=old_items,
                    new_value=new_items,
                    keyword="items",
                    relation=_make_relation(
                        "items",
                        old_items,
                        new_items,
                    ),
                )
            )

    # -----------------------------------------------------------------------
    # Tuple-style prefixItems
    # -----------------------------------------------------------------------
    old_prefix = old_schema.get("prefixItems") or []
    new_prefix = new_schema.get("prefixItems") or []

    if isinstance(old_prefix, list) and isinstance(new_prefix, list):
        common_length = min(
            len(old_prefix),
            len(new_prefix),
        )

        for index in range(common_length):
            old_item = old_prefix[index]
            new_item = new_prefix[index]

            if (
                isinstance(old_item, dict)
                and isinstance(new_item, dict)
            ):
                changes.extend(
                    compare_schema(
                        old_item,
                        new_item,
                        endpoint,
                        method=method,
                        direction=direction,
                        location=location,
                        schema_path=f"{schema_path}[{index}]",
                        visited=visited,
                    )
                )
            elif not _values_equal(old_item, new_item):
                changes.append(
                    make_change(
                        "schema_prefixItems_changed",
                        endpoint,
                        method=method,
                        direction=direction,
                        location=location,
                        parameter=field,
                        schema_path=f"{schema_path}[{index}]",
                        old_value=old_item,
                        new_value=new_item,
                        keyword="prefixItems",
                        relation="changed",
                    )
                )

        if len(old_prefix) != len(new_prefix):
            changes.append(
                make_change(
                    "schema_prefixItems_changed",
                    endpoint,
                    method=method,
                    direction=direction,
                    location=location,
                    parameter=field,
                    schema_path=schema_path,
                    old_value=old_prefix,
                    new_value=new_prefix,
                    keyword="prefixItems",
                    relation=(
                        "added"
                        if len(new_prefix) > len(old_prefix)
                        else "removed"
                    ),
                )
            )

    return changes