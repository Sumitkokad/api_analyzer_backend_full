from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Optional


GATE_ENGINE_VERSION = "2026.09.1"

VALID_CLASSIFICATIONS = {
    "breaking",
    "non-breaking",
    "potentially-breaking",
}

VALID_SEVERITIES = {
    "low",
    "medium",
    "high",
}

VALID_STATUSES = {
    "PASS",
    "WARN",
    "FAIL",
    "ERROR",
}

SEVERITY_RANK = {
    "low": 1,
    "medium": 2,
    "high": 3,
}

DEFAULT_FAIL_SEVERITY_THRESHOLD = "high"


@dataclass(frozen=True)
class GateResult:
    status: str
    reason_code: str
    message: str
    blocking_change_count: int = 0
    breaking_change_count: int = 0
    potentially_breaking_count: int = 0
    waived_breaking_count: int = 0
    quality_score: Optional[int] = None
    engine_version: str = GATE_ENGINE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_gate(
    changes: Iterable[Any] | None = None,
    *,
    quality_report: Optional[Any] = None,
    fail_severity_threshold: str = DEFAULT_FAIL_SEVERITY_THRESHOLD,
    potentially_breaking_policy: str = "warn",
    strict_quality: bool = False,
) -> GateResult:
    """
    Pure deterministic gate evaluation.

    No database access.
    No network access.
    No LLM calls.
    No GitHub calls.

    Classification and severity are treated as authoritative inputs
    produced by the deterministic rule engine.
    """

    try:
        threshold = _validate_severity_threshold(
            fail_severity_threshold
        )

        policy = _validate_potentially_breaking_policy(
            potentially_breaking_policy
        )

        normalized_changes = [
            _normalize_change(change)
            for change in (changes or [])
        ]

        quality_score = _extract_quality_score(quality_report)

        # Invalid/incomplete change data must never become PASS.
        for change in normalized_changes:
            validation_error = _validate_change(change)

            if validation_error:
                return GateResult(
                    status="ERROR",
                    reason_code="ANALYZER_INTERNAL_ERROR",
                    message=validation_error,
                    quality_score=quality_score,
                )

        breaking_count = 0
        potentially_breaking_count = 0
        waived_breaking_count = 0
        blocking_count = 0

        for change in normalized_changes:
            classification = change["classification"]
            severity = change["severity"]
            waived = change["waived"]

            if classification == "breaking":
                breaking_count += 1

                if waived:
                    waived_breaking_count += 1
                    continue

                if _severity_meets_threshold(
                    severity,
                    threshold,
                ):
                    blocking_count += 1

            elif classification == "potentially-breaking":
                potentially_breaking_count += 1

                if policy == "fail":
                    blocking_count += 1

        # Highest-priority result: hard breaking changes.
        if blocking_count > 0:
            if potentially_breaking_policy == "fail":
                message = (
                    f"{blocking_count} compatibility change(s) "
                    "block the gate."
                )
            else:
                message = (
                    f"{blocking_count} unwaived breaking change(s) "
                    "meet the configured severity threshold."
                )

            return GateResult(
                status="FAIL",
                reason_code="BREAKING_CHANGES_FOUND",
                message=message,
                blocking_change_count=blocking_count,
                breaking_change_count=breaking_count,
                potentially_breaking_count=potentially_breaking_count,
                waived_breaking_count=waived_breaking_count,
                quality_score=quality_score,
            )

        # Breaking changes below the configured failure threshold
        # must WARN rather than PASS.
        if breaking_count > waived_breaking_count:
            below_threshold_count = (
                breaking_count
                - waived_breaking_count
            )

            return GateResult(
                status="WARN",
                reason_code="BREAKING_CHANGES_FOUND",
                message=(
                    f"{below_threshold_count} breaking change(s) "
                    "are below the configured failure severity threshold "
                    "and require review."
                ),
                blocking_change_count=0,
                breaking_change_count=breaking_count,
                potentially_breaking_count=0,
                waived_breaking_count=waived_breaking_count,
                quality_score=quality_score,
            )

        # Potentially-breaking changes are WARN by default.
        if potentially_breaking_count > 0:
            return GateResult(
                status="WARN",
                reason_code="POTENTIAL_BREAKING_CHANGES",
                message=(
                    f"{potentially_breaking_count} potentially-breaking "
                    "change(s) require review."
                ),
                blocking_change_count=0,
                breaking_change_count=breaking_count,
                potentially_breaking_count=potentially_breaking_count,
                waived_breaking_count=waived_breaking_count,
                quality_score=quality_score,
            )

        # A waived breaking change remains a breaking change,
        # but no longer blocks the gate.
        if waived_breaking_count > 0:
            return GateResult(
                status="WARN",
                reason_code="WAIVED_BREAKING_CHANGES",
                message=(
                    f"{waived_breaking_count} breaking change(s) "
                    "are covered by waivers."
                ),
                blocking_change_count=0,
                breaking_change_count=breaking_count,
                potentially_breaking_count=0,
                waived_breaking_count=waived_breaking_count,
                quality_score=quality_score,
            )

        # Quality is evaluated after compatibility so that an
        # actually blocking breaking change remains FAIL.
        quality_result = _evaluate_quality_gate(
            quality_report,
            strict_quality=strict_quality,
        )

        if quality_result is not None:
            return GateResult(
                status=quality_result["status"],
                reason_code=quality_result["reason_code"],
                message=quality_result["message"],
                blocking_change_count=0,
                breaking_change_count=breaking_count,
                potentially_breaking_count=0,
                waived_breaking_count=0,
                quality_score=quality_score,
            )

        return GateResult(
            status="PASS",
            reason_code="NO_BREAKING_CHANGES",
            message=(
                "No unwaived breaking or potentially-breaking "
                "changes were detected."
            ),
            blocking_change_count=0,
            breaking_change_count=breaking_count,
            potentially_breaking_count=0,
            waived_breaking_count=0,
            quality_score=quality_score,
        )

    except ValueError as exc:
        return GateResult(
            status="ERROR",
            reason_code="ANALYZER_INTERNAL_ERROR",
            message=str(exc),
            quality_score=_extract_quality_score(quality_report),
        )
    except Exception as exc:
        return GateResult(
            status="ERROR",
            reason_code="ANALYZER_INTERNAL_ERROR",
            message=f"Gate evaluation failed: {exc}",
            quality_score=_extract_quality_score(quality_report),
        )


def _normalize_change(change: Any) -> dict[str, Any]:
    """
    Accept dictionaries, Pydantic models, dataclasses, or ordinary objects.
    """

    if isinstance(change, Mapping):
        source = change
        return {
            "classification": source.get("classification")
            or source.get("compatibility"),
            "severity": source.get("severity"),
            "waived": bool(source.get("waived", False)),
        }

    if hasattr(change, "model_dump"):
        source = change.model_dump()

        return {
            "classification": source.get("classification")
            or source.get("compatibility"),
            "severity": source.get("severity"),
            "waived": bool(source.get("waived", False)),
        }

    return {
        "classification": getattr(
            change,
            "classification",
            None,
        )
        or getattr(change, "compatibility", None),
        "severity": getattr(
            change,
            "severity",
            None,
        ),
        "waived": bool(
            getattr(change, "waived", False)
        ),
    }


def _validate_change(change: Mapping[str, Any]) -> Optional[str]:
    classification = change.get("classification")
    severity = change.get("severity")

    if classification not in VALID_CLASSIFICATIONS:
        return (
            "Gate received an invalid or missing classification: "
            f"{classification!r}"
        )

    if severity not in VALID_SEVERITIES:
        return (
            "Gate received an invalid or missing severity: "
            f"{severity!r}"
        )

    return None


def _validate_severity_threshold(
    threshold: str,
) -> str:
    if threshold not in VALID_SEVERITIES:
        raise ValueError(
            "fail_severity_threshold must be one of: "
            "low, medium, high."
        )

    return threshold


def _validate_potentially_breaking_policy(
    policy: str,
) -> str:
    if policy not in {"warn", "fail"}:
        raise ValueError(
            "potentially_breaking_policy must be 'warn' or 'fail'."
        )

    return policy


def _severity_meets_threshold(
    severity: str,
    threshold: str,
) -> bool:
    return (
        SEVERITY_RANK[severity]
        >= SEVERITY_RANK[threshold]
    )


def _extract_quality_score(
    quality_report: Any,
) -> Optional[int]:
    if quality_report is None:
        return None

    if isinstance(quality_report, Mapping):
        score = quality_report.get("quality_score")

        if score is None:
            return None

        return _safe_score(score)

    if hasattr(quality_report, "quality_score"):
        return _safe_score(
            getattr(quality_report, "quality_score")
        )

    return None


def _safe_score(value: Any) -> Optional[int]:
    try:
        score = int(value)
    except (TypeError, ValueError):
        return None

    return max(0, min(100, score))


def _evaluate_quality_gate(
    quality_report: Any,
    *,
    strict_quality: bool,
) -> Optional[dict[str, str]]:
    if quality_report is None:
        return None

    score = _extract_quality_score(quality_report)

    if score is None:
        return {
            "status": "ERROR",
            "reason_code": "ANALYZER_INTERNAL_ERROR",
            "message": (
                "Quality report exists but contains no valid "
                "quality score."
            ),
        }

    threshold = None

    if isinstance(quality_report, Mapping):
        threshold = quality_report.get("min_quality_score")

        if threshold is None:
            threshold = quality_report.get("threshold")

    else:
        threshold = getattr(
            quality_report,
            "min_quality_score",
            None,
        )

    if threshold is None:
        return None

    try:
        threshold = int(threshold)
    except (TypeError, ValueError):
        return {
            "status": "ERROR",
            "reason_code": "ANALYZER_INTERNAL_ERROR",
            "message": (
                "Quality report contains an invalid "
                "quality threshold."
            ),
        }

    if score >= threshold:
        return None

    return {
        "status": "ERROR" if strict_quality else "WARN",
        "reason_code": "LOW_CONTRACT_COVERAGE",
        "message": (
            f"Contract quality score {score} is below "
            f"the required threshold {threshold}."
        ),
    }


__all__ = [
    "GATE_ENGINE_VERSION",
    "GateResult",
    "evaluate_gate",
]