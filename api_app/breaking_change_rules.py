def classify_change(change):
    change_type = change.change_type
    direction = getattr(change, "direction", "unknown")
    old_value = getattr(change, "old_value", None)
    new_value = getattr(change, "new_value", None)

    result = _classify_change_type(change_type, direction, old_value, new_value)

    if hasattr(change, "compatibility"):
        compatibility = result["classification"]
        if compatibility == "potentially-breaking":
            compatibility = "potentially-breaking"
        change.compatibility = compatibility
        change.severity = result["severity"]

    return result


def _classify_change_type(change_type, direction, old_value, new_value):

    if change_type == "endpoint_removed":
        return {
            "classification": "breaking",
            "severity": "high"
        }

    if change_type == "endpoint_added":
        return {
            "classification": "non-breaking",
            "severity": "low"
        }

    if change_type == "parameter_required_changed":
        if old_value is False and new_value is True:
            return {
                "classification": "breaking",
                "severity": "high"
            }

        return {
            "classification": "non-breaking",
            "severity": "low"
        }

    if change_type == "parameter_added_required":
        return {
            "classification": "breaking",
            "severity": "high"
        }

    if change_type == "parameter_added_optional":
        return {
            "classification": "non-breaking",
            "severity": "low"
        }

    if change_type == "parameter_removed":
        return {
            "classification": "breaking",
            "severity": "medium"
        }

    if change_type == "parameter_type_changed":
        return {
            "classification": "breaking",
            "severity": "high"
        }

    if change_type == "request_field_added_required":
        return {
            "classification": "breaking",
            "severity": "high"
        }

    if change_type == "request_field_added_optional":
        return {
            "classification": "non-breaking",
            "severity": "low"
        }

    if change_type == "request_field_removed":
        return {
            "classification": "breaking",
            "severity": "high"
        }

    if change_type == "request_field_type_changed":
        return {
            "classification": "breaking",
            "severity": "high"
        }

    if change_type == "request_field_required_changed":
        if old_value is False and new_value is True:
            return {
                "classification": "breaking",
                "severity": "high"
            }

        return {
            "classification": "non-breaking",
            "severity": "low"
        }

    if change_type == "response_field_removed":
        return {
            "classification": "breaking",
            "severity": "high"
        }

    if change_type == "response_field_added_required":
        return {
            "classification": "non-breaking",
            "severity": "low"
        }

    if change_type == "response_field_added_optional":
        return {
            "classification": "non-breaking",
            "severity": "low"
        }

    if change_type == "response_field_type_changed":
        return {
            "classification": "breaking",
            "severity": "high"
        }

    if change_type == "response_field_required_changed":
        if old_value is True and new_value is False:
            return {
                "classification": "non-breaking",
                "severity": "low"
            }

        return {
            "classification": "breaking",
            "severity": "high"
        }

    if change_type in {
        "request_media_type_removed",
        "response_media_type_removed",
        "response_status_removed",
        "response_header_removed",
        "security_scheme_removed",
        "security_scheme_changed",
        "operation_security_changed",
        "global_security_changed",
    }:
        return {
            "classification": "breaking",
            "severity": "high"
        }

    if change_type in {
        "request_media_type_added",
        "response_media_type_added",
        "response_status_added",
        "response_header_added",
        "security_scheme_added",
    }:
        return {
            "classification": "non-breaking",
            "severity": "low"
        }

    if change_type == "request_body_required_changed":
        if old_value is False and new_value is True:
            return {
                "classification": "breaking",
                "severity": "high"
            }
        return {
            "classification": "non-breaking",
            "severity": "low"
        }

    if change_type == "enum_values_removed":
        return {
            "classification": "breaking",
            "severity": "high" if direction == "request" else "medium"
        }

    if change_type == "enum_values_added":
        return {
            "classification": "non-breaking" if direction == "request" else "potentially-breaking",
            "severity": "low" if direction == "request" else "medium"
        }

    if change_type == "enum_changed":
        return {
            "classification": "breaking",
            "severity": "high"
        }

    if change_type.startswith("schema_") and change_type.endswith("_changed"):
        if change_type in {"schema_description_changed", "schema_default_changed"}:
            return {
                "classification": "non-breaking",
                "severity": "low"
            }

        if change_type == "schema_constraint_changed":
            relation = None
            if isinstance(new_value, dict):
                relation = new_value.get("relation")
            if direction == "request" and relation in {"tightened", "added"}:
                return {
                    "classification": "breaking",
                    "severity": "high"
                }
            if direction == "response" and relation in {"relaxed", "removed"}:
                return {
                    "classification": "potentially-breaking",
                    "severity": "medium"
                }
            return {
                "classification": "non-breaking",
                "severity": "low"
            }

        return {
            "classification": "breaking",
            "severity": "high"
        }

    if change_type.startswith("operation_"):
        if change_type == "operation_deprecated_changed" and new_value is True:
            return {
                "classification": "potentially-breaking",
                "severity": "medium"
            }
        return {
            "classification": "non-breaking",
            "severity": "low"
        }

    return {
        "classification": "non-breaking",
        "severity": "low"
    }
