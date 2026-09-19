from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import APISpecification, AnalysisJob, Comparison, Dependency, Project
from .permissions import IsOwner
from .serializers import (
    APIChangeRecordSerializer,
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
        return self.queryset.filter(project__owner=self.request.user)


class ProjectViewSet(viewsets.ModelViewSet):
    serializer_class = ProjectSerializer
    permission_classes = (IsOwner,)

    def get_queryset(self):
        return Project.objects.filter(owner=self.request.user)


class APISpecificationViewSet(OwnedQuerySetMixin, viewsets.ModelViewSet):
    serializer_class = APISpecificationSerializer
    queryset = APISpecification.objects.select_related("project")


class ComparisonViewSet(OwnedQuerySetMixin, viewsets.ModelViewSet):
    serializer_class = ComparisonSerializer
    queryset = Comparison.objects.select_related(
        "project",
        "old_specification",
        "new_specification",
    ).prefetch_related(
        "changes__impact_reports",
        "changes__evidence_records",
    )

    @action(detail=True, methods=["get"])
    def changes(self, request, pk=None):
        comparison = self.get_object()
        serializer = DetailedAPIChangeSerializer(comparison.changes.all(), many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def impact(self, request, pk=None):
        comparison = self.get_object()
        serializer = ImpactReportRecordSerializer(comparison.impact_reports.all(), many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def evidence(self, request, pk=None):
        comparison = self.get_object()
        records = []
        for change in comparison.changes.all():
            records.extend(change.evidence_records.all())
        serializer = EvidenceSerializer(records, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def migration(self, request, pk=None):
        comparison = self.get_object()
        if not hasattr(comparison, "migration_plan"):
            return Response({})
        serializer = MigrationPlanSerializer(comparison.migration_plan)
        return Response(serializer.data)


class AnalysisJobViewSet(OwnedQuerySetMixin, viewsets.ModelViewSet):
    serializer_class = AnalysisJobSerializer
    queryset = AnalysisJob.objects.select_related("project", "comparison").prefetch_related(
        "comparison__changes__impact_reports",
        "comparison__changes__evidence_records",
    )

    def perform_create(self, serializer):
        job = serializer.save(status="queued")
        run_analysis_job(job)


class DependencyViewSet(OwnedQuerySetMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = DependencySerializer
    queryset = Dependency.objects.select_related("project")
