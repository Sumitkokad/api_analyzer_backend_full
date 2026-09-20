from __future__ import annotations
import hashlib
import secrets
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import (
    APISpecification,
    AnalysisJob,
    Comparison,
    Dependency,
    Project,
    ProjectToken,
)
from .permissions import IsOwner
from .serializers import (
    APISpecificationSerializer,
    AnalysisJobSerializer,
    ComparisonSerializer,
    DependencySerializer,
    DetailedAPIChangeSerializer,
    EvidenceSerializer,
    ImpactReportRecordSerializer,
    MigrationPlanSerializer,
    ProjectSerializer,
)
from .services import run_analysis_job


class OwnedQuerySetMixin:
    permission_classes = (IsOwner,)

    def get_queryset(self):
        return self.queryset.filter(
            project__owner=self.request.user
        )


class ProjectViewSet(viewsets.ModelViewSet):
    serializer_class = ProjectSerializer
    permission_classes = (IsOwner,)

    def get_queryset(self):
        return Project.objects.filter(owner=self.request.user)

    @action(
        detail=True,
        methods=["post"],
        url_path="tokens",
    )
    def create_token(self, request, pk=None):
        project = self.get_object()

        token_name = request.data.get(
            "name",
            "GitHub Actions CI",
        ).strip()

        if not token_name:
            token_name = "GitHub Actions CI"

        raw_token = secrets.token_urlsafe(48)

        token_hash = hashlib.sha256(
            raw_token.encode("utf-8")
        ).hexdigest()

        token = ProjectToken.objects.create(
            project=project,
            name=token_name,
            token_hash=token_hash,
        )

        return Response(
            {
                "id": token.id,
                "project_id": project.id,
                "name": token.name,
                "token": raw_token,
                "warning": "Copy this token now. It will not be shown again.",
            },
            status=201,
        )


class APISpecificationViewSet(
    OwnedQuerySetMixin,
    viewsets.ModelViewSet,
):
    serializer_class = APISpecificationSerializer

    queryset = APISpecification.objects.select_related(
        "project"
    )


class ComparisonViewSet(
    OwnedQuerySetMixin,
    viewsets.ModelViewSet,
):
    serializer_class = ComparisonSerializer

    queryset = (
        Comparison.objects
        .select_related(
            "project",
            "old_specification",
            "new_specification",
            "base_spec",
            "head_spec",
        )
        .prefetch_related(
            "changes__impact_reports",
            "changes__evidence_records",
        )
    )

    @action(
        detail=True,
        methods=["get"],
        url_path="status",
    )
    def comparison_status(self, request, pk=None):
        comparison = self.get_object()

        return Response(
            {
                "id": comparison.id,
                "status": comparison.status,
                "gate_status": comparison.gate_status,
                "gate_reason_code": (
                    comparison.gate_reason_code
                ),
                "summary_counts": (
                    comparison.summary_counts
                ),
                "quality": (
                    comparison.quality_report
                ),
                "error": comparison.error,
            }
        )

    @action(
        detail=True,
        methods=["get"],
    )
    def changes(self, request, pk=None):
        comparison = self.get_object()

        serializer = DetailedAPIChangeSerializer(
            comparison.changes.all(),
            many=True,
            context=self.get_serializer_context(),
        )

        return Response(serializer.data)

    @action(
        detail=True,
        methods=["get"],
    )
    def impact(self, request, pk=None):
        comparison = self.get_object()

        serializer = ImpactReportRecordSerializer(
            comparison.impact_reports.all(),
            many=True,
            context=self.get_serializer_context(),
        )

        return Response(serializer.data)

    @action(
        detail=True,
        methods=["get"],
    )
    def evidence(self, request, pk=None):
        comparison = self.get_object()

        records = []

        for change in comparison.changes.all():
            records.extend(
                change.evidence_records.all()
            )

        serializer = EvidenceSerializer(
            records,
            many=True,
            context=self.get_serializer_context(),
        )

        return Response(serializer.data)

    @action(
        detail=True,
        methods=["get"],
    )
    def migration(self, request, pk=None):
        comparison = self.get_object()

        try:
            migration_plan = comparison.migration_plan
        except Comparison.migration_plan.RelatedObjectDoesNotExist:
            return Response({})

        serializer = MigrationPlanSerializer(
            migration_plan,
            context=self.get_serializer_context(),
        )

        return Response(serializer.data)

    @action(
        detail=True,
        methods=["get"],
        url_path="quality",
    )
    def quality(self, request, pk=None):
        comparison = self.get_object()

        return Response(
            comparison.quality_report or {}
        )


class AnalysisJobViewSet(
    OwnedQuerySetMixin,
    viewsets.ModelViewSet,
):
    serializer_class = AnalysisJobSerializer

    queryset = (
        AnalysisJob.objects
        .select_related(
            "project",
            "comparison",
        )
        .prefetch_related(
            "comparison__changes__impact_reports",
            "comparison__changes__evidence_records",
        )
    )

    @action(
        detail=True,
        methods=["get"],
        url_path="status",
    )
    def job_status(self, request, pk=None):
        job = self.get_object()

        return Response(
            {
                "id": job.id,
                "status": job.status,
                "stage": job.stage,
                "progress": job.progress,
                "attempts": job.attempts,
                "heartbeat_at": job.heartbeat_at,
                "error_code": job.error_code,
                "error": job.error,
                "comparison_id": job.comparison_id,
            }
        )

    def perform_create(self, serializer):
        job = serializer.save(
            status="queued"
        )

        # Preserve the existing local/manual behavior.
        # Real asynchronous execution will be introduced
        # in the CI pipeline phase.
        run_analysis_job(job)


class DependencyViewSet(
    OwnedQuerySetMixin,
    viewsets.ReadOnlyModelViewSet,
):
    serializer_class = DependencySerializer

    queryset = Dependency.objects.select_related(
        "project"
    )