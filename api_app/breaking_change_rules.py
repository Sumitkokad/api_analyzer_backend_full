from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------

# Bump this whenever compatibility behavior changes.
RULES_VERSION = "2026.09.1"


Classification = Literal[
    "breaking",
    "non-breaking",
    "potentially-breaking",
]

Severity = Literal[
    "low",
    "medium",
    "high",
]


# ---------------------------------------------------------------------------
# Rule result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    classification: Classification
    severity: Severity
    reason: str
    remediation_hint: str
    flags: list[str] = field(default_factory=list)
    rules_version: str = RULES_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Known rule/change types
# ---------------------------------------------------------------------------

RULE_CHANGE_TYPES = {
    # Endpoint
    "endpoint_removed",
    "endpoint_added",
    "path_parameter_renamed",
    "endpoint_trailing_slash_changed",

    # Parameters
    "parameter_added_required",
    "parameter_added_optional",
    "parameter_removed",
    "parameter_required_changed",
    "parameter_type_changed",
    "parameter_style_changed",

    # Request body
    "request_body_required_changed",
    "request_media_type_removed",
    "request_media_type_added",
    "request_field_added_required",
    "request_field_added_optional",
    "request_field_removed",
    "request_field_type_changed",
    "request_field_required_changed",

    # Responses
    "response_status_removed",
    "response_status_added",
    "response_media_type_removed",
    "response_media_type_added",
    "response_field_removed",
    "response_field_added_optional",
    "response_field_added_required",
    "response_field_type_changed",
    "response_field_required_changed",
    "response_header_removed",
    "response_header_added",
    "response_header_required_changed",

    # Schema
    "schema_constraint_changed",
    "schema_type_changed",
    "schema_format_changed",
    "schema_nullable_changed",
    "schema_default_changed",
    "schema_readOnly_changed",
    "schema_writeOnly_changed",
    "schema_discriminator_changed",
    "schema_title_changed",
    "schema_description_changed",
    "schema_examples_changed",
    "schema_composition_changed",

    # Enum
    "enum_values_added",
    "enum_values_removed",
    "enum_changed",

    # Security
    "security_scheme_added",
    "security_scheme_removed",
    "security_scheme_changed",
    "global_security_changed",
    "operation_security_changed",

    # Metadata
    "operation_operationId_changed",
    "operation_summary_changed",
    "operation_description_changed",
    "operation_tags_changed",
    "operation_servers_changed",
    "operation_callbacks_changed",
    "operation_deprecated_changed",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get(change: Any, name: str, default: Any = None) -> Any:
    if isinstance(change, dict):
        return change.get(name, default)
    return getattr(change, name, default)


def _normalise(value: Any) -> Any:
    """
    Convert Pydantic/model/list/set values into deterministic Python values.
    """
    if isinstance(value, dict):
        return {
            str(key): _normalise(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }

    if isinstance(value, (list, tuple, set, frozenset)):
        return [_normalise(item) for item in value]

    return value


def _relation_from_change(change: Any) -> str | None:
    """
    Relation may eventually exist directly on APIChange.

    During migration, also support the current schema_diff convention where
    relation is nested inside new_value.
    """
    direct = _get(change, "relation")
    if direct:
        return str(direct)

    new_value = _get(change, "new_value")

    if isinstance(new_value, dict):
        relation = new_value.get("relation")
        if relation:
            return str(relation)

    return None


def _keyword_from_change(change: Any) -> str | None:
    direct = _get(change, "keyword")
    if direct:
        return str(direct)

    new_value = _get(change, "new_value")
    old_value = _get(change, "old_value")

    if isinstance(new_value, dict) and new_value.get("constraint"):
        return str(new_value["constraint"])

    if isinstance(old_value, dict) and old_value.get("constraint"):
        return str(old_value["constraint"])

    return None


def _direction(change: Any) -> str:
    return str(_get(change, "direction", "unknown") or "unknown").lower()


def _old_new(change: Any) -> tuple[Any, Any]:
    return _get(change, "old_value"), _get(change, "new_value")


def _rule(
    *,
    rule_id: str,
    classification: Classification,
    severity: Severity,
    reason: str,
    remediation_hint: str,
    flags: list[str] | None = None,
) -> RuleResult:
    return RuleResult(
        rule_id=rule_id,
        classification=classification,
        severity=severity,
        reason=reason,
        remediation_hint=remediation_hint,
        flags=list(flags or []),
    )


def _unknown_rule(change: Any, extra_reason: str = "") -> RuleResult:
    change_type = str(_get(change, "change_type", "unknown") or "unknown")
    endpoint = str(_get(change, "endpoint", "") or "")

    logger.warning(
        "Unknown API compatibility change_type=%s endpoint=%s",
        change_type,
        endpoint,
    )

    reason = (
        f"Change type '{change_type}' has no explicit compatibility rule. "
        "The analyzer cannot prove that the change is safe."
    )

    if extra_reason:
        reason = f"{reason} {extra_reason}"

    return _rule(
        rule_id=change_type,
        classification="potentially-breaking",
        severity="medium",
        reason=reason,
        remediation_hint=(
            "Review the change manually and add an explicit deterministic rule "
            "before treating it as a guaranteed compatible change."
        ),
        flags=["unknown_change_type"],
    )


def _derive_type_relation(old_value: Any, new_value: Any) -> str:
    """
    Determine a useful relation for common OpenAPI type changes.

    Request-side widening:
        integer -> number
    is considered safe.

    Everything else defaults to changed/unknown and is handled conservatively.
    """
    old_type = old_value
    new_type = new_value

    if isinstance(old_value, dict):
        old_type = old_value.get("type", old_value.get("value"))

    if isinstance(new_value, dict):
        new_type = new_value.get("type", new_value.get("value"))

    old_type = old_type.lower() if isinstance(old_type, str) else old_type
    new_type = new_type.lower() if isinstance(new_type, str) else new_type

    if old_type == new_type:
        return "changed"

    if old_type == "integer" and new_type == "number":
        return "widened"

    # Handle 3.1-style type unions when they arrive as lists.
    if isinstance(old_type, list) and isinstance(new_type, list):
        old_set = set(old_type)
        new_set = set(new_type)

        if old_set < new_set:
            return "widened"

        if new_set < old_set:
            return "narrowed"

    return "changed"


def _bool_transition(
    old_value: Any,
    new_value: Any,
) -> str:
    if old_value is False and new_value is True:
        return "tightened"
    if old_value is True and new_value is False:
        return "relaxed"
    return "changed"


def _security_relation(change: Any) -> str | None:
    """
    Prefer explicit relation from the diff engine.

    Fallback heuristics are conservative.
    """
    explicit = _relation_from_change(change)
    if explicit:
        return explicit

    old_value, new_value = _old_new(change)

    if old_value is None and new_value is not None:
        return "requirement_added"

    if old_value is not None and new_value is None:
        return "requirement_removed"

    return None


def _is_only_description_change(
    old_value: Any,
    new_value: Any,
) -> bool:
    if not isinstance(old_value, dict) or not isinstance(new_value, dict):
        return False

    old_copy = dict(old_value)
    new_copy = dict(new_value)

    old_copy.pop("description", None)
    new_copy.pop("description", None)

    return old_copy == new_copy


# ---------------------------------------------------------------------------
# Parameter rules
# ---------------------------------------------------------------------------

def _parameter_required_rule(change: Any) -> RuleResult:
    old_value, new_value = _old_new(change)

    if old_value is False and new_value is True:
        return _rule(
            rule_id="parameter_required_changed",
            classification="breaking",
            severity="high",
            reason="A previously optional parameter is now required.",
            remediation_hint=(
                "Keep the parameter optional or provide a compatible default "
                "before releasing the new contract."
            ),
        )

    if old_value is True and new_value is False:
        return _rule(
            rule_id="parameter_required_changed",
            classification="non-breaking",
            severity="low",
            reason="A previously required parameter is now optional.",
            remediation_hint=(
                "No client migration is normally required; update documentation "
                "and contract tests as appropriate."
            ),
        )

    return _unknown_rule(
        change,
        "The requiredness transition is not a recognized boolean change.",
    )


def _parameter_type_rule(change: Any) -> RuleResult:
    old_value, new_value = _old_new(change)
    relation = _relation_from_change(change) or _derive_type_relation(
        old_value,
        new_value,
    )

    if relation == "widened":
        return _rule(
            rule_id="parameter_type_changed:widened",
            classification="non-breaking",
            severity="low",
            reason=(
                "The parameter type was widened in a way that is compatible "
                "with the previous accepted value domain."
            ),
            remediation_hint=(
                "Keep contract tests for existing client values and verify "
                "server-side coercion behavior."
            ),
        )

    return _rule(
        rule_id="parameter_type_changed",
        classification="breaking",
        severity="high",
        reason="The parameter type changed and compatibility cannot be preserved safely.",
        remediation_hint=(
            "Preserve the original accepted type or introduce a compatible "
            "migration/versioning strategy."
        ),
    )


# ---------------------------------------------------------------------------
# Request schema rules
# ---------------------------------------------------------------------------

def _request_type_rule(change: Any, rule_id: str) -> RuleResult:
    old_value, new_value = _old_new(change)
    relation = _relation_from_change(change) or _derive_type_relation(
        old_value,
        new_value,
    )

    if relation in {"widened", "relaxed", "removed"}:
        return _rule(
            rule_id=f"{rule_id}:{relation}",
            classification="non-breaking",
            severity="low",
            reason=(
                "The request-side schema became more permissive, so existing "
                "client values remain accepted."
            ),
            remediation_hint=(
                "Keep compatibility tests covering values accepted by the "
                "previous contract."
            ),
        )

    if relation in {"narrowed", "tightened", "added"}:
        return _rule(
            rule_id=f"{rule_id}:{relation}",
            classification="breaking",
            severity="high",
            reason=(
                "The request-side schema became more restrictive, so previously "
                "valid client input may now be rejected."
            ),
            remediation_hint=(
                "Preserve the previous accepted value domain or introduce a "
                "compatible version/migration."
            ),
        )

    if rule_id in {
        "request_field_type_changed",
        "parameter_type_changed",
    }:
        return _rule(
            rule_id=rule_id,
            classification="breaking",
            severity="high",
            reason="The request type changed and no safe widening was proven.",
            remediation_hint=(
                "Keep the previous type contract or provide a compatible migration."
            ),
        )

    return _unknown_rule(
        change,
        "The request schema relation could not be determined.",
    )


def _request_nullable_rule(change: Any) -> RuleResult:
    old_value, new_value = _old_new(change)

    if old_value is False and new_value is True:
        return _rule(
            rule_id="schema_nullable_changed:added",
            classification="non-breaking",
            severity="low",
            reason="The request schema now additionally permits null values.",
            remediation_hint="Keep existing validation tests for non-null values.",
        )

    if old_value is True and new_value is False:
        return _rule(
            rule_id="schema_nullable_changed:removed",
            classification="breaking",
            severity="high",
            reason="The request schema no longer permits null values.",
            remediation_hint=(
                "Continue accepting null or introduce a compatibility migration."
            ),
        )

    return _unknown_rule(change)


def _request_enum_rule(change: Any) -> RuleResult:
    change_type = _get(change, "change_type", "")
    if change_type == "enum_values_added":
        return _rule(
            rule_id="enum_values_added:request",
            classification="non-breaking",
            severity="low",
            reason="The request enum accepts additional values.",
            remediation_hint=(
                "Keep clients using existing values; update generated models if needed."
            ),
        )

    if change_type in {"enum_values_removed", "enum_changed"}:
        return _rule(
            rule_id=f"{change_type}:request",
            classification="breaking",
            severity="high",
            reason="The request enum no longer accepts one or more previously valid values.",
            remediation_hint=(
                "Restore the removed values or provide a migration/version transition."
            ),
        )

    return _unknown_rule(change)


# ---------------------------------------------------------------------------
# Response schema rules
# ---------------------------------------------------------------------------

def _response_type_rule(change: Any, rule_id: str) -> RuleResult:
    relation = _relation_from_change(change) or _derive_type_relation(
        *_old_new(change)
    )

    # Target specification: any response type change is treated conservatively
    # as breaking even when the direction appears widened.
    return _rule(
        rule_id=rule_id if relation == "unknown" else f"{rule_id}:{relation}",
        classification="breaking",
        severity="high",
        reason=(
            "A response type changed, which can break existing client parsers "
            "or generated SDK models."
        ),
        remediation_hint=(
            "Preserve the existing response type or introduce a compatible "
            "version/migration."
        ),
    )


def _response_nullable_rule(change: Any) -> RuleResult:
    old_value, new_value = _old_new(change)

    if old_value is False and new_value is True:
        return _rule(
            rule_id="schema_nullable_changed:response_added",
            classification="breaking",
            severity="medium",
            reason=(
                "A response field that was previously non-null may now return null."
            ),
            remediation_hint=(
                "Ensure existing clients safely handle null before enabling the change."
            ),
        )

    if old_value is True and new_value is False:
        return _rule(
            rule_id="schema_nullable_changed:response_removed",
            classification="non-breaking",
            severity="low",
            reason=(
                "A response field that could previously be null is now guaranteed "
                "to be non-null."
            ),
            remediation_hint="Keep response contract tests covering existing clients.",
        )

    return _unknown_rule(change)


def _response_enum_rule(change: Any) -> RuleResult:
    change_type = _get(change, "change_type", "")

    if change_type == "enum_values_added":
        return _rule(
            rule_id="enum_values_added:response",
            classification="potentially-breaking",
            severity="medium",
            reason=(
                "The response enum now contains additional values that older "
                "clients may not understand."
            ),
            remediation_hint=(
                "Verify consumers use tolerant enum handling or coordinate the rollout."
            ),
        )

    if change_type == "enum_values_removed":
        return _rule(
            rule_id="enum_values_removed:response",
            classification="non-breaking",
            severity="low",
            reason=(
                "The response no longer emits some previously possible enum values."
            ),
            remediation_hint="Confirm removed values are not required by consumers.",
        )

    return _rule(
        rule_id=f"{change_type}:response",
        classification="potentially-breaking",
        severity="medium",
        reason="The response enum changed and consumer assumptions may be affected.",
        remediation_hint="Review generated clients and explicit enum handling.",
    )


# ---------------------------------------------------------------------------
# Schema constraint rules
# ---------------------------------------------------------------------------

def _constraint_rule(change: Any) -> RuleResult:
    direction = _direction(change)
    relation = _relation_from_change(change)

    keyword = _keyword_from_change(change) or "constraint"
    rule_id = (
        f"schema_constraint_changed:{relation}"
        if relation
        else "schema_constraint_changed"
    )

    if direction not in {"request", "response"}:
        return _rule(
            rule_id=rule_id,
            classification="potentially-breaking",
            severity="medium",
            reason=(
                f"The '{keyword}' constraint changed, but the schema direction "
                "is unknown so compatibility cannot be proven."
            ),
            remediation_hint=(
                "Identify whether the schema is request or response data and "
                "add an explicit compatibility rule."
            ),
            flags=["unknown_direction"],
        )

    if relation is None or relation in {"changed", "unknown"}:
        return _rule(
            rule_id=rule_id,
            classification="potentially-breaking",
            severity="medium",
            reason=(
                f"The '{keyword}' constraint changed without a provable "
                "tightening/relaxation relation."
            ),
            remediation_hint=(
                "Review the constraint change manually or enrich schema_diff "
                "with a deterministic relation."
            ),
            flags=["unknown_relation"],
        )

    if direction == "request":
        if relation in {"relaxed", "removed"}:
            return _rule(
                rule_id=rule_id,
                classification="non-breaking",
                severity="low",
                reason=(
                    f"The request '{keyword}' constraint was relaxed or removed, "
                    "so previously valid client input remains accepted."
                ),
                remediation_hint=(
                    "Keep compatibility tests for previously valid inputs."
                ),
            )

        if relation in {"tightened", "added", "narrowed"}:
            return _rule(
                rule_id=rule_id,
                classification="breaking",
                severity="high",
                reason=(
                    f"The request '{keyword}' constraint became more restrictive."
                ),
                remediation_hint=(
                    "Preserve the previous accepted input range or version the change."
                ),
            )

    if direction == "response":
        if relation in {"tightened", "added", "narrowed"}:
            return _rule(
                rule_id=rule_id,
                classification="potentially-breaking",
                severity="medium",
                reason=(
                    f"The response '{keyword}' constraint became more restrictive. "
                    "Consumers may depend on the previous broader range."
                ),
                remediation_hint=(
                    "Review consumer assumptions and generated schemas before rollout."
                ),
            )

        if relation in {"relaxed", "removed"}:
            return _rule(
                rule_id=rule_id,
                classification="non-breaking",
                severity="low",
                reason=(
                    f"The response '{keyword}' constraint was relaxed or removed."
                ),
                remediation_hint=(
                    "Verify that the consumer behavior remains correct for the broader domain."
                ),
            )

    return _unknown_rule(
        change,
        "The schema relation/direction combination is not explicitly supported.",
    )


# ---------------------------------------------------------------------------
# AdditionalProperties
# ---------------------------------------------------------------------------

def _additional_properties_rule(change: Any) -> RuleResult:
    direction = _direction(change)
    old_value, new_value = _old_new(change)

    old_value = _extract_constraint_value(old_value)
    new_value = _extract_constraint_value(new_value)

    # Only handle simple boolean form deterministically here.
    if isinstance(old_value, bool) and isinstance(new_value, bool):
        if direction == "request":
            if old_value is False and new_value is True:
                return _rule(
                    rule_id="schema_additionalProperties_changed:relaxed",
                    classification="non-breaking",
                    severity="low",
                    reason=(
                        "The request object now accepts additional properties."
                    ),
                    remediation_hint="Keep contract tests for existing request fields.",
                )

            if old_value is True and new_value is False:
                return _rule(
                    rule_id="schema_additionalProperties_changed:tightened",
                    classification="breaking",
                    severity="high",
                    reason=(
                        "The request object now rejects previously accepted extra properties."
                    ),
                    remediation_hint=(
                        "Continue accepting existing extra fields or version the contract."
                    ),
                )

        if direction == "response":
            if old_value is False and new_value is True:
                return _rule(
                    rule_id="schema_additionalProperties_changed:response_relaxed",
                    classification="potentially-breaking",
                    severity="medium",
                    reason=(
                        "The response may now contain additional properties."
                    ),
                    remediation_hint=(
                        "Verify tolerant-reader behavior in all consumers."
                    ),
                )

            if old_value is True and new_value is False:
                return _rule(
                    rule_id="schema_additionalProperties_changed:response_tightened",
                    classification="non-breaking",
                    severity="low",
                    reason=(
                        "The response now guarantees that extra properties are not emitted."
                    ),
                    remediation_hint="Keep consumer contract tests.",
                )

    return _constraint_rule(change)


def _extract_constraint_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

def _security_rule(change: Any) -> RuleResult:
    change_type = str(_get(change, "change_type", "unknown"))
    relation = _security_relation(change)

    old_value, new_value = _old_new(change)

    if change_type == "security_scheme_added":
        return _rule(
            rule_id="security_scheme_added",
            classification="non-breaking",
            severity="low",
            reason="A new security scheme was added without removing existing schemes.",
            remediation_hint=(
                "Review whether new endpoints or operations require the scheme."
            ),
        )

    if change_type == "security_scheme_removed":
        return _rule(
            rule_id="security_scheme_removed",
            classification="breaking",
            severity="high",
            reason="An existing security scheme was removed.",
            remediation_hint=(
                "Preserve the existing scheme or coordinate client authentication migration."
            ),
        )

    if change_type == "security_scheme_changed":
        if _is_only_description_change(old_value, new_value):
            return _rule(
                rule_id="security_scheme_changed:description_only",
                classification="non-breaking",
                severity="low",
                reason="Only the security scheme description changed.",
                remediation_hint="No compatibility migration is normally required.",
                flags=["informational"],
            )

        return _rule(
            rule_id="security_scheme_changed",
            classification="breaking",
            severity="high",
            reason=(
                "A functional security scheme property changed, which can prevent "
                "existing clients from authenticating."
            ),
            remediation_hint=(
                "Preserve the existing authentication behavior or coordinate client migration."
            ),
        )

    if relation in {
        "requirement_added",
        "scope_added",
        "scheme_replaced",
    }:
        return _rule(
            rule_id=f"{change_type}:{relation}",
            classification="breaking",
            severity="high",
            reason="Security requirements became stricter for an existing operation.",
            remediation_hint=(
                "Verify all existing clients can satisfy the new authentication requirements."
            ),
        )

    if relation in {
        "requirement_removed",
        "scope_removed",
    }:
        return _rule(
            rule_id=f"{change_type}:{relation}",
            classification="non-breaking",
            severity="low",
            reason="Security requirements became less restrictive.",
            remediation_hint=(
                "Review the security implications and confirm that the relaxation is intentional."
            ),
            flags=["security_review"],
        )

    return _rule(
        rule_id=change_type,
        classification="potentially-breaking",
        severity="medium",
        reason=(
            "Security behavior changed, but the exact tightening/relaxation relation "
            "could not be proven."
        ),
        remediation_hint=(
            "Review the security requirement change manually before release."
        ),
        flags=["unknown_security_relation"],
    )


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def _metadata_rule(change: Any) -> RuleResult:
    change_type = str(_get(change, "change_type", "unknown"))

    metadata_rules: dict[str, tuple[Classification, Severity, str, str, list[str]]] = {
        "operation_summary_changed": (
            "non-breaking",
            "low",
            "The operation summary changed and does not alter the API contract.",
            "Update documentation and generated API descriptions.",
            ["informational"],
        ),
        "operation_description_changed": (
            "non-breaking",
            "low",
            "The operation description changed and does not alter the API contract.",
            "Update documentation as appropriate.",
            ["informational"],
        ),
        "operation_tags_changed": (
            "non-breaking",
            "low",
            "Operation tags changed without changing request/response behavior.",
            "Update documentation and generated grouping if needed.",
            ["informational"],
        ),
        "operation_operationId_changed": (
            "potentially-breaking",
            "low",
            "The operationId changed and generated SDK/client method names may change.",
            "Review generated SDKs and consumer references to the operationId.",
            [],
        ),
        "operation_servers_changed": (
            "potentially-breaking",
            "medium",
            "The operation server/base URL changed and may redirect clients to a different host.",
            "Verify deployed base URLs, routing, authentication, and client configuration.",
            [],
        ),
        "operation_callbacks_changed": (
            "potentially-breaking",
            "medium",
            "The callback contract changed and callback consumers may be affected.",
            "Review callback clients and regenerate affected client models.",
            [],
        ),
    }

    if change_type == "operation_deprecated_changed":
        _, new_value = _old_new(change)

        if bool(new_value) is True:
            return _rule(
                rule_id="operation_deprecated_changed",
                classification="non-breaking",
                severity="low",
                reason="The operation was marked deprecated.",
                remediation_hint=(
                    "Notify consumers and provide a documented replacement/sunset plan."
                ),
                flags=["deprecation_notice"],
            )

        return _rule(
            rule_id="operation_deprecated_changed",
            classification="non-breaking",
            severity="low",
            reason="The operation deprecation metadata changed.",
            remediation_hint="Update consumer documentation as appropriate.",
            flags=["informational"],
        )

    rule = metadata_rules.get(change_type)

    if rule:
        classification, severity, reason, remediation, flags = rule
        return _rule(
            rule_id=change_type,
            classification=classification,
            severity=severity,
            reason=reason,
            remediation_hint=remediation,
            flags=flags,
        )

    if change_type.startswith("operation_"):
        return _rule(
            rule_id=change_type,
            classification="potentially-breaking",
            severity="medium",
            reason=(
                f"Metadata change '{change_type}' is not explicitly classified "
                "as safe."
            ),
            remediation_hint=(
                "Review the operation metadata and add a deterministic rule if needed."
            ),
            flags=["unknown_operation_metadata"],
        )

    return _unknown_rule(change)


# ---------------------------------------------------------------------------
# Required-field rules
# ---------------------------------------------------------------------------

def _field_required_rule(
    change: Any,
    *,
    direction: str,
) -> RuleResult:
    old_value, new_value = _old_new(change)
    change_type = str(_get(change, "change_type", "unknown"))

    if old_value is False and new_value is True:
        if direction == "request":
            return _rule(
                rule_id=change_type,
                classification="breaking",
                severity="high",
                reason=(
                    "A request field that was previously optional is now required."
                ),
                remediation_hint=(
                    "Keep the field optional or provide a compatible default/migration."
                ),
            )

        if direction == "response":
            return _rule(
                rule_id=change_type,
                classification="non-breaking",
                severity="low",
                reason=(
                    "A response field is now guaranteed to be present."
                ),
                remediation_hint=(
                    "Existing clients should continue to parse the response."
                ),
            )

    if old_value is True and new_value is False:
        if direction == "request":
            return _rule(
                rule_id=change_type,
                classification="non-breaking",
                severity="low",
                reason=(
                    "A previously required request field is now optional."
                ),
                remediation_hint="Keep existing request validation tests.",
            )

        if direction == "response":
            return _rule(
                rule_id=change_type,
                classification="breaking",
                severity="medium",
                reason=(
                    "A response field that was guaranteed to be present is now optional."
                ),
                remediation_hint=(
                    "Ensure clients safely handle the field being absent."
                ),
            )

    return _unknown_rule(
        change,
        "The requiredness transition could not be safely classified.",
    )


# ---------------------------------------------------------------------------
# Main evaluator
# ---------------------------------------------------------------------------

def evaluate(
    change: Any,
    *,
    response_added_required_policy: Literal[
        "tolerant_reader",
        "strict",
    ] = "tolerant_reader",
) -> RuleResult:
    """
    Pure deterministic compatibility evaluator.

    This function:
      - never calls an LLM
      - never performs I/O
      - never returns 'critical'
      - never treats unknowns as non-breaking
      - returns a versioned RuleResult
    """
    change_type = str(_get(change, "change_type", "unknown"))
    direction = _direction(change)
    old_value, new_value = _old_new(change)

    # ------------------------------------------------------------------
    # Endpoint rules
    # ------------------------------------------------------------------

    if change_type == "endpoint_removed":
        return _rule(
            rule_id="endpoint_removed",
            classification="breaking",
            severity="high",
            reason="An existing API endpoint was removed.",
            remediation_hint=(
                "Keep the endpoint available, introduce a deprecation window, "
                "or provide a versioned replacement."
            ),
        )

    if change_type == "endpoint_added":
        return _rule(
            rule_id="endpoint_added",
            classification="non-breaking",
            severity="low",
            reason="A new API endpoint was added without removing existing behavior.",
            remediation_hint="Add contract and regression tests for the new endpoint.",
        )

    if change_type == "path_parameter_renamed":
        return _rule(
            rule_id="path_parameter_renamed",
            classification="non-breaking",
            severity="low",
            reason=(
                "Only the path-template parameter name changed; the endpoint's "
                "positional route remains the same."
            ),
            remediation_hint=(
                "Update generated documentation or SDK metadata if the parameter "
                "name is surfaced to consumers."
            ),
            flags=["informational"],
        )

    if change_type == "endpoint_trailing_slash_changed":
        return _rule(
            rule_id="endpoint_trailing_slash_changed",
            classification="potentially-breaking",
            severity="low",
            reason=(
                "The endpoint trailing slash changed and framework redirect/404 "
                "behavior can differ."
            ),
            remediation_hint=(
                "Verify routing, redirects, generated clients, and reverse-proxy behavior."
            ),
        )

    # ------------------------------------------------------------------
    # Parameter rules
    # ------------------------------------------------------------------

    if change_type == "parameter_added_required":
        return _rule(
            rule_id="parameter_added_required",
            classification="breaking",
            severity="high",
            reason="A new required request parameter was introduced.",
            remediation_hint=(
                "Make the parameter optional or provide a compatible default."
            ),
        )

    if change_type == "parameter_added_optional":
        return _rule(
            rule_id="parameter_added_optional",
            classification="non-breaking",
            severity="low",
            reason="An optional request parameter was added.",
            remediation_hint="No existing-client migration is normally required.",
        )

    if change_type == "parameter_removed":
        return _rule(
            rule_id="parameter_removed",
            classification="breaking",
            severity="medium",
            reason="An existing parameter was removed.",
            remediation_hint=(
                "Keep accepting the parameter during the compatibility window "
                "or version the endpoint."
            ),
        )

    if change_type == "parameter_required_changed":
        return _parameter_required_rule(change)

    if change_type == "parameter_type_changed":
        return _parameter_type_rule(change)

    if change_type == "parameter_style_changed":
        return _rule(
            rule_id="parameter_style_changed",
            classification="potentially-breaking",
            severity="medium",
            reason=(
                "Parameter serialization style/explode/allowReserved behavior changed."
            ),
            remediation_hint=(
                "Verify how existing clients serialize and deserialize the parameter."
            ),
        )

    # ------------------------------------------------------------------
    # Request body/media
    # ------------------------------------------------------------------

    if change_type == "request_body_required_changed":
        old, new = old_value, new_value

        if old is False and new is True:
            return _rule(
                rule_id="request_body_required_changed",
                classification="breaking",
                severity="high",
                reason="The request body is now required.",
                remediation_hint=(
                    "Keep the request body optional or introduce a compatible migration."
                ),
            )

        if old is True and new is False:
            return _rule(
                rule_id="request_body_required_changed",
                classification="non-breaking",
                severity="low",
                reason="The request body is now optional.",
                remediation_hint="Existing clients should continue to work.",
            )

        return _unknown_rule(change)

    if change_type == "request_media_type_removed":
        return _rule(
            rule_id="request_media_type_removed",
            classification="breaking",
            severity="high",
            reason="A previously accepted request media type was removed.",
            remediation_hint=(
                "Continue supporting the media type or coordinate client migration."
            ),
        )

    if change_type == "request_media_type_added":
        return _rule(
            rule_id="request_media_type_added",
            classification="non-breaking",
            severity="low",
            reason="A new request media type was added.",
            remediation_hint="Existing clients may continue using the previous media type.",
        )

    if change_type in {
        "request_field_added_required",
        "request_field_added_optional",
    }:
        if change_type == "request_field_added_required":
            return _rule(
                rule_id=change_type,
                classification="breaking",
                severity="high",
                reason="A required request field was added.",
                remediation_hint=(
                    "Make the field optional or provide a compatible default."
                ),
            )

        return _rule(
            rule_id=change_type,
            classification="non-breaking",
            severity="low",
            reason="An optional request field was added.",
            remediation_hint="Existing request payloads remain valid.",
        )

    if change_type == "request_field_removed":
        return _rule(
            rule_id="request_field_removed",
            classification="breaking",
            severity="high",
            reason="An existing request field was removed.",
            remediation_hint=(
                "Continue accepting the field during the migration window "
                "or version the contract."
            ),
        )

    if change_type == "request_field_type_changed":
        return _request_type_rule(change, "request_field_type_changed")

    if change_type == "request_field_required_changed":
        return _field_required_rule(change, direction="request")

    # ------------------------------------------------------------------
    # Response rules
    # ------------------------------------------------------------------

    if change_type == "response_status_removed":
        return _rule(
            rule_id="response_status_removed",
            classification="breaking",
            severity="high",
            reason="A previously documented response status was removed.",
            remediation_hint=(
                "Keep returning the documented status or coordinate client changes."
            ),
        )

    if change_type == "response_status_added":
        return _rule(
            rule_id="response_status_added",
            classification="non-breaking",
            severity="low",
            reason="A new response status was documented.",
            remediation_hint=(
                "Confirm tolerant handling of the additional status in consumers."
            ),
        )

    if change_type == "response_media_type_removed":
        return _rule(
            rule_id="response_media_type_removed",
            classification="breaking",
            severity="high",
            reason="A previously documented response media type was removed.",
            remediation_hint=(
                "Continue supporting the media type or coordinate client migration."
            ),
        )

    if change_type == "response_media_type_added":
        return _rule(
            rule_id="response_media_type_added",
            classification="non-breaking",
            severity="low",
            reason="A new response media type was added.",
            remediation_hint=(
                "Verify clients continue to negotiate/use their existing media type."
            ),
        )

    if change_type == "response_field_removed":
        return _rule(
            rule_id="response_field_removed",
            classification="breaking",
            severity="high",
            reason="An existing response field was removed.",
            remediation_hint=(
                "Keep the field during the compatibility window or version the response."
            ),
        )

    if change_type == "response_field_type_changed":
        return _response_type_rule(change, "response_field_type_changed")

    if change_type == "response_field_added_optional":
        return _rule(
            rule_id="response_field_added_optional",
            classification="non-breaking",
            severity="low",
            reason="An optional response field was added.",
            remediation_hint=(
                "Existing tolerant-reader clients should continue to work."
            ),
        )

    if change_type == "response_field_added_required":
        if response_added_required_policy == "strict":
            return _rule(
                rule_id="response_field_added_required:strict",
                classification="breaking",
                severity="high",
                reason=(
                    "A required response field was added under strict client compatibility policy."
                ),
                remediation_hint=(
                    "Preserve the previous response shape or update all strict clients."
                ),
                flags=["strict_client_risk"],
            )

        return _rule(
            rule_id="response_field_added_required",
            classification="non-breaking",
            severity="low",
            reason=(
                "A required response field was added. Under the default tolerant-reader "
                "policy, clients are expected to ignore unknown response fields."
            ),
            remediation_hint=(
                "Verify consumers are tolerant readers and surface the risk to teams "
                "that use strict response schemas."
            ),
            flags=["strict_client_risk"],
        )

    if change_type == "response_field_required_changed":
        return _field_required_rule(change, direction="response")

    if change_type == "response_header_removed":
        return _rule(
            rule_id="response_header_removed",
            classification="breaking",
            severity="high",
            reason="A documented response header was removed.",
            remediation_hint=(
                "Keep the header or coordinate consumer migration."
            ),
        )

    if change_type == "response_header_added":
        return _rule(
            rule_id="response_header_added",
            classification="non-breaking",
            severity="low",
            reason="A response header was added.",
            remediation_hint="Verify clients tolerate additional headers.",
        )

    if change_type == "response_header_required_changed":
        return _field_required_rule(change, direction="response")

    # ------------------------------------------------------------------
    # Schema changes
    # ------------------------------------------------------------------

    if change_type == "schema_constraint_changed":
        keyword = _keyword_from_change(change)

        if keyword == "additionalProperties":
            return _additional_properties_rule(change)

        return _constraint_rule(change)

    if change_type in {
        "schema_type_changed",
    }:
        if direction == "request":
            return _request_type_rule(change, change_type)

        if direction == "response":
            return _response_type_rule(change, change_type)

        return _unknown_rule(
            change,
            "A schema type change has no known request/response direction.",
        )

    if change_type == "schema_nullable_changed":
        if direction == "request":
            return _request_nullable_rule(change)

        if direction == "response":
            return _response_nullable_rule(change)

        return _unknown_rule(
            change,
            "Nullable changes require a known request/response direction.",
        )

    if change_type == "schema_format_changed":
        relation = _relation_from_change(change)

        if direction not in {"request", "response"}:
            return _unknown_rule(
                change,
                "Format changes require a known request/response direction.",
            )

        if direction == "request":
            if relation in {"removed", "relaxed"}:
                return _rule(
                    rule_id=f"schema_format_changed:{relation}",
                    classification="non-breaking",
                    severity="low",
                    reason="The request format constraint was relaxed or removed.",
                    remediation_hint="Keep validation tests for existing client values.",
                )

            return _rule(
                rule_id="schema_format_changed",
                classification="breaking",
                severity="medium",
                reason="The request format constraint became stricter or changed.",
                remediation_hint=(
                    "Verify all previously valid request values remain accepted."
                ),
            )

        return _rule(
            rule_id="schema_format_changed",
            classification="breaking",
            severity="medium",
            reason="The response format changed and client parsing assumptions may break.",
            remediation_hint=(
                "Verify generated SDKs and consumer deserializers."
            ),
        )

    if change_type == "schema_default_changed":
        return _rule(
            rule_id="schema_default_changed",
            classification="non-breaking",
            severity="low",
            reason="The schema default value changed.",
            remediation_hint=(
                "Review application behavior where the field is omitted and defaults are applied."
            ),
            flags=["behavior_change"],
        )

    if change_type in {
        "schema_readOnly_changed",
        "schema_writeOnly_changed",
        "schema_discriminator_changed",
        "schema_composition_changed",
    }:
        return _rule(
            rule_id=change_type,
            classification="potentially-breaking",
            severity="medium",
            reason=(
                f"The schema property '{change_type}' changed and its exact "
                "consumer impact depends on generated/runtime behavior."
            ),
            remediation_hint=(
                "Review affected generated clients and contract validation behavior."
            ),
        )

    if change_type in {
        "schema_title_changed",
        "schema_description_changed",
        "schema_examples_changed",
    }:
        return _rule(
            rule_id=change_type,
            classification="non-breaking",
            severity="low",
            reason="Only schema documentation/example metadata changed.",
            remediation_hint="Update documentation or examples as appropriate.",
            flags=["informational"],
        )

    # ------------------------------------------------------------------
    # Enum rules
    # ------------------------------------------------------------------

    if change_type in {
        "enum_values_added",
        "enum_values_removed",
        "enum_changed",
    }:
        if direction == "request":
            return _request_enum_rule(change)

        if direction == "response":
            return _response_enum_rule(change)

        return _unknown_rule(
            change,
            "Enum changes require a known request/response direction.",
        )

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------

    if change_type in {
        "security_scheme_added",
        "security_scheme_removed",
        "security_scheme_changed",
        "global_security_changed",
        "operation_security_changed",
    }:
        return _security_rule(change)

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    if change_type.startswith("operation_"):
        return _metadata_rule(change)

    # ------------------------------------------------------------------
    # Explicitly unknown change type
    # ------------------------------------------------------------------

    return _unknown_rule(change)


# ---------------------------------------------------------------------------
# Backward-compatible wrapper
# ---------------------------------------------------------------------------

def classify_change(
    change: Any,
    *,
    response_added_required_policy: Literal[
        "tolerant_reader",
        "strict",
    ] = "tolerant_reader",
) -> dict[str, Any]:
    """
    Backward-compatible API.

    Existing callers can continue doing:

        result = classify_change(change)

    New code should prefer:

        result = evaluate(change)

    The compatibility/severity fields are copied onto the change object when
    those fields exist.
    """
    result = evaluate(
        change,
        response_added_required_policy=response_added_required_policy,
    )

    if isinstance(change, dict):
        change["compatibility"] = result.classification
        change["severity"] = result.severity
        change["rule_id"] = result.rule_id
        change["flags"] = list(result.flags)
        change["reason"] = result.reason
        change["remediation_hint"] = result.remediation_hint
        change["rules_version"] = result.rules_version
    else:
        _set_if_supported(change, "compatibility", result.classification)
        _set_if_supported(change, "severity", result.severity)
        _set_if_supported(change, "rule_id", result.rule_id)
        _set_if_supported(change, "flags", list(result.flags))
        _set_if_supported(change, "reason", result.reason)
        _set_if_supported(change, "remediation_hint", result.remediation_hint)
        _set_if_supported(change, "rules_version", result.rules_version)

    return {
        # Original shape
        "classification": result.classification,
        "severity": result.severity,

        # New deterministic metadata
        "rule_id": result.rule_id,
        "reason": result.reason,
        "remediation_hint": result.remediation_hint,
        "flags": list(result.flags),
        "rules_version": result.rules_version,
    }


def _set_if_supported(change: Any, field_name: str, value: Any) -> None:
    """
    Avoid breaking the current APIChange model before schemas.py is migrated.

    Once schemas.py contains all fields, these assignments become normal model
    assignments.
    """
    if hasattr(change, field_name):
        try:
            setattr(change, field_name, value)
        except Exception:
            logger.debug(
                "Could not set compatibility field '%s' on %s",
                field_name,
                type(change).__name__,
                exc_info=True,
            )


# ---------------------------------------------------------------------------
# Legacy private helper kept for compatibility
# ---------------------------------------------------------------------------

def _classify_change_type(change: Any) -> dict[str, Any]:
    """
    Older code may still import this helper.

    Keep it as a thin compatibility layer during the migration away from
    direct legacy rule access.
    """
    result = evaluate(change)
    return {
        "classification": result.classification,
        "severity": result.severity,
        "rule_id": result.rule_id,
        "reason": result.reason,
        "remediation_hint": result.remediation_hint,
        "flags": list(result.flags),
        "rules_version": result.rules_version,
    }