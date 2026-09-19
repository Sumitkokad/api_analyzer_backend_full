from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .breaking_change_rules import (
    RULES_VERSION,
    classify_change as deterministic_classify_change,
    evaluate as evaluate_change,
)


ClassificationType = Literal[
    "breaking",
    "non-breaking",
    "potentially-breaking",
]

SeverityType = Literal[
    "low",
    "medium",
    "high",
]


class ChangeClassification(BaseModel):
    """
    Backward-compatible classification model.

    Classification and severity always come from the deterministic
    breaking_change_rules engine. No LLM is used here.
    """

    model_config = ConfigDict(extra="allow")

    classification: ClassificationType
    severity: SeverityType
    reason: str = ""
    rule_id: str | None = None
    remediation_hint: str | None = None
    flags: list[str] = []
    rules_version: str = RULES_VERSION

    def __getitem__(self, key: str) -> Any:
        """
        Compatibility with legacy code that treated classification
        results like dictionaries.
        """
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


def classify_change(
    change: dict[str, Any] | Any,
    *,
    response_added_required_policy: str = "tolerant_reader",
) -> ChangeClassification:
    """
    Classify one API change using the deterministic rule engine.

    This function intentionally contains no LLM call.
    """
    result = deterministic_classify_change(
        change,
        response_added_required_policy=response_added_required_policy,
    )

    return ChangeClassification(
        classification=result["classification"],
        severity=result["severity"],
        reason=result.get("reason", ""),
        rule_id=result.get("rule_id"),
        remediation_hint=result.get("remediation_hint"),
        flags=result.get("flags", []),
        rules_version=result.get("rules_version", RULES_VERSION),
    )


def evaluate(
    change: dict[str, Any] | Any,
    *,
    response_added_required_policy: str = "tolerant_reader",
):
    """
    Expose the pure rule evaluation API while preserving this module
    as a compatibility layer.
    """
    return evaluate_change(
        change,
        response_added_required_policy=response_added_required_policy,
    )


__all__ = [
    "ChangeClassification",
    "ClassificationType",
    "SeverityType",
    "classify_change",
    "evaluate",
]