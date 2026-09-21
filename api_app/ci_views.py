import hashlib
import logging

from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .ci_service import (
    CIValidationError,
    build_ci_response,
    create_ci_submission,
    dispatch_ci_submission,
)
from .models import ProjectToken


logger = logging.getLogger(__name__)


MAX_CI_BODY_BYTES = getattr(
    settings,
    "API_ANALYZER_MAX_CI_BODY_BYTES",
    16 * 1024 * 1024,
)


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

    Authenticates a project-scoped CI token, creates the immutable
    CI comparison/job records, and then dispatches execution according
    to the backend execution policy.

    Execution policy:

        small contract
            -> synchronous execution

        large contract + worker enabled
            -> asynchronous execution

        large contract + worker disabled
            -> terminal capacity error
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
        # 4. Project identity must match authenticated token
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
        # 5. Create immutable/idempotent submission
        # ---------------------------------------------------------
        try:
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
        # 6. Dispatch AFTER the database transaction has committed
        # ---------------------------------------------------------
        #
        # This is important.
        #
        # create_ci_submission() commits:
        #
        #   APISpecification
        #   APISpecification
        #   Comparison
        #   AnalysisJob
        #
        # before dispatch_ci_submission() can execute the analysis.
        #
        try:
            result = dispatch_ci_submission(result)

        except Exception:
            logger.exception(
                "Unexpected CI dispatch error for project %s",
                project.pk,
            )

            return Response(
                {
                    "detail": "Unable to dispatch CI analysis request."
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # ---------------------------------------------------------
        # 7. Build machine-readable response
        # ---------------------------------------------------------
        response_data = build_ci_response(result)

        current_status = response_data.get(
            "status"
        )

        # Synchronous execution has already completed.
        if current_status in {
            "completed",
            "failed",
            "cancelled",
        }:
            return Response(
                response_data,
                status=status.HTTP_200_OK,
            )

        # Asynchronous execution remains queued/running.
        return Response(
            response_data,
            status=status.HTTP_202_ACCEPTED,
        )


__all__ = [
    "CIAnalyzeView",
]