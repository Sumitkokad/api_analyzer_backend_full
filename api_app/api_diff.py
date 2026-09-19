from __future__ import annotations

import logging
from typing import Any, Iterable

from .api_normalization import (
    HTTP_METHODS,
    canonical_path_key,
    normalize_openapi_spec,
    path_parameter_names,
)
from .schema_diff import compare_schema, make_change
from .schemas import APIChange


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Basic helpers
# ---------------------------------------------------------------------------

def _endpoint(method: str, path: str) -> str:
    return f"{method.upper()} {path}"


def _stable_value(value: Any) -> str:
    """
    Deterministic representation used for comparison only.
    """
    import json

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _values_equal(old_value: Any, new_value: Any) -> bool:
    return _stable_value(old_value) == _stable_value(new_value)


def _operations(path_data: dict[str, Any]) -> dict[str, Any]:
    """
    Supports normalized shape:

        {"operations": {...}}

    and keeps a small amount of compatibility with old direct-method data.
    """
    if not isinstance(path_data, dict):
        return {}

    if isinstance(path_data.get("operations"), dict):
        return path_data["operations"]

    return {
        key: value
        for key, value in path_data.items()
        if str(key).lower() in HTTP_METHODS
        and isinstance(value, dict)
    }


def _is_normalized(spec: dict[str, Any]) -> bool:
    paths = spec.get("paths")

    if not isinstance(paths, dict):
        return False

    for path_data in paths.values():
        if isinstance(path_data, dict) and "operations" in path_data:
            return True

    return False


def _normalize_for_diff(spec: dict[str, Any]) -> dict[str, Any]:
    """
    Accept both raw OpenAPI dictionaries and already-normalized dictionaries.
    """
    if _is_normalized(spec):
        return spec

    return normalize_openapi_spec(spec)


# ---------------------------------------------------------------------------
# Endpoint matching
# ---------------------------------------------------------------------------

def _endpoint_index(
    spec: dict[str, Any],
) -> dict[tuple[str, str], list[str]]:
    """
    Maps:

        (canonical_path, method)

    to the original normalized path(s).

    Canonical paths replace parameter names with positional placeholders:

        /users/{id} -> /users/{}
        /users/{pk} -> /users/{}
    """
    result: dict[tuple[str, str], list[str]] = {}

    for path, path_data in spec.get("paths", {}).items():
        operations = _operations(path_data)

        canonical = canonical_path_key(path)

        for method in operations:
            method_lower = str(method).lower()

            if method_lower not in HTTP_METHODS:
                continue

            key = (
                canonical,
                method_lower,
            )

            result.setdefault(
                key,
                [],
            ).append(path)

    for paths in result.values():
        paths.sort()

    return result


def _find_matching_path(
    old_paths: list[str],
    new_paths: list[str],
) -> tuple[str, str] | None:
    """
    Deterministically choose one old/new path when canonical route identity
    matches.

    In a valid OpenAPI document there should normally be only one path for a
    canonical route. If duplicates exist, sort and pair deterministically.
    """
    if not old_paths or not new_paths:
        return None

    return (
        sorted(old_paths)[0],
        sorted(new_paths)[0],
    )


def _parameter_rename_pairs(
    old_path: str,
    new_path: str,
) -> list[tuple[str, str]]:
    """
    Pair path parameters by position.

    /users/{id}/posts/{post_id}
    /users/{pk}/posts/{postId}

    becomes:

        id -> pk
        post_id -> postId
    """
    old_names = path_parameter_names(old_path)
    new_names = path_parameter_names(new_path)

    if len(old_names) != len(new_names):
        return []

    pairs: list[tuple[str, str]] = []

    for old_name, new_name in zip(old_names, new_names):
        if old_name != new_name:
            pairs.append(
                (
                    old_name,
                    new_name,
                )
            )

    return pairs


# ---------------------------------------------------------------------------
# Legacy endpoint helpers
# ---------------------------------------------------------------------------

def get_endpoints(api_spec: dict[str, Any]) -> set[str]:
    """
    Backwards-compatible helper.

    For raw OpenAPI documents it uses the raw paths.
    For normalized documents it uses normalized operations.
    """
    spec = _normalize_for_diff(api_spec)

    endpoints: set[str] = set()

    for path, path_data in spec.get("paths", {}).items():
        for method in _operations(path_data):
            if str(method).lower() in HTTP_METHODS:
                endpoints.add(
                    _endpoint(
                        str(method),
                        path,
                    )
                )

    return endpoints


def compare_endpoints(
    old_api: dict[str, Any],
    new_api: dict[str, Any],
) -> dict[str, list[str]]:
    """
    Backwards-compatible endpoint-only comparison.

    New code should use compare_api_specs().
    """
    old_spec = _normalize_for_diff(old_api)
    new_spec = _normalize_for_diff(new_api)

    old_index = _endpoint_index(old_spec)
    new_index = _endpoint_index(new_spec)

    old_keys = set(old_index)
    new_keys = set(new_index)

    added: list[str] = []
    removed: list[str] = []

    for key in sorted(new_keys - old_keys):
        for path in new_index[key]:
            added.append(
                _endpoint(
                    key[1],
                    path,
                )
            )

    for key in sorted(old_keys - new_keys):
        for path in old_index[key]:
            removed.append(
                _endpoint(
                    key[1],
                    path,
                )
            )

    return {
        "added": sorted(added),
        "removed": sorted(removed),
    }


# ---------------------------------------------------------------------------
# Legacy parameter helper
# ---------------------------------------------------------------------------

def get_parameters(operation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    parameters = operation.get("parameters", {})

    if isinstance(parameters, dict):
        return parameters

    if isinstance(parameters, list):
        result: dict[str, dict[str, Any]] = {}

        for parameter in parameters:
            if not isinstance(parameter, dict):
                continue

            name = parameter.get("name")

            if name:
                result[str(name)] = parameter

        return result

    return {}


# ---------------------------------------------------------------------------
# Security helpers
# ---------------------------------------------------------------------------

def _security_set(
    security: Any,
) -> set[str]:
    if not isinstance(security, list):
        return set()

    return {
        _stable_value(requirement)
        for requirement in security
        if isinstance(requirement, dict)
    }


def _security_requirement_by_scheme(
    security: Any,
) -> list[dict[str, set[str]]]:
    """
    Converts:

        [
            {"oauth": ["read", "write"]},
            {"apiKey": []}
        ]

    into deterministic structures suitable for relation detection.
    """
    if not isinstance(security, list):
        return []

    result: list[dict[str, set[str]]] = []

    for requirement in security:
        if not isinstance(requirement, dict):
            continue

        normalized: dict[str, set[str]] = {}

        for scheme, scopes in requirement.items():
            if isinstance(scopes, list):
                normalized[str(scheme)] = {
                    str(scope)
                    for scope in scopes
                }
            else:
                normalized[str(scheme)] = set()

        result.append(normalized)

    return result


def _security_relation(
    old_security: Any,
    new_security: Any,
) -> str:
    """
    Produces the relation consumed by the deterministic rule engine.

    This is deliberately conservative:
        requirement_added
        requirement_removed
        scope_added
        scope_removed
        scheme_replaced
        changed
    """
    old_set = _security_set(old_security)
    new_set = _security_set(new_security)

    added_requirements = new_set - old_set
    removed_requirements = old_set - new_set

    old_requirements = _security_requirement_by_scheme(
        old_security
    )
    new_requirements = _security_requirement_by_scheme(
        new_security
    )

    scope_added = False
    scope_removed = False

    for old_req in old_requirements:
        for new_req in new_requirements:
            common_schemes = (
                set(old_req) & set(new_req)
            )

            for scheme in common_schemes:
                old_scopes = old_req[scheme]
                new_scopes = new_req[scheme]

                if new_scopes - old_scopes:
                    scope_added = True

                if old_scopes - new_scopes:
                    scope_removed = True

    if added_requirements and removed_requirements:
        return "scheme_replaced"

    if added_requirements:
        return "requirement_added"

    if removed_requirements:
        return "requirement_removed"

    if scope_added and scope_removed:
        return "scheme_replaced"

    if scope_added:
        return "scope_added"

    if scope_removed:
        return "scope_removed"

    return "changed"


# ---------------------------------------------------------------------------
# Parameter serialization helpers
# ---------------------------------------------------------------------------

_PARAMETER_DEFAULTS = {
    "query": {
        "style": "form",
        "explode": True,
        "allowReserved": False,
    },
    "path": {
        "style": "simple",
        "explode": False,
        "allowReserved": False,
    },
    "header": {
        "style": "simple",
        "explode": False,
        "allowReserved": False,
    },
    "cookie": {
        "style": "form",
        "explode": True,
        "allowReserved": False,
    },
}


def _serialization_signature(
    parameter: dict[str, Any],
) -> dict[str, Any]:
    location = parameter.get("in")

    defaults = _PARAMETER_DEFAULTS.get(
        location,
        {},
    )

    return {
        "style": parameter.get(
            "style",
            defaults.get("style"),
        ),
        "explode": parameter.get(
            "explode",
            defaults.get("explode"),
        ),
        "allowReserved": parameter.get(
            "allowReserved",
            defaults.get("allowReserved"),
        ),
        "schema": parameter.get("schema"),
        "content": parameter.get("content"),
    }


def _parameter_paths(
    parameters: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        key: value
        for key, value in parameters.items()
        if value.get("in") == "path"
    }


def _parameter_match_pairs(
    old_parameters: dict[str, dict[str, Any]],
    new_parameters: dict[str, dict[str, Any]],
    old_path: str,
    new_path: str,
) -> tuple[
    list[tuple[str, str]],
    set[str],
    set[str],
]:
    """
    Return:

        matched_pairs
        removed_keys
        added_keys

    Exact parameter names are matched first.

    Path parameters are then paired positionally so:

        id -> pk

    becomes a rename instead of remove/add.
    """
    old_keys = set(old_parameters)
    new_keys = set(new_parameters)

    matched: list[tuple[str, str]] = []

    exact = sorted(
        old_keys & new_keys
    )

    for key in exact:
        matched.append(
            (
                key,
                key,
            )
        )

    unmatched_old = old_keys - {
        pair[0]
        for pair in matched
    }

    unmatched_new = new_keys - {
        pair[1]
        for pair in matched
    }

    old_path_keys = [
        key
        for key in unmatched_old
        if old_parameters[key].get("in") == "path"
    ]

    new_path_keys = [
        key
        for key in unmatched_new
        if new_parameters[key].get("in") == "path"
    ]

    old_path_keys.sort()
    new_path_keys.sort()

    # Use actual path parameter position for rename matching.
    old_path_names = path_parameter_names(
        old_path
    )
    new_path_names = path_parameter_names(
        new_path
    )

    old_by_name = {
        name: key
        for key in old_path_keys
        for name in [old_parameters[key].get("name")]
    }

    new_by_name = {
        name: key
        for key in new_path_keys
        for name in [new_parameters[key].get("name")]
    }

    if len(old_path_names) == len(new_path_names):
        for old_name, new_name in zip(
            old_path_names,
            new_path_names,
        ):
            if old_name == new_name:
                continue

            old_key = old_by_name.get(old_name)
            new_key = new_by_name.get(new_name)

            if old_key and new_key:
                matched.append(
                    (
                        old_key,
                        new_key,
                    )
                )

    matched_old = {
        pair[0]
        for pair in matched
    }

    matched_new = {
        pair[1]
        for pair in matched
    }

    removed = old_keys - matched_old
    added = new_keys - matched_new

    return (
        sorted(matched),
        removed,
        added,
    )


# ---------------------------------------------------------------------------
# Legacy parameter comparison
# ---------------------------------------------------------------------------

def compare_parameters(
    old_api: dict[str, Any],
    new_api: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Backwards-compatible parameter-only comparison.
    """
    old_spec = _normalize_for_diff(old_api)
    new_spec = _normalize_for_diff(new_api)

    changes = _compare_normalized_parameters(
        old_spec,
        new_spec,
    )

    return [
        change.model_dump()
        if isinstance(change, APIChange)
        else change
        for change in changes
    ]


# ---------------------------------------------------------------------------
# Request body helpers
# ---------------------------------------------------------------------------

def get_request_body_schema(
    operation: dict[str, Any],
) -> dict[str, Any]:
    request_body = (
        operation.get("requestBody")
        or {}
    )

    content = (
        request_body.get("content")
        or {}
    )

    # Prefer application/json for compatibility
    # with the old helper, but now fall back to
    # any available media type.
    json_content = content.get(
        "application/json"
    )

    if isinstance(json_content, dict):
        return json_content.get(
            "schema",
            {},
        )

    for media in sorted(content):
        value = content[media]

        if isinstance(value, dict):
            return value.get(
                "schema",
                {},
            )

    return {}


def get_schema_properties(
    schema: dict[str, Any],
) -> dict[str, Any]:
    return (
        schema.get("properties", {})
        if isinstance(schema, dict)
        else {}
    )


def get_required_fields(
    schema: dict[str, Any],
) -> set[str]:
    if not isinstance(schema, dict):
        return set()

    required = schema.get(
        "required",
        [],
    )

    if not isinstance(required, list):
        return set()

    return {
        str(item)
        for item in required
    }


# ---------------------------------------------------------------------------
# Legacy request comparison
# ---------------------------------------------------------------------------

def compare_request_bodies(
    old_api: dict[str, Any],
    new_api: dict[str, Any],
) -> list[dict[str, Any]]:
    old_spec = _normalize_for_diff(old_api)
    new_spec = _normalize_for_diff(new_api)

    return [
        change.model_dump()
        if isinstance(change, APIChange)
        else change
        for change in _compare_normalized_request_bodies(
            old_spec,
            new_spec,
        )
    ]


# ---------------------------------------------------------------------------
# Response helper
# ---------------------------------------------------------------------------

def get_response_schema(
    operation: dict[str, Any],
) -> dict[str, Any]:
    responses = (
        operation.get("responses")
        or {}
    )

    # Backwards-compatible preference.
    response = responses.get("200")

    if not response:
        response = responses.get("201")

    if not response:
        for status in sorted(responses):
            response = responses[status]
            break

    if not isinstance(response, dict):
        return {}

    content = (
        response.get("content")
        or {}
    )

    json_content = content.get(
        "application/json"
    )

    if isinstance(json_content, dict):
        return json_content.get(
            "schema",
            {},
        )

    for media in sorted(content):
        value = content[media]

        if isinstance(value, dict):
            return value.get(
                "schema",
                {},
            )

    return {}


# ---------------------------------------------------------------------------
# Normalized operation pairing
# ---------------------------------------------------------------------------

def _operation_pairs(
    old_spec: dict[str, Any],
    new_spec: dict[str, Any],
) -> Iterable[
    tuple[
        str,
        str,
        str,
        dict[str, Any],
        dict[str, Any],
    ]
]:
    """
    Yield:

        old_path,
        new_path,
        method,
        old_operation,
        new_operation

    using canonical route identity.
    """
    old_index = _endpoint_index(
        old_spec
    )
    new_index = _endpoint_index(
        new_spec
    )

    common_keys = sorted(
        set(old_index) & set(new_index)
    )

    for canonical_path, method in common_keys:
        match = _find_matching_path(
            old_index[(canonical_path, method)],
            new_index[(canonical_path, method)],
        )

        if match is None:
            continue

        old_path, new_path = match

        old_operation = (
            _operations(
                old_spec["paths"][old_path]
            )
            [method]
        )

        new_operation = (
            _operations(
                new_spec["paths"][new_path]
            )
            [method]
        )

        yield (
            old_path,
            new_path,
            method,
            old_operation,
            new_operation,
        )


# ---------------------------------------------------------------------------
# Endpoint comparison
# ---------------------------------------------------------------------------

def _compare_normalized_endpoints(
    old_spec: dict[str, Any],
    new_spec: dict[str, Any],
) -> list[APIChange]:
    changes: list[APIChange] = []

    old_index = _endpoint_index(
        old_spec
    )

    new_index = _endpoint_index(
        new_spec
    )

    old_keys = set(old_index)
    new_keys = set(new_index)

    # ---------------------------------------------------------------
    # Added routes
    # ---------------------------------------------------------------

    for key in sorted(
        new_keys - old_keys
    ):
        canonical_path, method = key

        for path in new_index[key]:
            endpoint = _endpoint(
                method,
                path,
            )

            changes.append(
                APIChange(
                    **make_change(
                        "endpoint_added",
                        endpoint,
                        method=method,
                        direction="endpoint",
                        location="paths",
                        old_value=None,
                        new_value=path,
                    )
                )
            )

    # ---------------------------------------------------------------
    # Removed routes
    # ---------------------------------------------------------------

    for key in sorted(
        old_keys - new_keys
    ):
        canonical_path, method = key

        for path in old_index[key]:
            endpoint = _endpoint(
                method,
                path,
            )

            changes.append(
                APIChange(
                    **make_change(
                        "endpoint_removed",
                        endpoint,
                        method=method,
                        direction="endpoint",
                        location="paths",
                        old_value=path,
                        new_value=None,
                    )
                )
            )

    # ---------------------------------------------------------------
    # Same canonical route
    # ---------------------------------------------------------------

    for key in sorted(
        old_keys & new_keys
    ):
        canonical_path, method = key

        match = _find_matching_path(
            old_index[key],
            new_index[key],
        )

        if match is None:
            continue

        old_path, new_path = match

        # D8: actual trailing slash is semantically visible.
        if (
            old_path != new_path
            and (
                old_path.rstrip("/") == new_path.rstrip("/")
            )
        ):
            changes.append(
                APIChange(
                    **make_change(
                        "endpoint_trailing_slash_changed",
                        _endpoint(
                            method,
                            new_path,
                        ),
                        method=method,
                        direction="endpoint",
                        location="path",
                        old_value=old_path,
                        new_value=new_path,
                    )
                )
            )

        # D7: path parameter rename.
        for old_name, new_name in _parameter_rename_pairs(
            old_path,
            new_path,
        ):
            changes.append(
                APIChange(
                    **make_change(
                        "path_parameter_renamed",
                        _endpoint(
                            method,
                            new_path,
                        ),
                        method=method,
                        direction="endpoint",
                        location="path",
                        parameter=new_name,
                        old_value=old_name,
                        new_value=new_name,
                    )
                )
            )

    return changes


# ---------------------------------------------------------------------------
# Parameter comparison
# ---------------------------------------------------------------------------

def _compare_normalized_parameters(
    old_spec: dict[str, Any],
    new_spec: dict[str, Any],
) -> list[APIChange]:
    changes: list[APIChange] = []

    for (
        old_path,
        new_path,
        method,
        old_operation,
        new_operation,
    ) in _operation_pairs(
        old_spec,
        new_spec,
    ):
        endpoint = _endpoint(
            method,
            new_path,
        )

        old_parameters = (
            old_operation.get(
                "parameters",
                {},
            )
        )

        new_parameters = (
            new_operation.get(
                "parameters",
                {},
            )
        )

        (
            matched_pairs,
            removed,
            added,
        ) = _parameter_match_pairs(
            old_parameters,
            new_parameters,
            old_path,
            new_path,
        )

        # -----------------------------------------------------------
        # Added
        # -----------------------------------------------------------

        for key in sorted(
            added
        ):
            parameter = new_parameters[key]

            changes.append(
                APIChange(
                    **make_change(
                        (
                            "parameter_added_required"
                            if parameter.get(
                                "required",
                                False,
                            )
                            else "parameter_added_optional"
                        ),
                        endpoint,
                        method=method,
                        direction="request",
                        location=parameter.get("in"),
                        parameter=parameter.get("name"),
                        old_value=None,
                        new_value=parameter,
                    )
                )
            )

        # -----------------------------------------------------------
        # Removed
        # -----------------------------------------------------------

        for key in sorted(
            removed
        ):
            parameter = old_parameters[key]

            changes.append(
                APIChange(
                    **make_change(
                        "parameter_removed",
                        endpoint,
                        method=method,
                        direction="request",
                        location=parameter.get("in"),
                        parameter=parameter.get("name"),
                        old_value=parameter,
                        new_value=None,
                    )
                )
            )

        # -----------------------------------------------------------
        # Matched
        # -----------------------------------------------------------

        for (
            old_key,
            new_key,
        ) in matched_pairs:
            old_parameter = old_parameters[
                old_key
            ]

            new_parameter = new_parameters[
                new_key
            ]

            name = (
                new_parameter.get("name")
                or old_parameter.get("name")
            )

            location = (
                new_parameter.get("in")
                or old_parameter.get("in")
            )

            # Requiredness
            if (
                old_parameter.get(
                    "required",
                    False,
                )
                !=
                new_parameter.get(
                    "required",
                    False,
                )
            ):
                changes.append(
                    APIChange(
                        **make_change(
                            "parameter_required_changed",
                            endpoint,
                            method=method,
                            direction="request",
                            location=location,
                            parameter=name,
                            old_value=old_parameter.get(
                                "required",
                                False,
                            ),
                            new_value=new_parameter.get(
                                "required",
                                False,
                            ),
                        )
                    )
                )

            # Type
            old_schema = (
                old_parameter.get(
                    "schema",
                    {},
                )
                or {}
            )

            new_schema = (
                new_parameter.get(
                    "schema",
                    {},
                )
                or {}
            )

            old_type = old_schema.get(
                "type"
            )

            new_type = new_schema.get(
                "type"
            )

            if not _values_equal(
                old_type,
                new_type,
            ):
                changes.append(
                    APIChange(
                        **make_change(
                            "parameter_type_changed",
                            endpoint,
                            method=method,
                            direction="request",
                            location=location,
                            parameter=name,
                            old_value=old_type,
                            new_value=new_type,
                            keyword="type",
                        )
                    )
                )

            # -------------------------------------------------------
            # Parameter serialization
            # -------------------------------------------------------

            old_serialization = (
                _serialization_signature(
                    old_parameter
                )
            )

            new_serialization = (
                _serialization_signature(
                    new_parameter
                )
            )

            if not _values_equal(
                old_serialization,
                new_serialization,
            ):
                changes.append(
                    APIChange(
                        **make_change(
                            "parameter_style_changed",
                            endpoint,
                            method=method,
                            direction="request",
                            location=location,
                            parameter=name,
                            old_value=old_serialization,
                            new_value=new_serialization,
                        )
                    )
                )

            # -------------------------------------------------------
            # Other schema semantics
            # -------------------------------------------------------

            schema_changes = compare_schema(
                old_schema,
                new_schema,
                endpoint,
                method=method,
                direction="request",
                location=location or "parameter",
                schema_path=f"$.parameters.{name}",
            )

            # The parameter-level type change is already emitted with
            # parameter_type_changed, so avoid duplicate schema_type_changed.
            for raw_change in schema_changes:
                if (
                    raw_change.get(
                        "change_type"
                    )
                    == "schema_type_changed"
                ):
                    continue

                changes.append(
                    APIChange(
                        **raw_change
                    )
                )

    return changes


# ---------------------------------------------------------------------------
# Content/media type comparison
# ---------------------------------------------------------------------------

def _compare_content(
    old_content: dict[str, Any],
    new_content: dict[str, Any],
    endpoint: str,
    method: str,
    direction: str,
    *,
    status: str | None = None,
) -> list[APIChange]:
    changes: list[APIChange] = []

    old_content = (
        old_content
        if isinstance(old_content, dict)
        else {}
    )

    new_content = (
        new_content
        if isinstance(new_content, dict)
        else {}
    )

    old_media = set(
        old_content
    )

    new_media = set(
        new_content
    )

    location = (
        f"response:{status}"
        if status is not None
        else "requestBody"
    )

    # ---------------------------------------------------------------
    # Added media type
    # ---------------------------------------------------------------

    for media_type in sorted(
        new_media - old_media
    ):
        change_type = (
            f"{direction}_media_type_added"
        )

        changes.append(
            APIChange(
                **make_change(
                    change_type,
                    endpoint,
                    method=method,
                    direction=direction,
                    location=location,
                    parameter=media_type,
                    old_value=None,
                    new_value=new_content[
                        media_type
                    ],
                )
            )
        )

    # ---------------------------------------------------------------
    # Removed media type
    # ---------------------------------------------------------------

    for media_type in sorted(
        old_media - new_media
    ):
        change_type = (
            f"{direction}_media_type_removed"
        )

        changes.append(
            APIChange(
                **make_change(
                    change_type,
                    endpoint,
                    method=method,
                    direction=direction,
                    location=location,
                    parameter=media_type,
                    old_value=old_content[
                        media_type
                    ],
                    new_value=None,
                )
            )
        )

    # ---------------------------------------------------------------
    # Common media types
    # ---------------------------------------------------------------

    for media_type in sorted(
        old_media & new_media
    ):
        old_media_entry = (
            old_content[media_type]
            if isinstance(
                old_content[media_type],
                dict,
            )
            else {}
        )

        new_media_entry = (
            new_content[media_type]
            if isinstance(
                new_content[media_type],
                dict,
            )
            else {}
        )

        old_schema = (
            old_media_entry.get(
                "schema",
                {},
            )
            or {}
        )

        new_schema = (
            new_media_entry.get(
                "schema",
                {},
            )
            or {}
        )

        changes.extend(
            APIChange(
                **raw_change
            )
            for raw_change in compare_schema(
                old_schema,
                new_schema,
                endpoint,
                method=method,
                direction=direction,
                location=(
                    f"{location}:{media_type}"
                ),
                schema_path="$",
            )
        )

        # Media-type parameters such as charset are deliberately not treated
        # as distinct media types. The normalization layer canonicalizes:
        #
        #   application/json; charset=utf-8
        #
        # into:
        #
        #   application/json
        #
        # The parameters are retained as metadata for future quality analysis.

    return changes


# ---------------------------------------------------------------------------
# Request body comparison
# ---------------------------------------------------------------------------

def _compare_normalized_request_bodies(
    old_spec: dict[str, Any],
    new_spec: dict[str, Any],
) -> list[APIChange]:
    changes: list[APIChange] = []

    for (
        old_path,
        new_path,
        method,
        old_operation,
        new_operation,
    ) in _operation_pairs(
        old_spec,
        new_spec,
    ):
        endpoint = _endpoint(
            method,
            new_path,
        )

        old_body = (
            old_operation.get(
                "requestBody",
                {},
            )
            or {}
        )

        new_body = (
            new_operation.get(
                "requestBody",
                {},
            )
            or {}
        )

        old_required = bool(
            old_body.get(
                "required",
                False,
            )
        )

        new_required = bool(
            new_body.get(
                "required",
                False,
            )
        )

        if old_required != new_required:
            changes.append(
                APIChange(
                    **make_change(
                        "request_body_required_changed",
                        endpoint,
                        method=method,
                        direction="request",
                        location="requestBody",
                        old_value=old_required,
                        new_value=new_required,
                    )
                )
            )

        changes.extend(
            _compare_content(
                old_body.get(
                    "content",
                    {},
                ),
                new_body.get(
                    "content",
                    {},
                ),
                endpoint,
                method,
                "request",
            )
        )

    return changes


# ---------------------------------------------------------------------------
# Response headers
# ---------------------------------------------------------------------------

def _compare_headers(
    old_headers: dict[str, Any],
    new_headers: dict[str, Any],
    endpoint: str,
    method: str,
    status: str,
) -> list[APIChange]:
    changes: list[APIChange] = []

    old_headers = (
        old_headers
        if isinstance(old_headers, dict)
        else {}
    )

    new_headers = (
        new_headers
        if isinstance(new_headers, dict)
        else {}
    )

    # Added
    for name in sorted(
        set(new_headers) - set(old_headers)
    ):
        changes.append(
            APIChange(
                **make_change(
                    "response_header_added",
                    endpoint,
                    method=method,
                    direction="response",
                    location=(
                        f"response:{status}:header"
                    ),
                    parameter=name,
                    old_value=None,
                    new_value=new_headers[
                        name
                    ],
                )
            )
        )

    # Removed
    for name in sorted(
        set(old_headers) - set(new_headers)
    ):
        changes.append(
            APIChange(
                **make_change(
                    "response_header_removed",
                    endpoint,
                    method=method,
                    direction="response",
                    location=(
                        f"response:{status}:header"
                    ),
                    parameter=name,
                    old_value=old_headers[
                        name
                    ],
                    new_value=None,
                )
            )
        )

    # Common
    for name in sorted(
        set(old_headers) & set(new_headers)
    ):
        old_header = (
            old_headers[name]
            if isinstance(
                old_headers[name],
                dict,
            )
            else {}
        )

        new_header = (
            new_headers[name]
            if isinstance(
                new_headers[name],
                dict,
            )
            else {}
        )

        if (
            old_header.get(
                "required",
                False,
            )
            !=
            new_header.get(
                "required",
                False,
            )
        ):
            changes.append(
                APIChange(
                    **make_change(
                        "response_header_required_changed",
                        endpoint,
                        method=method,
                        direction="response",
                        location=(
                            f"response:{status}:header"
                        ),
                        parameter=name,
                        old_value=old_header.get(
                            "required",
                            False,
                        ),
                        new_value=new_header.get(
                            "required",
                            False,
                        ),
                    )
                )
            )

        old_schema = (
            old_header.get(
                "schema",
                {},
            )
            or {}
        )

        new_schema = (
            new_header.get(
                "schema",
                {},
            )
            or {}
        )

        for raw_change in compare_schema(
            old_schema,
            new_schema,
            endpoint,
            method=method,
            direction="response",
            location=(
                f"response:{status}:header"
            ),
            schema_path=f"$.headers.{name}",
        ):
            changes.append(
                APIChange(
                    **raw_change
                )
            )

    return changes


# ---------------------------------------------------------------------------
# Response comparison
# ---------------------------------------------------------------------------

def _compare_normalized_responses(
    old_spec: dict[str, Any],
    new_spec: dict[str, Any],
) -> list[APIChange]:
    changes: list[APIChange] = []

    for (
        old_path,
        new_path,
        method,
        old_operation,
        new_operation,
    ) in _operation_pairs(
        old_spec,
        new_spec,
    ):
        endpoint = _endpoint(
            method,
            new_path,
        )

        old_responses = (
            old_operation.get(
                "responses",
                {},
            )
            or {}
        )

        new_responses = (
            new_operation.get(
                "responses",
                {},
            )
            or {}
        )

        # -----------------------------------------------------------
        # Added response statuses
        # -----------------------------------------------------------

        for status in sorted(
            set(new_responses) - set(old_responses),
            key=str,
        ):
            changes.append(
                APIChange(
                    **make_change(
                        "response_status_added",
                        endpoint,
                        method=method,
                        direction="response",
                        location="response",
                        parameter=str(status),
                        old_value=None,
                        new_value=new_responses[
                            status
                        ],
                    )
                )
            )

        # -----------------------------------------------------------
        # Removed response statuses
        # -----------------------------------------------------------

        for status in sorted(
            set(old_responses) - set(new_responses),
            key=str,
        ):
            changes.append(
                APIChange(
                    **make_change(
                        "response_status_removed",
                        endpoint,
                        method=method,
                        direction="response",
                        location="response",
                        parameter=str(status),
                        old_value=old_responses[
                            status
                        ],
                        new_value=None,
                    )
                )
            )

        # -----------------------------------------------------------
        # Common statuses
        # -----------------------------------------------------------

        for status in sorted(
            set(old_responses) & set(new_responses),
            key=str,
        ):
            old_response = (
                old_responses[status]
                if isinstance(
                    old_responses[status],
                    dict,
                )
                else {}
            )

            new_response = (
                new_responses[status]
                if isinstance(
                    new_responses[status],
                    dict,
                )
                else {}
            )

            changes.extend(
                _compare_content(
                    old_response.get(
                        "content",
                        {},
                    ),
                    new_response.get(
                        "content",
                        {},
                    ),
                    endpoint,
                    method,
                    "response",
                    status=str(status),
                )
            )

            changes.extend(
                _compare_headers(
                    old_response.get(
                        "headers",
                        {},
                    ),
                    new_response.get(
                        "headers",
                        {},
                    ),
                    endpoint,
                    method,
                    str(status),
                )
            )

    return changes


# ---------------------------------------------------------------------------
# Legacy response comparison
# ---------------------------------------------------------------------------

def compare_responses(
    old_api: dict[str, Any],
    new_api: dict[str, Any],
) -> list[dict[str, Any]]:
    old_spec = _normalize_for_diff(old_api)
    new_spec = _normalize_for_diff(new_api)

    return [
        change.model_dump()
        if isinstance(change, APIChange)
        else change
        for change in _compare_normalized_responses(
            old_spec,
            new_spec,
        )
    ]


# ---------------------------------------------------------------------------
# Security comparison
# ---------------------------------------------------------------------------

def _compare_security_schemes(
    old_spec: dict[str, Any],
    new_spec: dict[str, Any],
) -> list[APIChange]:
    changes: list[APIChange] = []

    old_schemes = (
        old_spec
        .get("components", {})
        .get("securitySchemes", {})
        or {}
    )

    new_schemes = (
        new_spec
        .get("components", {})
        .get("securitySchemes", {})
        or {}
    )

    # Added
    for name in sorted(
        set(new_schemes) - set(old_schemes)
    ):
        changes.append(
            APIChange(
                **make_change(
                    "security_scheme_added",
                    "GLOBAL",
                    direction="security",
                    location=(
                        "components.securitySchemes"
                    ),
                    parameter=name,
                    old_value=None,
                    new_value=new_schemes[
                        name
                    ],
                )
            )
        )

    # Removed
    for name in sorted(
        set(old_schemes) - set(new_schemes)
    ):
        changes.append(
            APIChange(
                **make_change(
                    "security_scheme_removed",
                    "GLOBAL",
                    direction="security",
                    location=(
                        "components.securitySchemes"
                    ),
                    parameter=name,
                    old_value=old_schemes[
                        name
                    ],
                    new_value=None,
                )
            )
        )

    # Functional changes
    for name in sorted(
        set(old_schemes) & set(new_schemes)
    ):
        old_scheme = (
            old_schemes[name]
        )

        new_scheme = (
            new_schemes[name]
        )

        if not _values_equal(
            old_scheme,
            new_scheme,
        ):
            changes.append(
                APIChange(
                    **make_change(
                        "security_scheme_changed",
                        "GLOBAL",
                        direction="security",
                        location=(
                            "components.securitySchemes"
                        ),
                        parameter=name,
                        old_value=old_scheme,
                        new_value=new_scheme,
                        relation="scheme_replaced",
                    )
                )
            )

    return changes


def _compare_security_requirements(
    old_security: Any,
    new_security: Any,
    *,
    change_type: str,
    endpoint: str,
    method: str | None = None,
    explicit: bool = False,
) -> APIChange | None:
    if _values_equal(
        old_security,
        new_security,
    ):
        return None

    relation = _security_relation(
        old_security,
        new_security,
    )

    return APIChange(
        **make_change(
            change_type,
            endpoint,
            method=method,
            direction="security",
            location=(
                "operation"
                if method
                else "global"
            ),
            old_value=old_security,
            new_value=new_security,
            relation=relation,
            source={
                "explicit_override": explicit,
            },
        )
    )


def _compare_security(
    old_spec: dict[str, Any],
    new_spec: dict[str, Any],
) -> list[APIChange]:
    changes: list[APIChange] = []

    # ---------------------------------------------------------------
    # Security schemes
    # ---------------------------------------------------------------

    changes.extend(
        _compare_security_schemes(
            old_spec,
            new_spec,
        )
    )

    # ---------------------------------------------------------------
    # Global security
    # ---------------------------------------------------------------

    global_change = (
        _compare_security_requirements(
            old_spec.get(
                "security",
                [],
            ),
            new_spec.get(
                "security",
                [],
            ),
            change_type="global_security_changed",
            endpoint="GLOBAL",
        )
    )

    if global_change is not None:
        changes.append(
            global_change
        )

    # ---------------------------------------------------------------
    # Explicit operation overrides only
    # ---------------------------------------------------------------

    for (
        old_path,
        new_path,
        method,
        old_operation,
        new_operation,
    ) in _operation_pairs(
        old_spec,
        new_spec,
    ):
        old_explicit = bool(
            old_operation.get(
                "x-security-explicit",
                False,
            )
        )

        new_explicit = bool(
            new_operation.get(
                "x-security-explicit",
                False,
            )
        )

        if not (
            old_explicit
            or new_explicit
        ):
            # Important D5 behavior:
            #
            # Global security changes must not fan out into N operation
            # security changes.
            continue

        endpoint = _endpoint(
            method,
            new_path,
        )

        operation_change = (
            _compare_security_requirements(
                old_operation.get(
                    "security",
                    [],
                ),
                new_operation.get(
                    "security",
                    [],
                ),
                change_type="operation_security_changed",
                endpoint=endpoint,
                method=method,
                explicit=True,
            )
        )

        if operation_change is not None:
            changes.append(
                operation_change
            )

    return changes


# ---------------------------------------------------------------------------
# Metadata comparison
# ---------------------------------------------------------------------------

def _compare_metadata(
    old_spec: dict[str, Any],
    new_spec: dict[str, Any],
) -> list[APIChange]:
    changes: list[APIChange] = []

    metadata_keys = (
        "operationId",
        "deprecated",
        "summary",
        "description",
        "tags",
        "servers",
        "callbacks",
    )

    for (
        old_path,
        new_path,
        method,
        old_operation,
        new_operation,
    ) in _operation_pairs(
        old_spec,
        new_spec,
    ):
        endpoint = _endpoint(
            method,
            new_path,
        )

        for key in metadata_keys:
            old_value = old_operation.get(
                key
            )

            new_value = new_operation.get(
                key
            )

            if _values_equal(
                old_value,
                new_value,
            ):
                continue

            changes.append(
                APIChange(
                    **make_change(
                        f"operation_{key}_changed",
                        endpoint,
                        method=method,
                        direction="metadata",
                        location="operation",
                        old_value=old_value,
                        new_value=new_value,
                        category="metadata",
                    )
                )
            )

    return changes


# ---------------------------------------------------------------------------
# Stable deduplication
# ---------------------------------------------------------------------------

def deduplicate_changes(
    changes: list[APIChange],
) -> list[APIChange]:
    """
    Collision-safe deterministic deduplication.

    IMPORTANT:
    We deduplicate using stable_hash, not the short 16-character change_id.

    Therefore two semantically distinct changes such as:

        minimum: 1 -> 5
        maximum: 100 -> 50

    cannot be silently collapsed simply because they share the same location.
    """
    by_stable_hash: dict[str, APIChange] = {}

    short_id_to_hash: dict[str, str] = {}

    for change in changes:
        stable_hash = (
            change.stable_hash
            or change.compute_stable_hash()
        )

        short_id = (
            change.change_id
            or change.compute_change_id()
        )

        if short_id in short_id_to_hash:
            previous_hash = (
                short_id_to_hash[short_id]
            )

            if previous_hash != stable_hash:
                logger.warning(
                    "Change ID collision detected: "
                    "change_id=%s previous_hash=%s new_hash=%s",
                    short_id,
                    previous_hash,
                    stable_hash,
                )

        short_id_to_hash[
            short_id
        ] = stable_hash

        # Exact semantic duplicates collapse.
        by_stable_hash.setdefault(
            stable_hash,
            change,
        )

    return sorted(
        by_stable_hash.values(),
        key=lambda change: (
            change.endpoint,
            change.method or "",
            change.change_type,
            change.direction,
            change.location or "",
            change.parameter or "",
            change.schema_path or "",
            change.keyword or "",
            change.relation or "",
            change.stable_hash or "",
        ),
    )


# ---------------------------------------------------------------------------
# Authoritative comparison pipeline
# ---------------------------------------------------------------------------

def compare_api_specs(
    old_api: dict[str, Any],
    new_api: dict[str, Any],
) -> list[APIChange]:
    """
    Authoritative semantic OpenAPI comparison.

    Flow:

        raw BASE
             ↓
        normalization
             ↓
        normalized BASE ─────┐
                             │
        raw HEAD             │
             ↓               │
        normalization        │
             ↓               │
        normalized HEAD ─────┘
                             ↓
                      semantic diff
                             ↓
                       APIChange[]

    Classification/severity are NOT decided here.
    That happens exclusively in breaking_change_rules.evaluate().
    """
    old_spec = _normalize_for_diff(
        old_api
    )

    new_spec = _normalize_for_diff(
        new_api
    )

    changes: list[APIChange] = []

    # Endpoint lifecycle + path rename + trailing slash.
    changes.extend(
        _compare_normalized_endpoints(
            old_spec,
            new_spec,
        )
    )

    # Parameters + styles + parameter schemas.
    changes.extend(
        _compare_normalized_parameters(
            old_spec,
            new_spec,
        )
    )

    # Request bodies + media types + request schemas.
    changes.extend(
        _compare_normalized_request_bodies(
            old_spec,
            new_spec,
        )
    )

    # Responses + media types + headers + response schemas.
    changes.extend(
        _compare_normalized_responses(
            old_spec,
            new_spec,
        )
    )

    # Security schemes + global/explicit operation requirements.
    changes.extend(
        _compare_security(
            old_spec,
            new_spec,
        )
    )

    # Operation metadata.
    changes.extend(
        _compare_metadata(
            old_spec,
            new_spec,
        )
    )

    return deduplicate_changes(
        changes
    )