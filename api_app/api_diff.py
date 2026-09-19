from __future__ import annotations

from typing import Any

try:
    from api_normalization import HTTP_METHODS, normalize_openapi_spec
    from schema_diff import compare_schema, make_change
    from schemas import APIChange
except ImportError:
    from .api_normalization import HTTP_METHODS, normalize_openapi_spec
    from .schema_diff import compare_schema, make_change
    from .schemas import APIChange


def get_endpoints(api_spec):
    endpoints = set()

    for path, methods in api_spec.get("paths", {}).items():
        for method in methods:
            if method.lower() in HTTP_METHODS:
                endpoints.add(f"{method.upper()} {path}")

    return endpoints


def compare_endpoints(old_api, new_api):
    old_endpoints = get_endpoints(old_api)
    new_endpoints = get_endpoints(new_api)

    return {
        "added": sorted(new_endpoints - old_endpoints),
        "removed": sorted(old_endpoints - new_endpoints),
    }


def get_parameters(operation):
    return {
        parameter["name"]: parameter
        for parameter in operation.get("parameters", [])
    }


def compare_parameters(old_api, new_api):
    changes = []

    old_paths = old_api.get("paths", {})
    new_paths = new_api.get("paths", {})

    common_paths = old_paths.keys() & new_paths.keys()

    for path in common_paths:
        old_methods = old_paths[path]
        new_methods = new_paths[path]

        common_methods = old_methods.keys() & new_methods.keys()

        for method in common_methods:
            if method.lower() not in HTTP_METHODS:
                continue

            old_operation = old_methods[method]
            new_operation = new_methods[method]

            old_parameters = get_parameters(old_operation)
            new_parameters = get_parameters(new_operation)

            old_names = set(old_parameters)
            new_names = set(new_parameters)

            endpoint = f"{method.upper()} {path}"

            for name in sorted(new_names - old_names):
                parameter = new_parameters[name]

                changes.append({
                    "change_type": (
                        "parameter_added_required"
                        if parameter.get("required", False)
                        else "parameter_added_optional"
                    ),
                    "endpoint": endpoint,
                    "parameter": name,
                    "old_value": None,
                    "new_value": parameter,
                })

            for name in sorted(old_names - new_names):
                changes.append({
                    "change_type": "parameter_removed",
                    "endpoint": endpoint,
                    "parameter": name,
                    "old_value": old_parameters[name],
                    "new_value": None,
                })

            for name in sorted(old_names & new_names):
                old_parameter = old_parameters[name]
                new_parameter = new_parameters[name]

                old_required = old_parameter.get("required", False)
                new_required = new_parameter.get("required", False)

                if old_required != new_required:
                    changes.append({
                        "change_type": "parameter_required_changed",
                        "endpoint": endpoint,
                        "parameter": name,
                        "old_value": old_required,
                        "new_value": new_required,
                    })

                old_type = old_parameter.get("schema", {}).get("type")
                new_type = new_parameter.get("schema", {}).get("type")

                if old_type != new_type:
                    changes.append({
                        "change_type": "parameter_type_changed",
                        "endpoint": endpoint,
                        "parameter": name,
                        "old_value": old_type,
                        "new_value": new_type,
                    })
    return changes


def get_request_body_schema(operation):
    request_body = operation.get("requestBody", {})

    content = request_body.get("content", {})
    json_content = content.get("application/json", {})

    return json_content.get("schema", {})


def get_schema_properties(schema):
    return schema.get("properties", {})


def get_required_fields(schema):
    return set(schema.get("required", []))


def compare_request_bodies(old_api, new_api):
    changes = []

    old_paths = old_api.get("paths", {})
    new_paths = new_api.get("paths", {})

    common_paths = old_paths.keys() & new_paths.keys()

    for path in common_paths:
        old_methods = old_paths[path]
        new_methods = new_paths[path]

        common_methods = old_methods.keys() & new_methods.keys()

        for method in common_methods:
            if method.lower() not in HTTP_METHODS:
                continue

            old_operation = old_methods[method]
            new_operation = new_methods[method]

            old_schema = get_request_body_schema(old_operation)
            new_schema = get_request_body_schema(new_operation)

            old_properties = get_schema_properties(old_schema)
            new_properties = get_schema_properties(new_schema)

            old_required = get_required_fields(old_schema)
            new_required = get_required_fields(new_schema)

            old_fields = set(old_properties)
            new_fields = set(new_properties)

            endpoint = f"{method.upper()} {path}"

            for field in sorted(new_fields - old_fields):
                changes.append({
                    "change_type": (
                        "request_field_added_required"
                        if field in new_required
                        else "request_field_added_optional"
                    ),
                    "endpoint": endpoint,
                    "parameter": field,
                    "old_value": None,
                    "new_value": new_properties[field],
                })

            for field in sorted(old_fields - new_fields):
                changes.append({
                    "change_type": "request_field_removed",
                    "endpoint": endpoint,
                    "parameter": field,
                    "old_value": old_properties[field],
                    "new_value": None,
                })

            for field in sorted(old_fields & new_fields):
                old_type = old_properties[field].get("type")
                new_type = new_properties[field].get("type")

                if old_type != new_type:
                    changes.append({
                        "change_type": "request_field_type_changed",
                        "endpoint": endpoint,
                        "parameter": field,
                        "old_value": old_type,
                        "new_value": new_type,
                    })

                old_is_required = field in old_required
                new_is_required = field in new_required

                if old_is_required != new_is_required:
                    changes.append({
                        "change_type": "request_field_required_changed",
                        "endpoint": endpoint,
                        "parameter": field,
                        "old_value": old_is_required,
                        "new_value": new_is_required,
                    })

    return changes


def get_response_schema(operation):
    responses = operation.get("responses", {})

    response = responses.get("200")

    if not response:
        response = responses.get("201")

    if not response:
        return {}

    content = response.get("content", {})
    json_content = content.get("application/json", {})

    return json_content.get("schema", {})


def compare_responses(old_api, new_api):
    changes = []

    old_paths = old_api.get("paths", {})
    new_paths = new_api.get("paths", {})

    common_paths = old_paths.keys() & new_paths.keys()

    for path in common_paths:
        old_methods = old_paths[path]
        new_methods = new_paths[path]

        common_methods = old_methods.keys() & new_methods.keys()

        for method in common_methods:
            if method.lower() not in HTTP_METHODS:
                continue

            old_operation = old_methods[method]
            new_operation = new_methods[method]

            old_schema = get_response_schema(old_operation)
            new_schema = get_response_schema(new_operation)

            old_properties = old_schema.get("properties", {})
            new_properties = new_schema.get("properties", {})

            old_required = set(old_schema.get("required", []))
            new_required = set(new_schema.get("required", []))

            old_fields = set(old_properties)
            new_fields = set(new_properties)

            endpoint = f"{method.upper()} {path}"

            for field in sorted(new_fields - old_fields):
                changes.append({
                    "change_type": (
                        "response_field_added_required"
                        if field in new_required
                        else "response_field_added_optional"
                    ),
                    "endpoint": endpoint,
                    "parameter": field,
                    "old_value": None,
                    "new_value": new_properties[field],
                })

            for field in sorted(old_fields - new_fields):
                changes.append({
                    "change_type": "response_field_removed",
                    "endpoint": endpoint,
                    "parameter": field,
                    "old_value": old_properties[field],
                    "new_value": None,
                })

            for field in sorted(old_fields & new_fields):
                old_type = old_properties[field].get("type")
                new_type = new_properties[field].get("type")

                if old_type != new_type:
                    changes.append({
                        "change_type": "response_field_type_changed",
                        "endpoint": endpoint,
                        "parameter": field,
                        "old_value": old_type,
                        "new_value": new_type,
                    })

                old_is_required = field in old_required
                new_is_required = field in new_required

                if old_is_required != new_is_required:
                    changes.append({
                        "change_type": "response_field_required_changed",
                        "endpoint": endpoint,
                        "parameter": field,
                        "old_value": old_is_required,
                        "new_value": new_is_required,
                    })

    return changes


def _operation_pairs(old_spec: dict[str, Any], new_spec: dict[str, Any]):
    old_paths = old_spec.get("paths", {})
    new_paths = new_spec.get("paths", {})
    for path in sorted(set(old_paths) & set(new_paths)):
        old_operations = old_paths[path].get("operations", {})
        new_operations = new_paths[path].get("operations", {})
        for method in sorted(set(old_operations) & set(new_operations)):
            yield path, method, old_operations[method], new_operations[method]


def _endpoint_for(method: str, path: str) -> str:
    return f"{method.upper()} {path}"


def _compare_normalized_endpoints(old_spec, new_spec):
    changes = []
    old_endpoints = {
        (path, method)
        for path, data in old_spec.get("paths", {}).items()
        for method in data.get("operations", {})
    }
    new_endpoints = {
        (path, method)
        for path, data in new_spec.get("paths", {}).items()
        for method in data.get("operations", {})
    }

    for path, method in sorted(new_endpoints - old_endpoints):
        endpoint = _endpoint_for(method, path)
        changes.append(make_change(
            "endpoint_added",
            endpoint,
            method=method,
            direction="endpoint",
            old_value=None,
            new_value=endpoint,
        ))

    for path, method in sorted(old_endpoints - new_endpoints):
        endpoint = _endpoint_for(method, path)
        changes.append(make_change(
            "endpoint_removed",
            endpoint,
            method=method,
            direction="endpoint",
            old_value=endpoint,
            new_value=None,
        ))

    return changes


def _compare_normalized_parameters(old_spec, new_spec):
    changes = []
    for path, method, old_operation, new_operation in _operation_pairs(old_spec, new_spec):
        endpoint = _endpoint_for(method, path)
        old_parameters = old_operation.get("parameters", {})
        new_parameters = new_operation.get("parameters", {})

        for key in sorted(set(new_parameters) - set(old_parameters)):
            parameter = new_parameters[key]
            changes.append(make_change(
                "parameter_added_required" if parameter.get("required") else "parameter_added_optional",
                endpoint,
                method=method,
                direction="request",
                location=parameter.get("in"),
                parameter=parameter.get("name"),
                old_value=None,
                new_value=parameter,
            ))

        for key in sorted(set(old_parameters) - set(new_parameters)):
            parameter = old_parameters[key]
            changes.append(make_change(
                "parameter_removed",
                endpoint,
                method=method,
                direction="request",
                location=parameter.get("in"),
                parameter=parameter.get("name"),
                old_value=parameter,
                new_value=None,
            ))

        for key in sorted(set(old_parameters) & set(new_parameters)):
            old_parameter = old_parameters[key]
            new_parameter = new_parameters[key]
            name = new_parameter.get("name") or old_parameter.get("name")
            location = new_parameter.get("in") or old_parameter.get("in")
            if old_parameter.get("required") != new_parameter.get("required"):
                changes.append(make_change(
                    "parameter_required_changed",
                    endpoint,
                    method=method,
                    direction="request",
                    location=location,
                    parameter=name,
                    old_value=old_parameter.get("required"),
                    new_value=new_parameter.get("required"),
                ))

            old_schema = old_parameter.get("schema", {})
            new_schema = new_parameter.get("schema", {})
            if old_schema.get("type") != new_schema.get("type"):
                changes.append(make_change(
                    "parameter_type_changed",
                    endpoint,
                    method=method,
                    direction="request",
                    location=location,
                    parameter=name,
                    old_value=old_schema.get("type"),
                    new_value=new_schema.get("type"),
                ))

            schema_changes = compare_schema(
                old_parameter.get("schema", {}),
                new_parameter.get("schema", {}),
                endpoint,
                method=method,
                direction="request",
                location=location or "parameter",
                schema_path=f"$.parameters.{name}",
            )
            changes.extend([
                change for change in schema_changes
                if change["change_type"] != "schema_type_changed"
            ])

    return changes


def _compare_content(old_content, new_content, endpoint, method, direction, status=None):
    changes = []
    old_media = set(old_content)
    new_media = set(new_content)
    location = f"response:{status}" if status else "requestBody"

    for media_type in sorted(new_media - old_media):
        changes.append(make_change(
            f"{direction}_media_type_added",
            endpoint,
            method=method,
            direction=direction,
            location=location,
            parameter=media_type,
            old_value=None,
            new_value=new_content[media_type],
        ))

    for media_type in sorted(old_media - new_media):
        changes.append(make_change(
            f"{direction}_media_type_removed",
            endpoint,
            method=method,
            direction=direction,
            location=location,
            parameter=media_type,
            old_value=old_content[media_type],
            new_value=None,
        ))

    for media_type in sorted(old_media & new_media):
        changes.extend(compare_schema(
            old_content[media_type].get("schema", {}),
            new_content[media_type].get("schema", {}),
            endpoint,
            method=method,
            direction=direction,
            location=f"{location}:{media_type}",
        ))

    return changes


def _compare_request_bodies(old_spec, new_spec):
    changes = []
    for path, method, old_operation, new_operation in _operation_pairs(old_spec, new_spec):
        endpoint = _endpoint_for(method, path)
        old_body = old_operation.get("requestBody", {})
        new_body = new_operation.get("requestBody", {})
        if old_body.get("required", False) != new_body.get("required", False):
            changes.append(make_change(
                "request_body_required_changed",
                endpoint,
                method=method,
                direction="request",
                location="requestBody",
                old_value=old_body.get("required", False),
                new_value=new_body.get("required", False),
            ))
        changes.extend(_compare_content(
            old_body.get("content", {}),
            new_body.get("content", {}),
            endpoint,
            method,
            "request",
        ))
    return changes


def _compare_responses(old_spec, new_spec):
    changes = []
    for path, method, old_operation, new_operation in _operation_pairs(old_spec, new_spec):
        endpoint = _endpoint_for(method, path)
        old_responses = old_operation.get("responses", {})
        new_responses = new_operation.get("responses", {})

        for status in sorted(set(new_responses) - set(old_responses)):
            changes.append(make_change(
                "response_status_added",
                endpoint,
                method=method,
                direction="response",
                location="response",
                parameter=status,
                old_value=None,
                new_value=new_responses[status],
            ))

        for status in sorted(set(old_responses) - set(new_responses)):
            changes.append(make_change(
                "response_status_removed",
                endpoint,
                method=method,
                direction="response",
                location="response",
                parameter=status,
                old_value=old_responses[status],
                new_value=None,
            ))

        for status in sorted(set(old_responses) & set(new_responses)):
            old_response = old_responses[status]
            new_response = new_responses[status]
            changes.extend(_compare_content(
                old_response.get("content", {}),
                new_response.get("content", {}),
                endpoint,
                method,
                "response",
                status=status,
            ))
            changes.extend(_compare_headers(
                old_response.get("headers", {}),
                new_response.get("headers", {}),
                endpoint,
                method,
                status,
            ))
    return changes


def _compare_headers(old_headers, new_headers, endpoint, method, status):
    changes = []
    for name in sorted(set(new_headers) - set(old_headers)):
        changes.append(make_change(
            "response_header_added",
            endpoint,
            method=method,
            direction="response",
            location=f"response:{status}:header",
            parameter=name,
            old_value=None,
            new_value=new_headers[name],
        ))
    for name in sorted(set(old_headers) - set(new_headers)):
        changes.append(make_change(
            "response_header_removed",
            endpoint,
            method=method,
            direction="response",
            location=f"response:{status}:header",
            parameter=name,
            old_value=old_headers[name],
            new_value=None,
        ))
    for name in sorted(set(old_headers) & set(new_headers)):
        old_header = old_headers[name]
        new_header = new_headers[name]
        if old_header.get("required") != new_header.get("required"):
            changes.append(make_change(
                "response_header_required_changed",
                endpoint,
                method=method,
                direction="response",
                location=f"response:{status}:header",
                parameter=name,
                old_value=old_header.get("required"),
                new_value=new_header.get("required"),
            ))
        changes.extend(compare_schema(
            old_header.get("schema", {}),
            new_header.get("schema", {}),
            endpoint,
            method=method,
            direction="response",
            location=f"response:{status}:header",
            schema_path=f"$.headers.{name}",
        ))
    return changes


def _compare_security(old_spec, new_spec):
    changes = []
    if old_spec.get("security") != new_spec.get("security"):
        changes.append(make_change(
            "global_security_changed",
            "GLOBAL",
            direction="security",
            location="global",
            old_value=old_spec.get("security"),
            new_value=new_spec.get("security"),
        ))

    old_schemes = old_spec.get("components", {}).get("securitySchemes", {})
    new_schemes = new_spec.get("components", {}).get("securitySchemes", {})
    for name in sorted(set(new_schemes) - set(old_schemes)):
        changes.append(make_change(
            "security_scheme_added",
            "GLOBAL",
            direction="security",
            location="components.securitySchemes",
            parameter=name,
            old_value=None,
            new_value=new_schemes[name],
        ))
    for name in sorted(set(old_schemes) - set(new_schemes)):
        changes.append(make_change(
            "security_scheme_removed",
            "GLOBAL",
            direction="security",
            location="components.securitySchemes",
            parameter=name,
            old_value=old_schemes[name],
            new_value=None,
        ))
    for name in sorted(set(old_schemes) & set(new_schemes)):
        if old_schemes[name] != new_schemes[name]:
            changes.append(make_change(
                "security_scheme_changed",
                "GLOBAL",
                direction="security",
                location="components.securitySchemes",
                parameter=name,
                old_value=old_schemes[name],
                new_value=new_schemes[name],
            ))

    for path, method, old_operation, new_operation in _operation_pairs(old_spec, new_spec):
        if old_operation.get("security") != new_operation.get("security"):
            endpoint = _endpoint_for(method, path)
            changes.append(make_change(
                "operation_security_changed",
                endpoint,
                method=method,
                direction="security",
                location="operation",
                old_value=old_operation.get("security"),
                new_value=new_operation.get("security"),
            ))

    return changes


def _compare_metadata(old_spec, new_spec):
    changes = []
    for path, method, old_operation, new_operation in _operation_pairs(old_spec, new_spec):
        endpoint = _endpoint_for(method, path)
        for key in ("operationId", "deprecated", "summary", "description", "tags", "servers", "callbacks"):
            if old_operation.get(key) != new_operation.get(key):
                changes.append(make_change(
                    f"operation_{key}_changed",
                    endpoint,
                    method=method,
                    direction="metadata",
                    location="operation",
                    old_value=old_operation.get(key),
                    new_value=new_operation.get(key),
                    category="metadata",
                    confidence=1.0,
                ))
    return changes


def deduplicate_changes(changes: list[APIChange]) -> list[APIChange]:
    by_id: dict[str, APIChange] = {}
    for change in changes:
        by_id.setdefault(change.change_id or change.compute_change_id(), change)
    return sorted(
        by_id.values(),
        key=lambda item: (
            item.endpoint,
            item.method or "",
            item.change_type,
            item.location or "",
            item.parameter or "",
            item.schema_path or "",
        ),
    )


def compare_api_specs(old_api, new_api) -> list[APIChange]:
    old_spec = normalize_openapi_spec(old_api)
    new_spec = normalize_openapi_spec(new_api)

    raw_changes = []
    raw_changes.extend(_compare_normalized_endpoints(old_spec, new_spec))
    raw_changes.extend(_compare_normalized_parameters(old_spec, new_spec))
    raw_changes.extend(_compare_request_bodies(old_spec, new_spec))
    raw_changes.extend(_compare_responses(old_spec, new_spec))
    raw_changes.extend(_compare_security(old_spec, new_spec))
    raw_changes.extend(_compare_metadata(old_spec, new_spec))

    return deduplicate_changes([
        APIChange(**change)
        for change in raw_changes
    ])
