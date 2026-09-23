from django.urls import path
from rest_framework.routers import DefaultRouter

from .ci_status_views import CIComparisonStatusView
from .ci_views import CIAnalyzeView

from .github_views import (
    GitHubConnectionView,
    GitHubDisconnectView,
    GitHubInstallCallbackView,
    GitHubInstallStartView,
    GitHubRepositoryConnectView,
    GitHubRepositoryListView,
)

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
    path(
        "github/install/start/",
        GitHubInstallStartView.as_view(),
        name="github-install-start",
    ),
    path(
        "github/install/callback/",
        GitHubInstallCallbackView.as_view(),
        name="github-install-callback",
    ),
    path(
        "github/connection/",
        GitHubConnectionView.as_view(),
        name="github-connection",
    ),
    path(
        "github/repositories/",
        GitHubRepositoryListView.as_view(),
        name="github-repositories",
    ),
    path(
        "github/repositories/connect/",
        GitHubRepositoryConnectView.as_view(),
        name="github-repository-connect",
    ),
    path(
        "github/disconnect/",
        GitHubDisconnectView.as_view(),
        name="github-disconnect",
    ),
    path(
        "ci/analyze",
        CIAnalyzeView.as_view(),
        name="ci-analyze",
    ),

    path(
        "ci/comparisons/<int:comparison_id>/status/",
        CIComparisonStatusView.as_view(),
        name="ci-comparison-status",
    ),

    *router.urls,
]