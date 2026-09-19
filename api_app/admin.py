from django.contrib import admin

from .models import (
    APIChangeRecord,
    APISpecification,
    AnalysisJob,
    Comparison,
    Consumer,
    Dependency,
    Evidence,
    ImpactReportRecord,
    MigrationPlan,
    Project,
)


admin.site.register(Project)
admin.site.register(APISpecification)
admin.site.register(Comparison)
admin.site.register(APIChangeRecord)
admin.site.register(Evidence)
admin.site.register(ImpactReportRecord)
admin.site.register(MigrationPlan)
admin.site.register(Consumer)
admin.site.register(Dependency)
admin.site.register(AnalysisJob)
