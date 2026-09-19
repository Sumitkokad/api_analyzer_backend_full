import hashlib
import json
import os
from typing import Any

from django.db import transaction
from django.utils import timezone
from pydantic import ValidationError

from .api_diff import compare_api_specs
from .breaking_change_rules import classify_change
from .impact_analysis import analyze_impact
from .models import (
    APIChangeRecord,
    AnalysisJob,
    Comparison,
    Evidence,
    ImpactReportRecord,
    MigrationPlan,
)
from .schemas import ImpactReport


def content_hash(content: dict[str, Any]) -> str:
    raw = json.dumps(content, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _jsonable(value):
    return json.loads(json.dumps(value, default=str))


def _change_payload(change) -> dict[str, Any]:
    if hasattr(change, "model_dump"):
        return change.model_dump()
    return {
        "change_id": change.change_id,
        "change_type": change.change_type,
        "endpoint": change.endpoint,
        "method": change.method,
        "parameter": change.parameter,
        "schema_path": change.schema_path,
        "old_value": change.old_value,
        "new_value": change.new_value,
    }


def _has_llm_credentials() -> bool:
    if os.getenv("API_ANALYZER_DISABLE_LLM", "").lower() in {"1", "true", "yes"}:
        return False
    if os.getenv("API_ANALYZER_ENABLE_LLM", "").lower() not in {"1", "true", "yes"}:
        return False
    return bool(os.getenv("GROQ_API_KEY") or os.getenv("HUGGINGFACEHUB_API_TOKEN"))


def _evidence_excerpt(change) -> str:
    payload = {
        "old_value": change.old_value,
        "new_value": change.new_value,
    }
    return json.dumps(payload, indent=2, sort_keys=True, default=str)[:4000]


def _create_evidence(record: APIChangeRecord, change) -> list[dict[str, Any]]:
    evidence = []
    source = change.source or {}
    location = change.schema_path or change.location or change.parameter or change.endpoint
    if change.old_value is not None or change.new_value is not None:
        item = Evidence.objects.create(
            change=record,
            source_type="schema",
            source=source.get("source", "openapi_specification"),
            location=location or "",
            excerpt=_evidence_excerpt(change),
            confidence=change.confidence,
        )
        evidence.append({
            "source_type": item.source_type,
            "source": item.source,
            "location": item.location,
            "excerpt": item.excerpt,
            "confidence": item.confidence,
        })
    return evidence


def _fallback_impact(change, rule_result, evidence, llm_status="skipped", llm_error="") -> ImpactReport:
    subject = change.parameter or change.schema_path or change.endpoint
    classification = rule_result["classification"]
    severity = rule_result["severity"]
    reason = (
        f"Deterministic compatibility rules classified {change.change_type} on "
        f"{change.endpoint} as {classification} because the API contract changed "
        f"from {change.old_value!r} to {change.new_value!r}."
    )
    affected = [change.endpoint]
    if change.parameter:
        affected.append(f"{change.endpoint} parameter {change.parameter}")
    if change.schema_path:
        affected.append(change.schema_path)
    impact = (
        f"Consumers depending on {subject} may need to adjust requests, response parsing, "
        f"generated SDKs, or tests before adopting the new API."
    )
    recommendation = (
        "Review affected clients, update contract tests, and provide a compatibility window "
        "or migration notes before rollout."
    )
    if classification == "non-breaking":
        impact = f"Existing consumers of {change.endpoint} are not expected to fail solely because of this change."
        recommendation = "Document the change and keep regression tests covering the endpoint."

    report = ImpactReport(
        classification=classification,
        severity=severity,
        reason=reason,
        affected_components=affected,
        impact=impact,
        recommendation=recommendation,
        evidence=evidence,
        confidence=change.confidence,
    )
    return report


def _analyze_change_impact(change, rule_result, evidence) -> tuple[ImpactReport, str, str]:
    if not _has_llm_credentials():
        report = _fallback_impact(change, rule_result, evidence, llm_status="skipped")
        return report, "skipped", ""

    documentation = "\n\n".join(
        item.get("excerpt", "")
        for item in evidence
        if item.get("excerpt")
    )
    try:
        report = analyze_impact(
            json.dumps(_change_payload(change), indent=2, sort_keys=True, default=str),
            documentation or "No retrieved documentation evidence was available.",
        )
        return report, "completed", ""
    except (ValidationError, ValueError, TypeError) as exc:
        report = _fallback_impact(change, rule_result, evidence, llm_status="invalid", llm_error=str(exc))
        return report, "invalid", str(exc)
    except Exception as exc:
        report = _fallback_impact(change, rule_result, evidence, llm_status="failed", llm_error=str(exc))
        return report, "failed", str(exc)


def summarize_changes(changes):
    summary = {
        "total": len(changes),
        "breaking": 0,
        "non_breaking": 0,
        "potentially_breaking": 0,
        "by_severity": {},
    }
    severity_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    highest_risk = []
    affected_components = set()
    recommendations = []
    for change in changes:
        if change.compatibility == "breaking":
            summary["breaking"] += 1
        elif change.compatibility == "potentially-breaking":
            summary["potentially_breaking"] += 1
        else:
            summary["non_breaking"] += 1
        if change.severity:
            summary["by_severity"][change.severity] = summary["by_severity"].get(change.severity, 0) + 1
        if change.endpoint:
            affected_components.add(change.endpoint)
        if severity_rank.get(change.severity or "", 0) >= 3:
            highest_risk.append({
                "change_id": change.change_id,
                "change_type": change.change_type,
                "endpoint": change.endpoint,
                "severity": change.severity,
                "classification": change.compatibility,
            })
    if summary["breaking"] or summary["by_severity"].get("critical"):
        summary["overall_risk"] = "HIGH"
        recommendations.append("Prioritize breaking changes before release and publish migration guidance.")
    elif summary["potentially_breaking"]:
        summary["overall_risk"] = "MEDIUM"
        recommendations.append("Review potentially breaking changes with consumer teams.")
    else:
        summary["overall_risk"] = "LOW"
        recommendations.append("No breaking changes were detected by deterministic rules.")
    summary["highest_risk_changes"] = highest_risk[:10]
    summary["affected_components"] = sorted(affected_components)
    summary["migration_recommendations"] = recommendations
    return summary


def build_migration_plan(comparison, changes):
    breaking = [change for change in changes if change.compatibility == "breaking"]
    steps = []
    for change in breaking[:20]:
        steps.append(f"Update consumers affected by {change.change_type} at {change.endpoint}.")
    if not steps:
        steps.append("No mandatory consumer migration was detected by deterministic rules.")

    versioning = (
        "Use a major version or explicit deprecation window for the breaking changes."
        if breaking
        else "Changes appear compatible with the current version."
    )

    return MigrationPlan.objects.update_or_create(
        comparison=comparison,
        defaults={
            "summary": f"{len(breaking)} breaking changes require migration review.",
            "steps": steps,
            "versioning_recommendation": versioning,
            "rollback": "Keep the previous API version available until consumers are verified.",
        },
    )[0]


@transaction.atomic
def run_comparison(comparison: Comparison) -> Comparison:
    comparison.status = "running"
    comparison.error = ""
    comparison.save(update_fields=["status", "error", "updated_at"])

    comparison.changes.all().delete()
    comparison.impact_reports.all().delete()

    try:
        changes = compare_api_specs(
            comparison.old_specification.content,
            comparison.new_specification.content,
        )
        for change in changes:
            rule_result = classify_change(change)
            record = APIChangeRecord.objects.create(
                comparison=comparison,
                change_id=change.change_id,
                change_type=change.change_type,
                category=change.category,
                endpoint=change.endpoint,
                method=change.method or "",
                direction=change.direction,
                location=change.location or "",
                parameter=change.parameter or "",
                schema_path=change.schema_path or "",
                old_value=_jsonable(change.old_value),
                new_value=_jsonable(change.new_value),
                compatibility=rule_result["classification"],
                severity=rule_result["severity"],
                confidence=change.confidence,
                source=change.source,
            )
            evidence = _create_evidence(record, change)
            impact_report, llm_status, llm_error = _analyze_change_impact(change, rule_result, evidence)
            ImpactReportRecord.objects.create(
                comparison=comparison,
                change=record,
                classification=impact_report.classification,
                severity=impact_report.severity,
                reason=impact_report.reason,
                affected_components=impact_report.affected_components,
                impact=impact_report.impact,
                recommendation=impact_report.recommendation,
                confidence=impact_report.confidence,
                llm_status=llm_status,
                llm_error=llm_error,
            )

        comparison.summary = summarize_changes(changes)
        comparison.status = "completed"
        comparison.save(update_fields=["summary", "status", "updated_at"])
        build_migration_plan(comparison, changes)
        return comparison
    except Exception as exc:
        comparison.status = "failed"
        comparison.error = str(exc)
        comparison.save(update_fields=["status", "error", "updated_at"])
        raise


def run_analysis_job(job: AnalysisJob) -> AnalysisJob:
    job.status = "running"
    job.started_at = timezone.now()
    job.progress = 10
    job.save(update_fields=["status", "started_at", "progress", "updated_at"])
    try:
        if job.comparison:
            run_comparison(job.comparison)
            job.result = {
                "summary": job.comparison.summary,
                "comparison_id": job.comparison_id,
                "changes": [
                    {
                        "id": change.change_id,
                        "change_type": change.change_type,
                        "endpoint": change.endpoint,
                        "method": change.method,
                        "parameter": change.parameter,
                        "classification": change.compatibility,
                        "severity": change.severity,
                        "impact_reports": [
                            {
                                "reason": report.reason,
                                "impact": report.impact,
                                "affected_components": report.affected_components,
                                "recommendation": report.recommendation,
                                "confidence": report.confidence,
                                "llm_status": report.llm_status,
                            }
                            for report in change.impact_reports.all()
                        ],
                    }
                    for change in job.comparison.changes.prefetch_related("impact_reports").all()
                ],
            }
        job.status = "completed"
        job.progress = 100
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "progress", "completed_at", "result", "updated_at"])
        return job
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "error", "completed_at", "updated_at"])
        raise
