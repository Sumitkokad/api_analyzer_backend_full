import hashlib
import logging

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .ci_service import (
    CIValidationError,
    build_ci_response,
    create_ci_submission,
)
from .models import AnalysisJob, ProjectToken
from .services import run_analysis_job


logger = logging.getLogger(__name__)


MAX_CI_BODY_BYTES = 8 * 1024 * 1024


def _get_bearer_token(request):
    authorization = request.headers.get("Authorization", "")

    if not authorization:
        return None

    scheme, _, token = authorization.partition(" ")

    if scheme.lower() != "bearer":
        return None

    token = token.strip()

    if not token:
        return None

    return token


def _hash_token(token):
    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


def _authenticate_project_token(request):
    raw_token = _get_bearer_token(request)

    if not raw_token:
        return None

    token_hash = _hash_token(raw_token)

    token_record = (
        ProjectToken.objects
        .select_related("project")
        .filter(
            token_hash=token_hash,
            revoked_at__isnull=True,
        )
        .first()
    )

    if not token_record:
        return None

    token_record.last_used_at = timezone.now()
    token_record.save(
        update_fields=["last_used_at"]
    )

    return token_record


class CIAnalyzeView(APIView):
    """
    POST /api/ci/analyze

    Authenticates a project-scoped CI token and creates
    a queued comparison + analysis job.
    """

    parser_classes = (JSONParser,)
    authentication_classes = ()
    permission_classes = ()

    def post(self, request):
        # ---------------------------------------------------------
        # 1. Body-size protection
        # ---------------------------------------------------------
        content_length = request.META.get(
            "CONTENT_LENGTH"
        )

        if content_length:
            try:
                content_length = int(content_length)
            except (TypeError, ValueError):
                return Response(
                    {
                        "detail": "Invalid Content-Length."
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if content_length > MAX_CI_BODY_BYTES:
                return Response(
                    {
                        "detail": "Request body is too large."
                    },
                    status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                )

        # ---------------------------------------------------------
        # 2. Project-token authentication
        # ---------------------------------------------------------
        token_record = _authenticate_project_token(request)

        if token_record is None:
            return Response(
                {
                    "detail": "Invalid or revoked project token."
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        project = token_record.project

        # ---------------------------------------------------------
        # 3. Strict JSON object validation
        # ---------------------------------------------------------
        if not isinstance(request.data, dict):
            return Response(
                {
                    "detail": "Request body must be a JSON object."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        payload = request.data

        # ---------------------------------------------------------
        # 4. Project identity must match the authenticated token
        # ---------------------------------------------------------
        requested_project_id = payload.get(
            "project_id"
        )

        if requested_project_id is None:
            return Response(
                {
                    "project_id": [
                        "This field is required."
                    ]
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if str(requested_project_id) != str(project.pk):
            return Response(
                {
                    "project_id": [
                        "Project does not match the authenticated token."
                    ]
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # ---------------------------------------------------------
        # 5. Create idempotent CI submission
        # ---------------------------------------------------------
        try:
            with transaction.atomic():
                result = create_ci_submission(
                    project=project,
                    payload=payload,
                )

        except CIValidationError as exc:
            return Response(
                {
                    "detail": str(exc)
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        except Exception:
            logger.exception(
                "Unexpected CI submission error for project %s",
                project.pk,
            )

            return Response(
                {
                    "detail": "Unable to create CI analysis request."
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # ---------------------------------------------------------
        # 6. Return machine-readable CI response
        # ---------------------------------------------------------
        response_data = build_ci_response(result)

        return Response(
            response_data,
            status=status.HTTP_202_ACCEPTED,
        )


class CIProcessJobView(APIView):
    """
    POST /api/ci/jobs/<job_id>/process

    Securely triggers processing of one queued CI analysis job.

    This keeps AnalysisJob records and queue semantics intact,
    while allowing GitHub Actions to trigger processing without
    requiring a continuously running paid background worker.
    """

    parser_classes = (JSONParser,)
    authentication_classes = ()
    permission_classes = ()

    def post(self, request, job_id):
        # ---------------------------------------------------------
        # 1. Authenticate project token
        # ---------------------------------------------------------
        token_record = _authenticate_project_token(request)

        if token_record is None:
            return Response(
                {
                    "detail": "Invalid or revoked project token."
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        project = token_record.project

        # ---------------------------------------------------------
        # 2. Lock the specific job and verify project ownership
        # ---------------------------------------------------------
        with transaction.atomic():
            job = (
                AnalysisJob.objects
                .select_for_update()
                .select_related(
                    "project",
                    "comparison",
                )
                .filter(
                    pk=job_id,
                    project=project,
                )
                .first()
            )

            if job is None:
                return Response(
                    {
                        "detail": "Analysis job not found."
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Already finished. Nothing to process again.
            if job.status in {
                "completed",
                "failed",
            }:
                return Response(
                    {
                        "job_id": job.id,
                        "status": job.status,
                        "progress": job.progress,
                    },
                    status=status.HTTP_200_OK,
                )

            # Another process/request is already handling it.
            if job.status == "running":
                return Response(
                    {
                        "job_id": job.id,
                        "status": job.status,
                        "progress": job.progress,
                    },
                    status=status.HTTP_202_ACCEPTED,
                )

            # Only queued jobs may be triggered.
            if job.status != "queued":
                return Response(
                    {
                        "detail": (
                            f"Job cannot be processed from status "
                            f"'{job.status}'."
                        )
                    },
                    status=status.HTTP_409_CONFLICT,
                )

            # Claim the job before releasing the database lock.
            job.status = "running"
            job.started_at = timezone.now()
            job.progress = 10

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

            job.save(update_fields=update_fields)

        # ---------------------------------------------------------
        # 3. Process the claimed job
        # ---------------------------------------------------------
        try:
            job = run_analysis_job(job)

        except Exception:
            logger.exception(
                "CI analysis job %s failed during processing",
                job.id,
            )

            job.refresh_from_db()

            return Response(
                {
                    "job_id": job.id,
                    "status": job.status,
                    "progress": job.progress,
                    "error": job.error,
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # ---------------------------------------------------------
        # 4. Return final/updated job state
        # ---------------------------------------------------------
        return Response(
            {
                "job_id": job.id,
                "status": job.status,
                "progress": job.progress,
            },
            status=status.HTTP_200_OK,
        )


__all__ = [
    "CIAnalyzeView",
    "CIProcessJobView",
]