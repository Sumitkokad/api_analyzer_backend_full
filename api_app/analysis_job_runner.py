from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from .models import AnalysisJob
from .services import run_analysis_job


def claim_next_queued_job(
    project_id=None,
):
    """
    Atomically claim one queued analysis job.

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

        job.status = "running"
        job.started_at = timezone.now()
        job.progress = 5

        update_fields = [
            "status",
            "started_at",
            "progress",
            "updated_at",
        ]

        # These fields were added for job reliability.
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
            update_fields=update_fields
        )

        return job


def process_next_queued_job(
    project_id=None,
):
    """
    Claim one queued job and execute the existing
    analysis pipeline.

    Returns:
        AnalysisJob | None
    """

    job = claim_next_queued_job(
        project_id=project_id
    )

    if job is None:
        return None

    return run_analysis_job(job)


__all__ = [
    "claim_next_queued_job",
    "process_next_queued_job",
]