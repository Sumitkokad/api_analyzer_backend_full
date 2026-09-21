from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from django.db import IntegrityError, transaction
from django.utils import timezone

from .execution_policy import decide_execution
from .models import (
    APISpecification,
    AnalysisJob,
    Comparison,
    Project,
)
from .services import run_analysis_job


MAX_IDEMPOTENCY_KEY_LENGTH = 255
MAX_REPOSITORY_LENGTH = 300
MAX_SHA_LENGTH = 128


class CIValidationError(ValueError):
    pass


@dataclass(frozen=True)
class CISubmission:
    project_id: Any
    idempotency_key: str
    repository: str
    pull_request: int | None
    base_sha: str
    head_sha: str
    base_spec: Mapping[str, Any]
    head_spec: Mapping[str, Any]
    registered_routes: list[str]
    generator_warnings: list[str]
    adapter: dict[str, Any]


def _canonical_hash(value: Mapping[str, Any]) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def _validate_spec(
    value: Any,
    name: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CIValidationError(
            f"{name} must be an object."
        )

    result = dict(value)

    content = result.get("content")

    if not isinstance(content, Mapping):
        raise CIValidationError(
            f"{name}.content must be an object."
        )

    return result


def _validate_payload(
    payload: Mapping[str, Any],
) -> CISubmission:
    required = (
        "project_id",
        "idempotency_key",
        "repository",
        "base_sha",
        "head_sha",
        "base_spec",
        "head_spec",
    )

    missing = [
        field
        for field in required
        if field not in payload
    ]

    if missing:
        raise CIValidationError(
            f"Missing required fields: {', '.join(missing)}."
        )

    project_id = payload["project_id"]

    idempotency_key = str(
        payload["idempotency_key"]
    ).strip()

    repository = str(
        payload["repository"]
    ).strip()

    base_sha = str(
        payload["base_sha"]
    ).strip()

    head_sha = str(
        payload["head_sha"]
    ).strip()

    if not idempotency_key:
        raise CIValidationError(
            "idempotency_key must not be empty."
        )

    if len(idempotency_key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise CIValidationError(
            "idempotency_key is too long."
        )

    if not repository:
        raise CIValidationError(
            "repository must not be empty."
        )

    if len(repository) > MAX_REPOSITORY_LENGTH:
        raise CIValidationError(
            "repository is too long."
        )

    if not base_sha:
        raise CIValidationError(
            "base_sha must not be empty."
        )

    if not head_sha:
        raise CIValidationError(
            "head_sha must not be empty."
        )

    if len(base_sha) > MAX_SHA_LENGTH:
        raise CIValidationError(
            "base_sha is too long."
        )

    if len(head_sha) > MAX_SHA_LENGTH:
        raise CIValidationError(
            "head_sha is too long."
        )

    pull_request = payload.get("pull_request")

    if pull_request is not None:
        try:
            pull_request = int(pull_request)
        except (TypeError, ValueError) as exc:
            raise CIValidationError(
                "pull_request must be an integer."
            ) from exc

        if pull_request <= 0:
            raise CIValidationError(
                "pull_request must be greater than zero."
            )

    base_spec = _validate_spec(
        payload["base_spec"],
        "base_spec",
    )

    head_spec = _validate_spec(
        payload["head_spec"],
        "head_spec",
    )

    registered_routes = payload.get(
        "registered_routes",
        [],
    )

    if not isinstance(registered_routes, list):
        raise CIValidationError(
            "registered_routes must be a list."
        )

    registered_routes = [
        str(route)
        for route in registered_routes
    ]

    generator_warnings = payload.get(
        "generator_warnings",
        [],
    )

    if not isinstance(generator_warnings, list):
        raise CIValidationError(
            "generator_warnings must be a list."
        )

    generator_warnings = [
        str(warning)
        for warning in generator_warnings
    ]

    adapter = payload.get(
        "adapter",
        {},
    )

    if not isinstance(adapter, Mapping):
        raise CIValidationError(
            "adapter must be an object."
        )

    return CISubmission(
        project_id=project_id,
        idempotency_key=idempotency_key,
        repository=repository,
        pull_request=pull_request,
        base_sha=base_sha,
        head_sha=head_sha,
        base_spec=base_spec,
        head_spec=head_spec,
        registered_routes=registered_routes,
        generator_warnings=generator_warnings,
        adapter=dict(adapter),
    )


def _existing_submission(
    project: Project,
    idempotency_key: str,
):
    comparison = (
        Comparison.objects
        .filter(
            project=project,
            idempotency_key=idempotency_key,
        )
        .first()
    )

    if not comparison:
        return None, None

    job = (
        AnalysisJob.objects
        .filter(comparison=comparison)
        .order_by("-created_at")
        .first()
    )

    return comparison, job


def _spec_kwargs(
    *,
    project: Project,
    payload: Mapping[str, Any],
    name: str,
    commit_sha: str,
    repository: str,
):
    content = dict(
        payload["content"]
    )

    kwargs = {
        "project": project,
        "name": name,
        "version": str(
            content.get(
                "info",
                {},
            ).get(
                "version",
                "",
            )
            if isinstance(
                content.get("info"),
                Mapping,
            )
            else ""
        ),
        "content": content,
        "raw_text": "",
        "content_hash": _canonical_hash(content),
    }

    model_fields = {
        field.name
        for field in APISpecification._meta.get_fields()
    }

    optional_values = {
        "repository": repository,
        "commit_sha": commit_sha,
        "path": payload.get("path", ""),
        "source_type": payload.get(
            "source_type",
            "generated",
        ),
        "openapi_version": str(
            content.get(
                "openapi",
                "",
            )
        ),
    }

    for field_name, value in optional_values.items():
        if field_name in model_fields:
            kwargs[field_name] = value

    return kwargs


def _comparison_kwargs(
    *,
    project: Project,
    old_spec: APISpecification,
    new_spec: APISpecification,
    submission: CISubmission,
):
    kwargs = {
        "project": project,
        "old_specification": old_spec,
        "new_specification": new_spec,
        "status": "queued",
    }

    model_fields = {
        field.name
        for field in Comparison._meta.get_fields()
    }

    optional_values = {
        "base_spec": old_spec,
        "head_spec": new_spec,
        "base_sha": submission.base_sha,
        "head_sha": submission.head_sha,
        "repository": submission.repository,
        "pull_request_number": submission.pull_request,
        "baseline_mode": getattr(
            project,
            "baseline_mode",
            "merge_base",
        ),
        "gate_status": None,
        "gate_reason_code": "",
        "summary_counts": {},
        "quality_report": {},
        "idempotency_key": submission.idempotency_key,
        "analyzer_version": "",
        "rules_version": "",
        "policy_version": "",
        "prompt_version": "",
        "llm_model": "",
    }

    for field_name, value in optional_values.items():
        if field_name in model_fields:
            kwargs[field_name] = value

    return kwargs


def _job_kwargs(
    *,
    project: Project,
    comparison: Comparison,
):
    kwargs = {
        "project": project,
        "comparison": comparison,
        "status": "queued",
        "progress": 0,
    }

    model_fields = {
        field.name
        for field in AnalysisJob._meta.get_fields()
    }

    optional_values = {
        "stage": "received",
        "attempts": 0,
        "heartbeat_at": None,
        "error_code": "",
        "error_detail": "",
    }

    for field_name, value in optional_values.items():
        if field_name in model_fields:
            kwargs[field_name] = value

    return kwargs


def create_ci_submission(
    project: Project,
    payload: Mapping[str, Any],
):
    """
    Create or return an idempotent CI comparison submission.

    This function ONLY validates the request and persists the immutable
    CI snapshots, comparison, and analysis job.

    It intentionally does not execute the analysis.

    Execution must happen after this database transaction has committed.
    """

    if not isinstance(payload, Mapping):
        raise CIValidationError(
            "CI request body must be an object."
        )

    submission = _validate_payload(payload)

    if str(project.pk) != str(submission.project_id):
        raise CIValidationError(
            "project_id does not match the authenticated project."
        )

    existing_comparison, existing_job = _existing_submission(
        project,
        submission.idempotency_key,
    )

    if existing_comparison:
        return {
            "comparison": existing_comparison,
            "job": existing_job,
            "created": False,
        }

    try:
        with transaction.atomic():
            base_spec = APISpecification.objects.create(
                **_spec_kwargs(
                    project=project,
                    payload=submission.base_spec,
                    name="CI Base Contract",
                    commit_sha=submission.base_sha,
                    repository=submission.repository,
                )
            )

            head_spec = APISpecification.objects.create(
                **_spec_kwargs(
                    project=project,
                    payload=submission.head_spec,
                    name="CI Head Contract",
                    commit_sha=submission.head_sha,
                    repository=submission.repository,
                )
            )

            comparison = Comparison.objects.create(
                **_comparison_kwargs(
                    project=project,
                    old_spec=base_spec,
                    new_spec=head_spec,
                    submission=submission,
                )
            )

            job = AnalysisJob.objects.create(
                **_job_kwargs(
                    project=project,
                    comparison=comparison,
                )
            )

            return {
                "comparison": comparison,
                "job": job,
                "created": True,
            }

    except IntegrityError:
        existing_comparison, existing_job = _existing_submission(
            project,
            submission.idempotency_key,
        )

        if existing_comparison:
            return {
                "comparison": existing_comparison,
                "job": existing_job,
                "created": False,
            }

        raise


def _mark_capacity_error(job: AnalysisJob) -> None:
    """
    Mark a job as terminally failed when the current deployment
    cannot safely execute the requested contract.

    A separate worker can later be enabled for larger contracts.
    """

    comparison = job.comparison

    now = timezone.now()

    job.status = "failed"
    job.progress = 100

    model_fields = {
        field.name
        for field in AnalysisJob._meta.get_fields()
    }

    update_fields = [
        "status",
        "progress",
        "updated_at",
    ]

    if "completed_at" in model_fields:
        job.completed_at = now
        update_fields.append("completed_at")

    if "stage" in model_fields:
        job.stage = "failed"
        update_fields.append("stage")

    if "error_code" in model_fields:
        job.error_code = "ANALYZER_CAPACITY_LIMIT"
        update_fields.append("error_code")

    if "error_detail" in model_fields:
        job.error_detail = (
            "The API contract exceeds the synchronous analyzer limits "
            "and no background worker is currently enabled."
        )
        update_fields.append("error_detail")

    job.save(
        update_fields=list(dict.fromkeys(update_fields))
    )

    comparison.status = "failed"

    comparison_fields = {
        field.name
        for field in Comparison._meta.get_fields()
    }

    comparison_update_fields = [
        "status",
        "updated_at",
    ]

    if "gate_status" in comparison_fields:
        comparison.gate_status = "ERROR"
        comparison_update_fields.append("gate_status")

    if "gate_reason_code" in comparison_fields:
        comparison.gate_reason_code = (
            "ANALYZER_CAPACITY_LIMIT"
        )
        comparison_update_fields.append("gate_reason_code")

    if "error" in comparison_fields:
        comparison.error = (
            "The API contract exceeds the synchronous analyzer "
            "limits and no background worker is currently enabled."
        )
        comparison_update_fields.append("error")

    if "quality_report" in comparison_fields:
        comparison.quality_report = {
            "status": "error",
            "reason_code": "ANALYZER_CAPACITY_LIMIT",
        }
        comparison_update_fields.append("quality_report")

    comparison.save(
        update_fields=list(
            dict.fromkeys(comparison_update_fields)
        )
    )


def _execute_sync(job: AnalysisJob) -> AnalysisJob:
    """
    Execute an analysis immediately.

    The job has already been committed to the database before this
    function is called.
    """

    return run_analysis_job(job)


def dispatch_ci_submission(
    submission_result: Mapping[str, Any],
) -> dict[str, Any]:
    """
    Decide how a newly-created CI job should execute.

    Important:
    This function must be called AFTER create_ci_submission()
    has completed its database transaction.
    """

    comparison = submission_result["comparison"]
    job = submission_result["job"]

    # Idempotent retry:
    # Never execute an already terminal job again.
    if job is None:
        return dict(submission_result)

    if job.status in {
        "completed",
        "failed",
        "cancelled",
    }:
        return dict(submission_result)

    # If another process already picked it up, do not run it again.
    if job.status == "running":
        return dict(submission_result)

    base_spec = comparison.old_specification.content
    head_spec = comparison.new_specification.content

    decision = decide_execution(
        base_spec,
        head_spec,
    )

    if decision.mode == "sync":
        try:
            processed_job = _execute_sync(job)

            # Refresh the comparison so the response reflects
            # PASS/WARN/FAIL immediately when available.
            comparison.refresh_from_db()

            result = dict(submission_result)
            result["job"] = processed_job
            result["comparison"] = comparison
            result["execution_mode"] = "sync"
            result["execution_reason"] = decision.reason

            return result

        except Exception:
            # run_analysis_job() already records the failure state.
            job.refresh_from_db()
            comparison.refresh_from_db()

            result = dict(submission_result)
            result["job"] = job
            result["comparison"] = comparison
            result["execution_mode"] = "sync"
            result["execution_reason"] = "ANALYZER_EXECUTION_FAILED"

            return result

    if decision.mode == "async":
        # The job remains queued.
        # A background worker will claim and execute it.
        result = dict(submission_result)
        result["execution_mode"] = "async"
        result["execution_reason"] = decision.reason

        return result

    # No worker and contract exceeds the synchronous capacity.
    _mark_capacity_error(job)

    job.refresh_from_db()
    comparison.refresh_from_db()

    result = dict(submission_result)
    result["job"] = job
    result["comparison"] = comparison
    result["execution_mode"] = "error"
    result["execution_reason"] = decision.reason

    return result


def build_ci_response(
    submission_result: Mapping[str, Any],
) -> dict[str, Any]:
    comparison = submission_result["comparison"]
    job = submission_result["job"]

    execution_mode = submission_result.get(
        "execution_mode"
    )

    # 200-style completed result when synchronous execution
    # already finished. Otherwise the caller can return 202.
    status = comparison.status

    return {
        "comparison_id": comparison.pk,
        "job_id": job.pk if job else None,
        "status": status,
        "gate_status": getattr(
            comparison,
            "gate_status",
            None,
        ),
        "report_url": (
            f"/api/comparisons/{comparison.pk}/"
        ),
        "created": submission_result["created"],
        "execution_mode": execution_mode,
        "execution_reason": submission_result.get(
            "execution_reason"
        ),
    }


__all__ = [
    "CISubmission",
    "CIValidationError",
    "build_ci_response",
    "create_ci_submission",
    "dispatch_ci_submission",
]