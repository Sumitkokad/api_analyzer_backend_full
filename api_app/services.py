from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from django.db import transaction
from django.utils import timezone

from .api_diff import compare_api_specs
from .breaking_change_rules import classify_change
from .contracts.quality import evaluate_contract_quality
from .contracts.gate import evaluate_gate
from .impact_analysis import analyze_impact
from .models import (
    APIChangeRecord,
    AnalysisJob,
    Comparison,
    Evidence,
    ImpactReportRecord,
    MigrationPlan,
)


TRUE_VALUES = {"1", "true", "yes", "on"}


def _env_enabled(name: str) -> bool:
    return (
        os.getenv(name, "").strip().lower()
        in TRUE_VALUES
    )


def content_hash(content: dict[str, Any]) -> str:
    raw = json.dumps(
        content,
        sort_keys=True,
        default=str,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def _jsonable(value: Any) -> Any:
    return json.loads(
        json.dumps(
            value,
            default=str,
            ensure_ascii=False,
        )
    )


def _change_payload(change: Any) -> dict[str, Any]:
    if hasattr(change, "model_dump"):
        return change.model_dump()

    if isinstance(change, dict):
        return dict(change)

    return {
        "change_id": getattr(
            change,
            "change_id",
            None,
        ),
        "change_type": getattr(
            change,
            "change_type",
            None,
        ),
        "endpoint": getattr(
            change,
            "endpoint",
            None,
        ),
        "method": getattr(
            change,
            "method",
            None,
        ),
        "parameter": getattr(
            change,
            "parameter",
            None,
        ),
        "schema_path": getattr(
            change,
            "schema_path",
            None,
        ),
        "old_value": getattr(
            change,
            "old_value",
            None,
        ),
        "new_value": getattr(
            change,
            "new_value",
            None,
        ),
    }


def _create_evidence(
    record: APIChangeRecord,
    change: Any,
) -> list[dict[str, Any]]:
    old_value = getattr(
        change,
        "old_value",
        None,
    )
    new_value = getattr(
        change,
        "new_value",
        None,
    )

    if old_value is None and new_value is None:
        return []

    source = getattr(
        change,
        "source",
        {},
    ) or {}

    location = (
        getattr(change, "schema_path", None)
        or getattr(change, "location", None)
        or getattr(change, "parameter", None)
        or getattr(change, "endpoint", None)
        or ""
    )

    excerpt = json.dumps(
        {
            "old_value": old_value,
            "new_value": new_value,
        },
        indent=2,
        sort_keys=True,
        default=str,
        ensure_ascii=False,
    )[:4000]

    evidence_record = Evidence.objects.create(
        change=record,
        source_type="schema",
        source=source.get(
            "source",
            "openapi_specification",
        ),
        location=location,
        excerpt=excerpt,
        confidence=getattr(
            change,
            "confidence",
            1.0,
        ),
        retrieval="exact",
    )

    return [
        {
            "source_type": evidence_record.source_type,
            "source": evidence_record.source,
            "location": evidence_record.location,
            "excerpt": evidence_record.excerpt,
            "confidence": evidence_record.confidence,
            "retrieval": evidence_record.retrieval,
        }
    ]


def _fallback_impact(
    change: Any,
    rule_result: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    classification = rule_result["classification"]
    severity = rule_result["severity"]

    change_type = getattr(
        change,
        "change_type",
        "API change",
    )

    endpoint = getattr(
        change,
        "endpoint",
        "affected endpoint",
    )

    subject = (
        getattr(change, "parameter", None)
        or getattr(change, "schema_path", None)
        or endpoint
    )

    reason = (
        f"Deterministic compatibility rules classified "
        f"{change_type} on {endpoint} as {classification} "
        f"because the API contract changed from "
        f"{getattr(change, 'old_value', None)!r} to "
        f"{getattr(change, 'new_value', None)!r}."
    )

    affected_components = [endpoint]

    parameter = getattr(
        change,
        "parameter",
        None,
    )

    schema_path = getattr(
        change,
        "schema_path",
        None,
    )

    if parameter:
        affected_components.append(
            f"{endpoint} parameter {parameter}"
        )

    if schema_path:
        affected_components.append(
            schema_path
        )

    if classification == "non-breaking":
        impact = (
            f"Existing consumers of {endpoint} are not "
            "expected to fail solely because of this change."
        )
        recommendation = (
            "Document the change and keep regression "
            "tests covering the endpoint."
        )
    else:
        impact = (
            f"Consumers depending on {subject} may need "
            "to adjust requests, response parsing, "
            "generated SDKs, or tests before adopting "
            "the new API."
        )
        recommendation = (
            "Review affected clients, update contract "
            "tests, and provide a compatibility window "
            "or migration notes before rollout."
        )

    return {
        "classification": classification,
        "severity": severity,
        "reason": reason,
        "affected_components": list(
            dict.fromkeys(affected_components)
        ),
        "impact": impact,
        "recommendation": recommendation,
        "confidence": getattr(
            change,
            "confidence",
            1.0,
        ),
        "status": "skipped",
        "input_hash": "",
        "prompt_version": "",
        "model": "",
        "evidence": evidence,
        "error": "",
    }


def _analyze_change_impact(
    change: Any,
    rule_result: dict[str, Any],
    evidence: list[dict[str, Any]],
):
    """
    Deterministic rules remain authoritative.

    LLM is optional explanation only.
    """
    change_payload = _change_payload(change)

    documentation = "\n\n".join(
        item.get("excerpt", "")
        for item in evidence
        if item.get("excerpt")
    )

    try:
        report = analyze_impact(
            json.dumps(
                change_payload,
                indent=2,
                sort_keys=True,
                default=str,
            ),
            documentation
            or "No exact documentation evidence was available.",
            rule_result,
        )

        return {
            "classification": rule_result[
                "classification"
            ],
            "severity": rule_result[
                "severity"
            ],
            "reason": report.reason,
            "affected_components": (
                report.affected_components
            ),
            "impact": report.impact,
            "recommendation": (
                report.recommendation
            ),
            "confidence": report.confidence,
            "status": getattr(
                report,
                "status",
                "generated",
            ),
            "input_hash": getattr(
                report,
                "input_hash",
                "",
            ),
            "prompt_version": getattr(
                report,
                "prompt_version",
                "",
            ),
            "model": getattr(
                report,
                "model",
                "",
            ) or "",
            "evidence": getattr(
                report,
                "evidence",
                evidence,
            ),
            "error": getattr(
                report,
                "error",
                None,
            ) or "",
        }

    except Exception as exc:
        fallback = _fallback_impact(
            change,
            rule_result,
            evidence,
        )
        fallback["status"] = "failed"
        fallback["error"] = str(exc)
        return fallback


def summarize_changes(changes):
    summary = {
        "total": len(changes),
        "breaking": 0,
        "non_breaking": 0,
        "potentially_breaking": 0,
        "by_severity": {},
        "highest_risk_changes": [],
        "affected_components": [],
        "migration_recommendations": [],
    }

    severity_rank = {
        "high": 3,
        "medium": 2,
        "low": 1,
    }

    highest_risk = []
    affected_components = set()

    for change in changes:
        classification = getattr(
            change,
            "compatibility",
            None,
        )

        # In the common pipeline this can still be unknown
        # because the APIChange itself is deterministic later.
        if classification == "breaking":
            summary["breaking"] += 1
        elif classification == "potentially-breaking":
            summary["potentially_breaking"] += 1
        else:
            summary["non_breaking"] += 1

        severity = getattr(
            change,
            "severity",
            None,
        )

        if severity:
            summary["by_severity"][severity] = (
                summary["by_severity"].get(
                    severity,
                    0,
                )
                + 1
            )

        endpoint = getattr(
            change,
            "endpoint",
            None,
        )

        if endpoint:
            affected_components.add(
                endpoint
            )

    # The authoritative classification is recalculated
    # from the rule engine, because APIChange itself may
    # initially have unknown compatibility.
    breaking = 0
    non_breaking = 0
    potentially_breaking = 0
    by_severity = {}

    for change in changes:
        result = classify_change(change)

        classification = result[
            "classification"
        ]
        severity = result[
            "severity"
        ]

        if classification == "breaking":
            breaking += 1
        elif classification == "potentially-breaking":
            potentially_breaking += 1
        else:
            non_breaking += 1

        by_severity[severity] = (
            by_severity.get(severity, 0) + 1
        )

        if severity_rank.get(
            severity,
            0,
        ) >= 3:
            highest_risk.append(
                {
                    "change_id": getattr(
                        change,
                        "change_id",
                        None,
                    ),
                    "change_type": getattr(
                        change,
                        "change_type",
                        None,
                    ),
                    "endpoint": endpoint,
                    "severity": severity,
                    "classification": classification,
                }
            )

    summary["breaking"] = breaking
    summary["non_breaking"] = non_breaking
    summary["potentially_breaking"] = (
        potentially_breaking
    )
    summary["by_severity"] = by_severity
    summary["highest_risk_changes"] = (
        highest_risk[:10]
    )
    summary["affected_components"] = sorted(
        affected_components
    )

    if breaking:
        summary["overall_risk"] = "HIGH"
        summary["migration_recommendations"] = [
            "Prioritize breaking changes before release.",
            "Publish migration guidance for affected consumers.",
        ]
    elif potentially_breaking:
        summary["overall_risk"] = "MEDIUM"
        summary["migration_recommendations"] = [
            "Review potentially-breaking changes with consumer teams."
        ]
    else:
        summary["overall_risk"] = "LOW"
        summary["migration_recommendations"] = [
            "No breaking changes were detected by deterministic rules."
        ]

    return summary


def build_migration_plan(
    comparison: Comparison,
    changes,
):
    breaking = []

    for change in changes:
        result = classify_change(change)

        if result["classification"] == "breaking":
            breaking.append(
                (
                    change,
                    result,
                )
            )

    steps = [
        (
            f"Update consumers affected by "
            f"{change.change_type} at "
            f"{change.endpoint}."
        )
        for change, _ in breaking[:20]
    ]

    if not steps:
        steps.append(
            "No mandatory consumer migration was "
            "detected by deterministic rules."
        )

    versioning = (
        "Use a major version or explicit deprecation "
        "window for the breaking changes."
        if breaking
        else
        "Changes appear compatible with the current version."
    )

    plan, _ = MigrationPlan.objects.update_or_create(
        comparison=comparison,
        defaults={
            "summary": (
                f"{len(breaking)} breaking changes "
                "require migration review."
            ),
            "steps": steps,
            "versioning_recommendation": versioning,
            "rollback": (
                "Keep the previous API version available "
                "until consumers are verified."
            ),
        },
    )

    return plan


def _store_change(
    comparison: Comparison,
    change: Any,
    rule_result: dict[str, Any],
) -> APIChangeRecord:
    change_payload = _change_payload(change)

    stable_hash = (
        getattr(
            change,
            "stable_hash",
            None,
        )
        or change_payload.get(
            "stable_hash"
        )
        or ""
    )

    record = APIChangeRecord.objects.create(
        comparison=comparison,
        change_id=(
            getattr(
                change,
                "change_id",
                None,
            )
            or change_payload.get(
                "change_id"
            )
            or stable_hash[:16]
        ),
        stable_hash=stable_hash,
        rule_id=rule_result.get(
            "rule_id",
            "",
        ),
        change_type=getattr(
            change,
            "change_type",
            "",
        ),
        category=getattr(
            change,
            "category",
            "contract",
        ),
        endpoint=getattr(
            change,
            "endpoint",
            "",
        ),
        method=getattr(
            change,
            "method",
            "",
        )
        or "",
        direction=getattr(
            change,
            "direction",
            "unknown",
        ),
        location=getattr(
            change,
            "location",
            "",
        )
        or "",
        parameter=getattr(
            change,
            "parameter",
            "",
        )
        or "",
        schema_path=getattr(
            change,
            "schema_path",
            "",
        )
        or "",
        relation=getattr(
            change,
            "relation",
            "",
        )
        or "",
        flags=getattr(
            change,
            "flags",
            [],
        )
        or [],
        old_value=_jsonable(
            getattr(
                change,
                "old_value",
                None,
            )
        ),
        new_value=_jsonable(
            getattr(
                change,
                "new_value",
                None,
            )
        ),
        compatibility=rule_result[
            "classification"
        ],
        severity=rule_result[
            "severity"
        ],
        confidence=getattr(
            change,
            "confidence",
            1.0,
        ),
        source=getattr(
            change,
            "source",
            {},
        )
        or {},
    )

    return record


def _store_impact_report(
    comparison: Comparison,
    record: APIChangeRecord,
    impact: dict[str, Any],
):
    return ImpactReportRecord.objects.create(
        comparison=comparison,
        change=record,
        classification=impact[
            "classification"
        ],
        severity=impact[
            "severity"
        ],
        reason=impact["reason"],
        affected_components=impact[
            "affected_components"
        ],
        impact=impact["impact"],
        recommendation=impact[
            "recommendation"
        ],
        confidence=impact[
            "confidence"
        ],
        confidence_label="estimated",
        status=impact.get(
            "status",
            "skipped",
        ),
        input_hash=impact.get(
            "input_hash",
            "",
        ),
        prompt_version=impact.get(
            "prompt_version",
            "",
        ),
        model=impact.get(
            "model",
            "",
        ),
        evidence=impact.get(
            "evidence",
            [],
        ),
        error=impact.get(
            "error",
            "",
        ),
        llm_status=impact.get(
            "status",
            "skipped",
        ),
        llm_error=impact.get(
            "error",
            "",
        ),
    )


def _set_comparison_failed(
    comparison_id,
    error: Exception,
):
    Comparison.objects.filter(
        pk=comparison_id
    ).update(
        status="failed",
        error=str(error),
        updated_at=timezone.now(),
    )


def run_comparison(
    comparison: Comparison,
) -> Comparison:
    """
    Common deterministic comparison pipeline.

    Manual and future CI callers use this same function.
    """
    comparison.status = "running"
    comparison.error = ""
    comparison.save(
        update_fields=[
            "status",
            "quality_report",
            "error",
            "updated_at",
        ]
    )

    try:
        with transaction.atomic():
            base_spec = comparison.old_specification.content
            head_spec = comparison.new_specification.content

            project = comparison.project

            quality_threshold = getattr(
                project,
                "min_quality_score",
                70,
            )

            base_quality = evaluate_contract_quality(
                base_spec,
                min_quality_score=quality_threshold,
                strict=False,
            )

            head_quality = evaluate_contract_quality(
                head_spec,
                min_quality_score=quality_threshold,
                strict=False,
            )

            comparison.quality_report = {
                "engine_version": base_quality.engine_version,
                "threshold": quality_threshold,
                "base": base_quality.to_dict(),
                "head": head_quality.to_dict(),
            }

            changes = compare_api_specs(
                base_spec,
                head_spec,
            )

            comparison.changes.all().delete()
            comparison.impact_reports.all().delete()

            stored_changes = []
            gate_changes = []

            for change in changes:
                rule_result = classify_change(
                    change
                )
                gate_changes.append(
                    {
                        "classification": rule_result["classification"],
                        "severity": rule_result["severity"],
                        "waived": False,
                    }
                )

                record = _store_change(
                    comparison,
                    change,
                    rule_result,
                )

                evidence = _create_evidence(
                    record,
                    change,
                )

                impact = _analyze_change_impact(
                    change,
                    rule_result,
                    evidence,
                )

                _store_impact_report(
                    comparison,
                    record,
                    impact,
                )

                stored_changes.append(
                    change
                )

            summary = summarize_changes(
                stored_changes
            )

            comparison.summary = summary
            comparison.summary_counts = {
                "total": summary["total"],
                "breaking": summary["breaking"],
                "non_breaking": summary[
                    "non_breaking"
                ],
                "potentially_breaking": summary[
                    "potentially_breaking"
                ],
                "by_severity": summary[
                    "by_severity"
                ],
            }

            gate_result = evaluate_gate(
                gate_changes,
                quality_report={
                    "quality_score": head_quality.quality_score,
                    "threshold": quality_threshold,
                },
                fail_severity_threshold=getattr(
                    project,
                    "fail_severity_threshold",
                    "high",
                ),
                potentially_breaking_policy=getattr(
                    project,
                    "potentially_breaking_policy",
                    "warn",
                ),
                strict_quality=(
                    getattr(
                        project,
                        "error_mode",
                        "fail_closed",
                    )
                    == "fail_closed"
                ),
            )

            comparison.gate_status = gate_result.status
            comparison.gate_reason_code = gate_result.reason_code

            comparison.status = "completed"

            comparison.save(
                update_fields=[
                    "summary",
                    "summary_counts",
                    "quality_report",
                    "gate_status",
                    "gate_reason_code",
                    "status",
                    "updated_at",
                ]
            )

            build_migration_plan(
                comparison,
                stored_changes,
            )

        return comparison

    except Exception as exc:
        _set_comparison_failed(
            comparison.pk,
            exc,
        )
        raise


def run_analysis_job(
    job: AnalysisJob,
) -> AnalysisJob:
    job.status = "running"
    job.stage = "diff"
    job.attempts += 1
    job.started_at = timezone.now()
    job.heartbeat_at = timezone.now()
    job.progress = 10

    job.save(
        update_fields=[
            "status",
            "stage",
            "attempts",
            "started_at",
            "heartbeat_at",
            "progress",
            "updated_at",
        ]
    )

    try:
        if not job.comparison_id:
            raise ValueError(
                "Analysis job has no comparison."
            )

        comparison = run_comparison(
            job.comparison
        )

        job.stage = "publish"
        job.progress = 90
        job.heartbeat_at = timezone.now()

        job.result = {
            "comparison_id": comparison.id,
            "summary": comparison.summary,
        }

        job.status = "completed"
        job.progress = 100
        job.stage = "completed"
        job.completed_at = timezone.now()

        job.save(
            update_fields=[
                "status",
                "stage",
                "progress",
                "heartbeat_at",
                "result",
                "completed_at",
                "updated_at",
            ]
        )

        return job

    except Exception as exc:
        job.status = "failed"
        job.stage = "failed"
        job.error = str(exc)
        job.error_code = (
            "ANALYZER_INTERNAL_ERROR"
        )
        job.error_detail = str(exc)
        job.completed_at = timezone.now()

        job.save(
            update_fields=[
                "status",
                "stage",
                "error",
                "error_code",
                "error_detail",
                "completed_at",
                "updated_at",
            ]
        )

        raise