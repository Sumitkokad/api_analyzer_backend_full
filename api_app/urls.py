from django.urls import path
from rest_framework.routers import DefaultRouter
from .ci_status_views import CIComparisonStatusView

from .ci_views import CIAnalyzeView , CIProcessJobView
from .views import (
    APISpecificationViewSet,
    AnalysisJobViewSet,
    ComparisonViewSet,
    DependencyViewSet,
    ProjectViewSet,
)

router = DefaultRouter()

router.register(
    "projects",
    ProjectViewSet,
    basename="project",
)

router.register(
    "specifications",
    APISpecificationViewSet,
    basename="specification",
)

router.register(
    "comparisons",
    ComparisonViewSet,
    basename="comparison",
)

router.register(
    "analysis-jobs",
    AnalysisJobViewSet,
    basename="analysis-job",
)

router.register(
    "dependencies",
    DependencyViewSet,
    basename="dependency",
)

urlpatterns = [
    path("ci/analyze", CIAnalyzeView.as_view(), name="ci-analyze"),
    path(
        "ci/jobs/<int:job_id>/process",
        CIProcessJobView.as_view(),
        name="ci-process-job",
    ),

    path(
    "ci/comparisons/<int:comparison_id>/status/",
    CIComparisonStatusView.as_view(),
    name="ci-comparison-status",
    ),
    *router.urls,
]