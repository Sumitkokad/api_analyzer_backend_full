from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Project(TimeStampedModel):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="api_projects",
    )
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)

    class Meta:
        unique_together = ("owner", "name")
        ordering = ("name",)

    def __str__(self):
        return self.name


class APISpecification(TimeStampedModel):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="specifications",
    )
    name = models.CharField(max_length=150)
    version = models.CharField(max_length=80, blank=True)
    content = models.JSONField()
    raw_text = models.TextField(blank=True)
    content_hash = models.CharField(max_length=64, db_index=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("project", "content_hash")),
        ]

    def __str__(self):
        return f"{self.name} {self.version}".strip()


class Comparison(TimeStampedModel):
    STATUS_CHOICES = (
        ("queued", "Queued"),
        ("running", "Running"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    )

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="comparisons",
    )
    old_specification = models.ForeignKey(
        APISpecification,
        on_delete=models.CASCADE,
        related_name="old_comparisons",
    )
    new_specification = models.ForeignKey(
        APISpecification,
        on_delete=models.CASCADE,
        related_name="new_comparisons",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    summary = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("project", "status")),
        ]

    def __str__(self):
        return f"{self.project}: {self.old_specification} -> {self.new_specification}"


class APIChangeRecord(TimeStampedModel):
    comparison = models.ForeignKey(
        Comparison,
        on_delete=models.CASCADE,
        related_name="changes",
    )
    change_id = models.CharField(max_length=32, db_index=True)
    change_type = models.CharField(max_length=120)
    category = models.CharField(max_length=80, default="contract")
    endpoint = models.CharField(max_length=300)
    method = models.CharField(max_length=16, blank=True)
    direction = models.CharField(max_length=40, default="unknown")
    location = models.CharField(max_length=120, blank=True)
    parameter = models.CharField(max_length=200, blank=True)
    schema_path = models.CharField(max_length=300, blank=True)
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    compatibility = models.CharField(max_length=40, default="unknown")
    severity = models.CharField(max_length=20, blank=True)
    confidence = models.FloatField(default=1.0)
    source = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ("comparison", "change_id")
        ordering = ("endpoint", "method", "change_type", "parameter")

    def __str__(self):
        return f"{self.change_type} {self.endpoint}"


class Evidence(TimeStampedModel):
    change = models.ForeignKey(
        APIChangeRecord,
        on_delete=models.CASCADE,
        related_name="evidence_records",
    )
    source_type = models.CharField(max_length=40)
    source = models.CharField(max_length=500)
    location = models.CharField(max_length=200, blank=True)
    excerpt = models.TextField(blank=True)
    confidence = models.FloatField(default=1.0)


class ImpactReportRecord(TimeStampedModel):
    comparison = models.ForeignKey(
        Comparison,
        on_delete=models.CASCADE,
        related_name="impact_reports",
    )
    change = models.ForeignKey(
        APIChangeRecord,
        on_delete=models.CASCADE,
        related_name="impact_reports",
        null=True,
        blank=True,
    )
    classification = models.CharField(max_length=40)
    severity = models.CharField(max_length=20)
    reason = models.TextField(blank=True)
    affected_components = models.JSONField(default=list, blank=True)
    impact = models.TextField()
    recommendation = models.TextField()
    confidence = models.FloatField(default=1.0)
    llm_status = models.CharField(max_length=20, default="pending")
    llm_error = models.TextField(blank=True)


class MigrationPlan(TimeStampedModel):
    comparison = models.OneToOneField(
        Comparison,
        on_delete=models.CASCADE,
        related_name="migration_plan",
    )
    summary = models.TextField()
    steps = models.JSONField(default=list, blank=True)
    versioning_recommendation = models.TextField()
    rollback = models.TextField(blank=True)


class Consumer(TimeStampedModel):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="consumers",
    )
    name = models.CharField(max_length=150)
    consumer_type = models.CharField(max_length=80, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ("project", "name")


class Dependency(TimeStampedModel):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="dependencies",
    )
    source_identifier = models.CharField(max_length=300)
    target_identifier = models.CharField(max_length=300)
    relationship = models.CharField(max_length=120)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=("project", "source_identifier")),
            models.Index(fields=("project", "target_identifier")),
        ]


class AnalysisJob(TimeStampedModel):
    STATUS_CHOICES = (
        ("queued", "Queued"),
        ("running", "Running"),
        ("completed", "Completed"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
    )

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="analysis_jobs",
    )
    comparison = models.ForeignKey(
        Comparison,
        on_delete=models.SET_NULL,
        related_name="analysis_jobs",
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    progress = models.PositiveSmallIntegerField(default=0)
    error = models.TextField(blank=True)
    result = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
