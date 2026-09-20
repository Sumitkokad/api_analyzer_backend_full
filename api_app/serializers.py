from __future__ import annotations

from typing import Any

from django.db import IntegrityError, transaction
from rest_framework import serializers

from .api_normalization import (
    load_openapi_from_bytes,
    load_openapi_from_dict,
)
from .models import (
    APIChangeRecord,
    APISpecification,
    AnalysisJob,
    Comparison,
    Dependency,
    Evidence,
    ImpactReportRecord,
    MigrationPlan,
    Project,
)
from .services import content_hash, run_comparison


MAX_SPEC_UPLOAD_BYTES = 4 * 1024 * 1024


def _request_user(serializer):
    request = serializer.context.get("request")

    if request is None:
        return None

    return getattr(request, "user", None)


def _assert_project_owner(project, user):
    if user is None or project.owner_id != user.id:
        raise serializers.ValidationError(
            {"project": "Project not found."}
        )


class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = ("id", "name", "description", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")

    def validate_name(self, value):
        user = self.context["request"].user
        name = value.strip()

        if not name:
            raise serializers.ValidationError(
                "Project name cannot be empty."
            )

        queryset = Project.objects.filter(
            owner=user,
            name=name,
        )

        # During update, don't compare the project against itself.
        if self.instance is not None:
            queryset = queryset.exclude(pk=self.instance.pk)

        if queryset.exists():
            raise serializers.ValidationError(
                "A project with this name already exists."
            )

        return name

    def create(self, validated_data):
        user = self.context["request"].user

        try:
            with transaction.atomic():
                return Project.objects.create(
                    owner=user,
                    **validated_data,
                )

        except IntegrityError as exc:
            # Protect against a race condition where two requests
            # try to create the same project at the same time.
            raise serializers.ValidationError(
                {
                    "name": "A project with this name already exists."
                }
            ) from exc


class APISpecificationSerializer(serializers.ModelSerializer):
    upload = serializers.FileField(
        write_only=True,
        required=False,
    )

    class Meta:
        model = APISpecification

        fields = (
            "id",
            "project",
            "name",
            "version",
            "content",
            "raw_text",
            "content_hash",
            "repository",
            "commit_sha",
            "path",
            "source_type",
            "openapi_version",
            "is_released",
            "released_at",
            "upload",
            "created_at",
            "updated_at",
        )

        read_only_fields = (
            "id",
            "content_hash",
            "is_released",
            "released_at",
            "created_at",
            "updated_at",
        )

        extra_kwargs = {
            "raw_text": {
                "write_only": True,
                "required": False,
            },
            "repository": {
                "required": False,
            },
            "commit_sha": {
                "required": False,
            },
            "path": {
                "required": False,
            },
            "source_type": {
                "required": False,
            },
            "openapi_version": {
                "required": False,
            },
        }

    def validate_project(self, project):
        _assert_project_owner(
            project,
            _request_user(self),
        )

        return project

    def validate(self, attrs):
        upload = attrs.pop(
            "upload",
            None,
        )

        if upload is not None:
            if upload.size > MAX_SPEC_UPLOAD_BYTES:
                raise serializers.ValidationError(
                    {
                        "upload": (
                            "Specification file is too large. "
                            f"Maximum size is "
                            f"{MAX_SPEC_UPLOAD_BYTES} bytes."
                        )
                    }
                )

            try:
                raw_bytes = upload.read()
            except Exception as exc:
                raise serializers.ValidationError(
                    {
                        "upload": (
                            "Unable to read specification file."
                        )
                    }
                ) from exc

            try:
                parsed_content = load_openapi_from_bytes(
                    raw_bytes,
                    filename=getattr(
                        upload,
                        "name",
                        None,
                    ),
                )
            except Exception as exc:
                raise serializers.ValidationError(
                    {
                        "upload": str(exc)
                    }
                ) from exc

            attrs["content"] = parsed_content
            attrs["raw_text"] = raw_bytes.decode(
                "utf-8"
            )

            attrs.setdefault(
                "source_type",
                "manual",
            )

            attrs.setdefault(
                "path",
                getattr(
                    upload,
                    "name",
                    "",
                ),
            )

        content = attrs.get("content")

        if content is None:
            raise serializers.ValidationError(
                {
                    "content": (
                        "Specification content is required."
                    )
                }
            )

        if not isinstance(content, dict):
            raise serializers.ValidationError(
                {
                    "content": (
                        "Specification must be a JSON/YAML object."
                    )
                }
            )

        try:
            validated_content = load_openapi_from_dict(
                content
            )
        except Exception as exc:
            raise serializers.ValidationError(
                {
                    "content": str(exc)
                }
            ) from exc

        attrs["content"] = validated_content
        attrs["content_hash"] = content_hash(
            validated_content
        )

        attrs.setdefault(
            "source_type",
            "manual",
        )

        attrs.setdefault(
            "raw_text",
            "",
        )

        return attrs

    def update(self, instance, validated_data):
        _assert_project_owner(
            instance.project,
            _request_user(self),
        )

        return super().update(
            instance,
            validated_data,
        )


class APIChangeRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = APIChangeRecord

        fields = (
            "id",
            "comparison",
            "change_id",
            "stable_hash",
            "rule_id",
            "change_type",
            "category",
            "endpoint",
            "method",
            "direction",
            "location",
            "parameter",
            "schema_path",
            "relation",
            "flags",
            "old_value",
            "new_value",
            "compatibility",
            "severity",
            "confidence",
            "source",
            "created_at",
            "updated_at",
        )

        read_only_fields = fields


class ImpactReportRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImpactReportRecord

        fields = (
            "id",
            "comparison",
            "change",
            "classification",
            "severity",
            "reason",
            "affected_components",
            "impact",
            "recommendation",
            "confidence",
            "confidence_label",
            "status",
            "input_hash",
            "prompt_version",
            "model",
            "evidence",
            "error",
            "llm_status",
            "llm_error",
            "created_at",
            "updated_at",
        )

        read_only_fields = fields


class EvidenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Evidence

        fields = (
            "id",
            "change",
            "source_type",
            "source",
            "location",
            "excerpt",
            "confidence",
            "retrieval",
            "created_at",
            "updated_at",
        )

        read_only_fields = fields


class MigrationPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = MigrationPlan
        fields = (
            "id",
            "comparison",
            "summary",
            "steps",
            "versioning_recommendation",
            "rollback",
            "created_at",
            "updated_at",
        )

        read_only_fields = (
            "id",
            "created_at",
            "updated_at",
        )


class DependencySerializer(serializers.ModelSerializer):
    class Meta:
        model = Dependency

        fields = (
            "id",
            "project",
            "source_identifier",
            "target_identifier",
            "relationship",
            "metadata",
            "created_at",
            "updated_at",
        )

        read_only_fields = (
            "id",
            "created_at",
            "updated_at",
        )

    def validate_project(self, project):
        _assert_project_owner(
            project,
            _request_user(self),
        )

        return project


class DetailedImpactReportSerializer(
    serializers.ModelSerializer
):
    class Meta:
        model = ImpactReportRecord

        fields = (
            "id",
            "classification",
            "severity",
            "reason",
            "affected_components",
            "impact",
            "recommendation",
            "confidence",
            "confidence_label",
            "status",
            "input_hash",
            "prompt_version",
            "model",
            "evidence",
            "error",
            "llm_status",
            "llm_error",
            "created_at",
            "updated_at",
        )


class DetailedAPIChangeSerializer(
    serializers.ModelSerializer
):
    deterministic = serializers.SerializerMethodField()
    llm_analysis = serializers.SerializerMethodField()
    evidence = serializers.SerializerMethodField()

    class Meta:
        model = APIChangeRecord

        fields = (
            "id",
            "change_id",
            "stable_hash",
            "rule_id",
            "change_type",
            "category",
            "endpoint",
            "method",
            "direction",
            "location",
            "parameter",
            "schema_path",
            "relation",
            "flags",
            "old_value",
            "new_value",
            "compatibility",
            "severity",
            "confidence",
            "source",
            "deterministic",
            "llm_analysis",
            "evidence",
            "created_at",
            "updated_at",
        )

    def get_deterministic(self, obj):
        return {
            "classification": obj.compatibility,
            "severity": obj.severity,
            "rule_id": obj.rule_id,
            "relation": obj.relation,
            "flags": obj.flags,
            "old_value": obj.old_value,
            "new_value": obj.new_value,
            "confidence": obj.confidence,
        }

    def get_llm_analysis(self, obj):
        report = (
            obj.impact_reports
            .order_by("-created_at")
            .first()
        )

        if not report:
            return None

        return DetailedImpactReportSerializer(
            report
        ).data

    def get_evidence(self, obj):
        return EvidenceSerializer(
            obj.evidence_records.all(),
            many=True,
        ).data


class ComparisonSerializer(
    serializers.ModelSerializer
):
    changes_count = serializers.IntegerField(
        source="changes.count",
        read_only=True,
    )

    changes = DetailedAPIChangeSerializer(
        many=True,
        read_only=True,
    )

    class Meta:
        model = Comparison

        fields = (
            "id",
            "project",
            "old_specification",
            "new_specification",
            "base_spec",
            "head_spec",
            "status",
            "summary",
            "error",
            "base_sha",
            "head_sha",
            "repository",
            "pull_request_number",
            "baseline_mode",
            "gate_status",
            "gate_reason_code",
            "summary_counts",
            "quality_report",
            "analyzer_version",
            "rules_version",
            "policy_version",
            "prompt_version",
            "llm_model",
            "idempotency_key",
            "changes_count",
            "changes",
            "created_at",
            "updated_at",
        )

        read_only_fields = (
            "id",
            "status",
            "summary",
            "error",
            "gate_status",
            "gate_reason_code",
            "summary_counts",
            "quality_report",
            "analyzer_version",
            "rules_version",
            "policy_version",
            "prompt_version",
            "llm_model",
            "changes_count",
            "changes",
            "created_at",
            "updated_at",
        )

    def validate(self, attrs):
        project = attrs["project"]

        _assert_project_owner(
            project,
            _request_user(self),
        )

        old_spec = attrs["old_specification"]
        new_spec = attrs["new_specification"]

        if old_spec.project_id != project.id:
            raise serializers.ValidationError(
                {
                    "old_specification": (
                        "Specification does not belong "
                        "to the selected project."
                    )
                }
            )

        if new_spec.project_id != project.id:
            raise serializers.ValidationError(
                {
                    "new_specification": (
                        "Specification does not belong "
                        "to the selected project."
                    )
                }
            )

        base_spec = attrs.get("base_spec")
        head_spec = attrs.get("head_spec")

        if (
            base_spec is not None
            and base_spec.project_id != project.id
        ):
            raise serializers.ValidationError(
                {
                    "base_spec": (
                        "Base specification does not "
                        "belong to the selected project."
                    )
                }
            )

        if (
            head_spec is not None
            and head_spec.project_id != project.id
        ):
            raise serializers.ValidationError(
                {
                    "head_spec": (
                        "Head specification does not "
                        "belong to the selected project."
                    )
                }
            )

        return attrs

    def create(self, validated_data):
        """
        Preserve the existing manual comparison flow.

        CI/async execution will use the dedicated job pipeline later.
        """
        comparison = Comparison.objects.create(
            **validated_data
        )

        run_comparison(comparison)

        return comparison


class AnalysisJobSerializer(
    serializers.ModelSerializer
):
    comparison_detail = serializers.SerializerMethodField()

    class Meta:
        model = AnalysisJob

        fields = (
            "id",
            "project",
            "comparison",
            "status",
            "stage",
            "progress",
            "attempts",
            "heartbeat_at",
            "error",
            "error_code",
            "error_detail",
            "result",
            "comparison_detail",
            "started_at",
            "completed_at",
            "created_at",
            "updated_at",
        )

        read_only_fields = (
            "id",
            "status",
            "stage",
            "progress",
            "attempts",
            "heartbeat_at",
            "error",
            "error_code",
            "error_detail",
            "result",
            "comparison_detail",
            "started_at",
            "completed_at",
            "created_at",
            "updated_at",
        )

    def get_comparison_detail(self, obj):
        if not obj.comparison_id:
            return None

        return ComparisonSerializer(
            obj.comparison,
            context=self.context,
        ).data

    def validate(self, attrs):
        project = attrs["project"]

        _assert_project_owner(
            project,
            _request_user(self),
        )

        comparison = attrs.get(
            "comparison"
        )

        if (
            comparison is not None
            and comparison.project_id != project.id
        ):
            raise serializers.ValidationError(
                {
                    "comparison": (
                        "Comparison does not belong "
                        "to the selected project."
                    )
                }
            )

        return attrs