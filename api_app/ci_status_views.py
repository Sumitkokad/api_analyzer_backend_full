from __future__ import annotations

import hashlib
import json

from django.http import JsonResponse
from django.utils import timezone
from django.views import View

from .models import AnalysisJob, Comparison, ProjectToken


class CIComparisonStatusView(View):
    """
    Return the current status of a CI-triggered comparison.

    Authentication:
        Authorization: Bearer <project-token>

    URL:
        GET /api/ci/comparisons/<comparison_id>/status/
    """

    def _authenticate_token(self, request):
        authorization = request.headers.get("Authorization", "")

        if not authorization.startswith("Bearer "):
            return None

        raw_token = authorization[7:].strip()

        if not raw_token:
            return None

        token_hash = hashlib.sha256(
            raw_token.encode("utf-8")
        ).hexdigest()

        token = (
            ProjectToken.objects
            .select_related("project")
            .filter(
                token_hash=token_hash,
                revoked_at__isnull=True,
            )
            .first()
        )

        if token is None:
            return None

        token.last_used_at = timezone.now()
        token.save(update_fields=["last_used_at"])

        return token

    def get(self, request, comparison_id):
        token = self._authenticate_token(request)

        if token is None:
            return JsonResponse(
                {"detail": "Invalid or missing project token."},
                status=401,
            )

        try:
            comparison = (
                Comparison.objects
                .select_related("project")
                .get(pk=comparison_id)
            )
        except Comparison.DoesNotExist:
            return JsonResponse(
                {"detail": "Comparison not found."},
                status=404,
            )

        # Project-scoped authorization.
        if comparison.project_id != token.project_id:
            return JsonResponse(
                {"detail": "You do not have access to this comparison."},
                status=403,
            )

        job = (
            AnalysisJob.objects
            .filter(comparison=comparison)
            .order_by("-created_at")
            .first()
        )

        job_status = getattr(job, "status", None)
        job_progress = getattr(job, "progress", None)
        job_stage = getattr(job, "stage", None)
        job_error_code = getattr(job, "error_code", None)
        job_error_detail = getattr(job, "error_detail", None)

        gate_status = getattr(comparison, "gate_status", None)
        gate_reason_code = getattr(comparison, "gate_reason_code", None)

        reason_code = gate_reason_code or job_error_code

        summary_counts = getattr(comparison, "summary_counts", None)
        quality_report = getattr(comparison, "quality_report", None)

        # Keep JSON fields JSON-safe even if a database value is null.
        if summary_counts is None:
            summary_counts = {}

        if quality_report is None:
            quality_report = {}

        response = {
            "comparison_id": comparison.id,
            "job_id": job.id if job else None,
            "status": job_status or getattr(comparison, "status", None),
            "gate_status": gate_status,
            "reason_code": reason_code,
            "counts": summary_counts,
            "quality": quality_report,
            "progress": job_progress,
            "stage": job_stage,
            "error": job_error_detail,
            "report_url": getattr(comparison, "report_url", None),
        }

        return JsonResponse(response, status=200)