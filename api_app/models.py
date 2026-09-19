from __future__ import annotations

from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Project(TimeStampedModel):
    ERROR_MODE_CHOICES = (
        ("fail_closed", "Fail Closed"),
        ("fail_open", "Fail Open"),
    )

    RESPONSE_REQUIRED_POLICY_CHOICES = (
        ("tolerant_reader", "Tolerant Reader"),
        ("strict", "Strict"),
    )

    POTENTIALLY_BREAKING_POLICY_CHOICES = (
        ("warn", "Warn"),
        ("fail", "Fail"),
    )

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="api_projects",
    )
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)

    # GitHub / CI configuration
    repository_full_name = models.CharField(
        max_length=300,
        blank=True,
    )
    default_branch = models.CharField(
        max_length=120,
        blank=True,
    )
    baseline_mode = models.CharField(
        max_length=40,
        default="merge_base",
    )
    spec_path = models.CharField(
        max_length=500,
        blank=True,
    )
    adapter_type = models.CharField(
        max_length=120,
        blank=True,
    )

    # Gate / quality policy
    error_mode = models.CharField(
        max_length=20,
        choices=ERROR_MODE_CHOICES,
        default="fail_closed",
    )
    min_quality_score = models.PositiveSmallIntegerField(
        default=70,
    )
    response_added_required_policy = models.CharField(
        max_length=30,
        choices=RESPONSE_REQUIRED_POLICY_CHOICES,
        default="tolerant_reader",
    )
    fail_severity_threshold = models.CharField(
        max_length=20,
        default="high",
    )
    potentially_breaking_policy = models.CharField(
        max_length=20,
        choices=POTENTIALLY_BREAKING_POLICY_CHOICES,
        default="warn",
    )

    class Meta:
        unique_together = ("owner", "name")
        ordering = ("name",)

    def __str__(self):
        return self.name


class APISpecification(TimeStampedModel):
    SOURCE_TYPE_CHOICES = (
        ("generated", "Generated"),
        ("committed", "Committed"),
        ("published", "Published"),
        ("manual", "Manual"),
    )

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="specifications",
    )
    name = models.CharField(max_length=150)
    version = models.CharField(max_length=80, blank=True)

    # Existing storage
    content = models.JSONField()
    raw_text = models.TextField(blank=True)
    content_hash = models.CharField(
        max_length=64,
        db_index=True,
    )

    # Immutable snapshot metadata
    repository = models.CharField(
        max_length=300,
        blank=True,
    )
    commit_sha = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
    )
    path = models.CharField(
        max_length=500,
        blank=True,
    )
    source_type = models.CharField(
        max_length=20,
        choices=SOURCE_TYPE_CHOICES,
        default="manual",
    )
    openapi_version = models.CharField(
        max_length=30,
        blank=True,
    )
    is_released = models.BooleanField(
        default=False,
    )
    released_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(
                fields=("project", "content_hash")
            ),
            models.Index(
                fields=("project", "commit_sha")
            ),
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

    GATE_STATUS_CHOICES = (
        ("PASS", "Pass"),
        ("WARN", "Warn"),
        ("FAIL", "Fail"),
        ("ERROR", "Error"),
    )

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="comparisons",
    )

    # Existing manual-flow references
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

    # Explicit base/head snapshot references
    base_spec = models.ForeignKey(
        APISpecification,
        on_delete=models.SET_NULL,
        related_name="base_comparisons",
        null=True,
        blank=True,
    )
    head_spec = models.ForeignKey(
        APISpecification,
        on_delete=models.SET_NULL,
        related_name="head_comparisons",
        null=True,
        blank=True,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="queued",
    )

    summary = models.JSONField(
        default=dict,
        blank=True,
    )

    # CI / GitHub metadata
    base_sha = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
    )
    head_sha = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
    )
    repository = models.CharField(
        max_length=300,
        blank=True,
    )
    pull_request_number = models.PositiveIntegerField(
        null=True,
        blank=True,
    )
    baseline_mode = models.CharField(
        max_length=40,
        blank=True,
    )

    # Gate result
    gate_status = models.CharField(
        max_length=10,
        choices=GATE_STATUS_CHOICES,
        null=True,
        blank=True,
    )
    gate_reason_code = models.CharField(
        max_length=100,
        blank=True,
    )
    summary_counts = models.JSONField(
        default=dict,
        blank=True,
    )
    quality_report = models.JSONField(
        default=dict,
        blank=True,
    )

    # Reproducibility / analyzer versions
    analyzer_version = models.CharField(
        max_length=50,
        blank=True,
    )
    rules_version = models.CharField(
        max_length=50,
        blank=True,
    )
    policy_version = models.CharField(
        max_length=50,
        blank=True,
    )
    prompt_version = models.CharField(
        max_length=50,
        blank=True,
    )
    llm_model = models.CharField(
        max_length=200,
        blank=True,
    )

    # CI idempotency
    idempotency_key = models.CharField(
        max_length=300,
        null=True,
        blank=True,
    )

    error = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(
                fields=("project", "status")
            ),
            models.Index(
                fields=("repository", "head_sha")
            ),
            models.Index(
                fields=("project", "base_sha", "head_sha")
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("project", "idempotency_key"),
                name="unique_comparison_project_idempotency",
            ),
        ]

    def __str__(self):
        return (
            f"{self.project}: "
            f"{self.old_specification} -> "
            f"{self.new_specification}"
        )


class APIChangeRecord(TimeStampedModel):
    comparison = models.ForeignKey(
        Comparison,
        on_delete=models.CASCADE,
        related_name="changes",
    )
    change_id = models.CharField(
        max_length=32,
        db_index=True,
    )
    stable_hash = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
    )
    rule_id = models.CharField(
        max_length=150,
        blank=True,
    )

    change_type = models.CharField(
        max_length=120,
    )
    category = models.CharField(
        max_length=80,
        default="contract",
    )
    endpoint = models.CharField(
        max_length=300,
    )
    method = models.CharField(
        max_length=16,
        blank=True,
    )
    direction = models.CharField(
        max_length=40,
        default="unknown",
    )
    location = models.CharField(
        max_length=120,
        blank=True,
    )
    parameter = models.CharField(
        max_length=200,
        blank=True,
    )
    schema_path = models.CharField(
        max_length=300,
        blank=True,
    )

    relation = models.CharField(
        max_length=40,
        blank=True,
    )
    flags = models.JSONField(
        default=list,
        blank=True,
    )

    old_value = models.JSONField(
        null=True,
        blank=True,
    )
    new_value = models.JSONField(
        null=True,
        blank=True,
    )

    compatibility = models.CharField(
        max_length=40,
        default="unknown",
    )
    severity = models.CharField(
        max_length=20,
        blank=True,
    )
    confidence = models.FloatField(
        default=1.0,
    )
    source = models.JSONField(
        default=dict,
        blank=True,
    )

    class Meta:
        unique_together = (
            "comparison",
            "change_id",
        )
        ordering = (
            "endpoint",
            "method",
            "change_type",
            "parameter",
        )
        indexes = [
            models.Index(
                fields=("comparison", "stable_hash")
            ),
            models.Index(
                fields=("comparison", "rule_id")
            ),
        ]

    def __str__(self):
        return f"{self.change_type} {self.endpoint}"


class Evidence(TimeStampedModel):
    change = models.ForeignKey(
        APIChangeRecord,
        on_delete=models.CASCADE,
        related_name="evidence_records",
    )

    source_type = models.CharField(
        max_length=40,
    )
    source = models.CharField(
        max_length=500,
    )
    location = models.CharField(
        max_length=200,
        blank=True,
    )
    excerpt = models.TextField(
        blank=True,
    )
    confidence = models.FloatField(
        default=1.0,
    )

    # Phase 5 retrieval metadata
    retrieval = models.CharField(
        max_length=40,
        blank=True,
    )


class ImpactReportRecord(TimeStampedModel):
    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("generated", "Generated"),
        ("failed", "Failed"),
        ("skipped", "Skipped"),
    )

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

    # Deterministic fields remain authoritative
    classification = models.CharField(
        max_length=40,
    )
    severity = models.CharField(
        max_length=20,
    )

    reason = models.TextField(
        blank=True,
    )
    affected_components = models.JSONField(
        default=list,
        blank=True,
    )
    impact = models.TextField(
        blank=True,
    )
    recommendation = models.TextField(
        blank=True,
    )

    confidence = models.FloatField(
        default=1.0,
    )
    confidence_label = models.CharField(
        max_length=30,
        default="estimated",
    )

    # New lifecycle
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )

    input_hash = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
    )
    prompt_version = models.CharField(
        max_length=50,
        blank=True,
    )
    model = models.CharField(
        max_length=200,
        blank=True,
    )
    evidence = models.JSONField(
        default=list,
        blank=True,
    )

    error = models.TextField(
        blank=True,
    )

    # Backward compatibility with existing UI/API
    llm_status = models.CharField(
        max_length=20,
        default="pending",
    )
    llm_error = models.TextField(
        blank=True,
    )

    class Meta:
        indexes = [
            models.Index(
                fields=("comparison", "status")
            ),
            models.Index(
                fields=("change", "input_hash")
            ),
        ]


class MigrationPlan(TimeStampedModel):
    comparison = models.OneToOneField(
        Comparison,
        on_delete=models.CASCADE,
        related_name="migration_plan",
    )
    summary = models.TextField()
    steps = models.JSONField(
        default=list,
        blank=True,
    )
    versioning_recommendation = models.TextField()
    rollback = models.TextField(
        blank=True,
    )


class Consumer(TimeStampedModel):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="consumers",
    )
    name = models.CharField(
        max_length=150,
    )
    consumer_type = models.CharField(
        max_length=80,
        blank=True,
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
    )

    class Meta:
        unique_together = ("project", "name")


class Dependency(TimeStampedModel):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="dependencies",
    )
    source_identifier = models.CharField(
        max_length=300,
    )
    target_identifier = models.CharField(
        max_length=300,
    )
    relationship = models.CharField(
        max_length=120,
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
    )

    class Meta:
        indexes = [
            models.Index(
                fields=("project", "source_identifier")
            ),
            models.Index(
                fields=("project", "target_identifier")
            ),
        ]


class AnalysisJob(TimeStampedModel):
    STATUS_CHOICES = (
        ("queued", "Queued"),
        ("running", "Running"),
        ("completed", "Completed"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
    )

    STAGE_CHOICES = (
        ("queued", "Queued"),
        ("ingest", "Ingest"),
        ("quality", "Quality"),
        ("normalize", "Normalize"),
        ("diff", "Diff"),
        ("rules", "Rules"),
        ("policy", "Policy"),
        ("gate", "Gate"),
        ("evidence", "Evidence"),
        ("explanation", "Explanation"),
        ("publish", "Publish"),
        ("completed", "Completed"),
        ("failed", "Failed"),
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

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="queued",
    )
    stage = models.CharField(
        max_length=30,
        choices=STAGE_CHOICES,
        default="queued",
    )
    progress = models.PositiveSmallIntegerField(
        default=0,
    )
    attempts = models.PositiveIntegerField(
        default=0,
    )
    heartbeat_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    error = models.TextField(
        blank=True,
    )
    error_code = models.CharField(
        max_length=100,
        blank=True,
    )
    error_detail = models.TextField(
        blank=True,
    )

    result = models.JSONField(
        default=dict,
        blank=True,
    )

    started_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
    )


class GitHubConnection(TimeStampedModel):
    project = models.OneToOneField(
        Project,
        on_delete=models.CASCADE,
        related_name="github_connection",
    )
    installation_id = models.CharField(
        max_length=100,
        db_index=True,
    )
    repository_full_name = models.CharField(
        max_length=300,
    )
    connected = models.BooleanField(
        default=True,
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
    )

    def __str__(self):
        return self.repository_full_name


class Waiver(TimeStampedModel):
    comparison = models.ForeignKey(
        Comparison,
        on_delete=models.CASCADE,
        related_name="waivers",
    )
    rule_id = models.CharField(
        max_length=150,
    )
    endpoint = models.CharField(
        max_length=300,
        blank=True,
    )
    target = models.CharField(
        max_length=300,
        blank=True,
    )
    reason = models.TextField()
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="approved_api_waivers",
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    active = models.BooleanField(
        default=True,
    )

    class Meta:
        indexes = [
            models.Index(
                fields=("comparison", "rule_id")
            ),
        ]


class AuditLog(TimeStampedModel):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="audit_logs",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="api_audit_logs",
        null=True,
        blank=True,
    )
    action = models.CharField(
        max_length=120,
    )
    resource_type = models.CharField(
        max_length=120,
    )
    resource_id = models.CharField(
        max_length=120,
        blank=True,
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
    )

    class Meta:
        ordering = ("-created_at",)


class ProjectToken(TimeStampedModel):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="tokens",
    )
    name = models.CharField(
        max_length=120,
    )
    token_hash = models.CharField(
        max_length=128,
        unique=True,
        db_index=True,
    )
    last_used_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    revoked_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    class Meta:
        indexes = [
            models.Index(
                fields=("project", "revoked_at")
            ),
        ]

    @property
    def is_revoked(self):
        return self.revoked_at is not None