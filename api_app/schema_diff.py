from __future__ import annotations

from typing import Any


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
    "additionalProperties",
)


def _prefix(direction: str) -> str:
    return "response" if direction == "response" else "request"


def _field_name(schema_path: str) -> str:
    return schema_path[2:] if schema_path.startswith("$.") else schema_path


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
) -> dict[str, Any]:
    return {
        "change_type": change_type,
        "category": category,
        "endpoint": endpoint,
        "method": method,
        "direction": direction,
        "location": location,
        "parameter": parameter,
        "schema_path": schema_path,
        "old_value": old_value,
        "new_value": new_value,
        "confidence": confidence,
        "source": source or {},
    }


def _constraint_relation(key: str, old_value: Any, new_value: Any) -> str:
    tighter_when_increases = {"minimum", "exclusiveMinimum", "minLength", "minItems"}
    tighter_when_decreases = {"maximum", "exclusiveMaximum", "maxLength", "maxItems"}
    if old_value is None:
        return "added"
    if new_value is None:
        return "removed"
    if key in tighter_when_increases and new_value > old_value:
        return "tightened"
    if key in tighter_when_increases and new_value < old_value:
        return "relaxed"
    if key in tighter_when_decreases and new_value < old_value:
        return "tightened"
    if key in tighter_when_decreases and new_value > old_value:
        return "relaxed"
    return "changed"


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
    if old_enum == new_enum:
        return []
    if isinstance(old_enum, list) and isinstance(new_enum, list):
        old_set = set(old_enum)
        new_set = set(new_enum)
        if old_set == new_set:
            return []
        changes = []
        removed = sorted(old_set - new_set, key=str)
        added = sorted(new_set - old_set, key=str)
        if removed:
            changes.append(make_change(
                "enum_values_removed",
                endpoint,
                method=method,
                direction=direction,
                location=location,
                parameter=_field_name(schema_path),
                schema_path=schema_path,
                old_value=removed,
                new_value=sorted(new_set, key=str),
            ))
        if added:
            changes.append(make_change(
                "enum_values_added",
                endpoint,
                method=method,
                direction=direction,
                location=location,
                parameter=_field_name(schema_path),
                schema_path=schema_path,
                old_value=sorted(old_set, key=str),
                new_value=added,
            ))
        return changes
    if old_enum != new_enum:
        return [make_change(
            "enum_changed",
            endpoint,
            method=method,
            direction=direction,
            location=location,
            parameter=_field_name(schema_path),
            schema_path=schema_path,
            old_value=old_enum,
            new_value=new_enum,
        )]
    return []


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
    old_schema = old_schema or {}
    new_schema = new_schema or {}
    visited = visited or set()
    marker = (id(old_schema), id(new_schema), schema_path)
    if marker in visited:
        return []
    visited.add(marker)

    changes: list[dict[str, Any]] = []
    field = _field_name(schema_path)

    for key in ("type", "format", "nullable", "default", "readOnly", "writeOnly", "discriminator"):
        if old_schema.get(key) != new_schema.get(key):
            changes.append(make_change(
                f"schema_{key}_changed",
                endpoint,
                method=method,
                direction=direction,
                location=location,
                parameter=field,
                schema_path=schema_path,
                old_value=old_schema.get(key),
                new_value=new_schema.get(key),
            ))

    changes.extend(compare_enums(
        old_schema,
        new_schema,
        endpoint,
        method=method,
        direction=direction,
        location=location,
        schema_path=schema_path,
    ))

    for key in CONSTRAINT_KEYS:
        if old_schema.get(key) != new_schema.get(key):
            changes.append(make_change(
                "schema_constraint_changed",
                endpoint,
                method=method,
                direction=direction,
                location=location,
                parameter=field,
                schema_path=schema_path,
                old_value={"constraint": key, "value": old_schema.get(key)},
                new_value={
                    "constraint": key,
                    "value": new_schema.get(key),
                    "relation": _constraint_relation(key, old_schema.get(key), new_schema.get(key)),
                },
            ))

    for key in ("allOf", "anyOf", "oneOf"):
        if old_schema.get(key) != new_schema.get(key):
            changes.append(make_change(
                "schema_composition_changed",
                endpoint,
                method=method,
                direction=direction,
                location=location,
                parameter=field,
                schema_path=schema_path,
                old_value={key: old_schema.get(key)},
                new_value={key: new_schema.get(key)},
            ))

    old_properties = old_schema.get("properties") or {}
    new_properties = new_schema.get("properties") or {}
    old_required = set(old_schema.get("required") or [])
    new_required = set(new_schema.get("required") or [])
    prefix = _prefix(direction)

    for name in sorted(set(new_properties) - set(old_properties)):
        path = f"{schema_path}.{name}" if schema_path != "$" else f"$.{name}"
        changes.append(make_change(
            f"{prefix}_field_added_required" if name in new_required else f"{prefix}_field_added_optional",
            endpoint,
            method=method,
            direction=direction,
            location=location,
            parameter=_field_name(path),
            schema_path=path,
            old_value=None,
            new_value=new_properties[name],
        ))

    for name in sorted(set(old_properties) - set(new_properties)):
        path = f"{schema_path}.{name}" if schema_path != "$" else f"$.{name}"
        changes.append(make_change(
            f"{prefix}_field_removed",
            endpoint,
            method=method,
            direction=direction,
            location=location,
            parameter=_field_name(path),
            schema_path=path,
            old_value=old_properties[name],
            new_value=None,
        ))

    for name in sorted(set(old_properties) & set(new_properties)):
        path = f"{schema_path}.{name}" if schema_path != "$" else f"$.{name}"
        old_is_required = name in old_required
        new_is_required = name in new_required
        if old_is_required != new_is_required:
            changes.append(make_change(
                f"{prefix}_field_required_changed",
                endpoint,
                method=method,
                direction=direction,
                location=location,
                parameter=_field_name(path),
                schema_path=path,
                old_value=old_is_required,
                new_value=new_is_required,
            ))
        changes.extend(compare_schema(
            old_properties[name],
            new_properties[name],
            endpoint,
            method=method,
            direction=direction,
            location=location,
            schema_path=path,
            visited=visited,
        ))

    if "items" in old_schema or "items" in new_schema:
        changes.extend(compare_schema(
            old_schema.get("items", {}),
            new_schema.get("items", {}),
            endpoint,
            method=method,
            direction=direction,
            location=location,
            schema_path=f"{schema_path}[*]",
            visited=visited,
        ))

    return changes
