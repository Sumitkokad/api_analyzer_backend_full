from rest_framework.routers import DefaultRouter

from .views import (
    APISpecificationViewSet,
    AnalysisJobViewSet,
    ComparisonViewSet,
    DependencyViewSet,
    ProjectViewSet,
)

router = DefaultRouter()
router.register("projects", ProjectViewSet, basename="project")
router.register("specifications", APISpecificationViewSet, basename="specification")
router.register("comparisons", ComparisonViewSet, basename="comparison")
router.register("analysis-jobs", AnalysisJobViewSet, basename="analysis-job")
router.register("dependencies", DependencyViewSet, basename="dependency")

urlpatterns = router.urls
