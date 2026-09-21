from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from .models import AnalysisJob
from .services import run_analysis_job


def _claim_job(job):
    """
    Mark a queued AnalysisJob as running.

    This helper assumes the caller already holds a row lock
    inside transaction.atomic().
    """

    job.status = "running"
    job.started_at = timezone.now()
    job.progress = 5

    update_fields = [
        "status",
        "started_at",
        "progress",
        "updated_at",
    ]

    model_fields = {
        field.name
        for field in AnalysisJob._meta.get_fields()
    }

    if "attempts" in model_fields:
        job.attempts += 1
        update_fields.append("attempts")

    if "heartbeat_at" in model_fields:
        job.heartbeat_at = timezone.now()
        update_fields.append("heartbeat_at")

    if "stage" in model_fields:
        job.stage = "received"
        update_fields.append("stage")

    job.save(
        update_fields=list(
            dict.fromkeys(update_fields)
        )
    )

    return job


def claim_job(job_id):
    """
    Atomically claim one specific queued job.

    This is used by synchronous CI execution.

    Returns:
        AnalysisJob | None

    None means the job could not be claimed because it either:
        - does not exist
        - is not queued
        - is currently locked by another transaction
    """

    with transaction.atomic():
        job = (
            AnalysisJob.objects
            .select_for_update(
                skip_locked=True
            )
            .select_related(
                "project",
                "comparison",
            )
            .filter(
                pk=job_id,
                status="queued",
            )
            .first()
        )

        if job is None:
            return None

        return _claim_job(job)


def claim_next_queued_job(
    project_id=None,
):
    """
    Atomically claim the oldest queued analysis job.

    This is used by a background worker.

    Returns:
        AnalysisJob | None
    """

    with transaction.atomic():
        queryset = (
            AnalysisJob.objects
            .select_for_update(
                skip_locked=True
            )
            .select_related(
                "project",
                "comparison",
            )
            .filter(
                status="queued"
            )
            .order_by(
                "created_at"
            )
        )

        if project_id is not None:
            queryset = queryset.filter(
                project_id=project_id
            )

        job = queryset.first()

        if job is None:
            return None

        return _claim_job(job)


def run_claimed_job(job):
    """
    Execute a job that has already been atomically claimed.

    The actual analysis implementation remains inside
    services.run_analysis_job().
    """

    return run_analysis_job(job)


def process_job(job_id):
    """
    Atomically claim and execute one specific queued job.

    Primarily useful for synchronous execution.

    Returns:
        AnalysisJob | None
    """

    job = claim_job(job_id)

    if job is None:
        return None

    return run_claimed_job(job)


def process_next_queued_job(
    project_id=None,
):
    """
    Claim one queued job and execute the existing
    analysis pipeline.

    Primarily used by the background worker.

    Returns:
        AnalysisJob | None
    """

    job = claim_next_queued_job(
        project_id=project_id
    )

    if job is None:
        return None

    return run_claimed_job(job)


__all__ = [
    "claim_job",
    "claim_next_queued_job",
    "run_claimed_job",
    "process_job",
    "process_next_queued_job",
]