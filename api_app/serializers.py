import json

import yaml
from rest_framework import serializers

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


class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = ("id", "name", "description", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")

    def create(self, validated_data):
        return Project.objects.create(owner=self.context["request"].user, **validated_data)


class APISpecificationSerializer(serializers.ModelSerializer):
    upload = serializers.FileField(write_only=True, required=False)

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
            "upload",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "content_hash", "created_at", "updated_at")
        extra_kwargs = {"raw_text": {"write_only": True, "required": False}}

    def validate_project(self, project):
        if project.owner != self.context["request"].user:
            raise serializers.ValidationError("Project not found.")
        return project

    def validate(self, attrs):
        upload = attrs.pop("upload", None)
        if upload:
            if upload.size > 2 * 1024 * 1024:
                raise serializers.ValidationError({"upload": "Specification file is too large."})
            raw_text = upload.read().decode("utf-8")
            attrs["raw_text"] = raw_text
            try:
                attrs["content"] = json.loads(raw_text) if upload.name.endswith(".json") else yaml.safe_load(raw_text)
            except (json.JSONDecodeError, yaml.YAMLError, UnicodeDecodeError) as exc:
                raise serializers.ValidationError({"upload": "Malformed YAML or JSON specification."}) from exc

        if not attrs.get("content"):
            raise serializers.ValidationError({"content": "Specification content is required."})
        attrs["content_hash"] = content_hash(attrs["content"])
        return attrs


class APIChangeRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = APIChangeRecord
        fields = "__all__"


class ImpactReportRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImpactReportRecord
        fields = "__all__"


class EvidenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Evidence
        fields = "__all__"


class MigrationPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = MigrationPlan
        fields = "__all__"


class DependencySerializer(serializers.ModelSerializer):
    class Meta:
        model = Dependency
        fields = "__all__"


class DetailedImpactReportSerializer(serializers.ModelSerializer):
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
            "llm_status",
            "llm_error",
            "created_at",
            "updated_at",
        )


class DetailedAPIChangeSerializer(serializers.ModelSerializer):
    deterministic = serializers.SerializerMethodField()
    llm_analysis = serializers.SerializerMethodField()
    evidence = serializers.SerializerMethodField()

    class Meta:
        model = APIChangeRecord
        fields = (
            "id",
            "change_id",
            "change_type",
            "category",
            "endpoint",
            "method",
            "direction",
            "location",
            "parameter",
            "schema_path",
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
            "old_value": obj.old_value,
            "new_value": obj.new_value,
            "confidence": obj.confidence,
        }

    def get_llm_analysis(self, obj):
        report = obj.impact_reports.first()
        if not report:
            return None
        return DetailedImpactReportSerializer(report).data

    def get_evidence(self, obj):
        return EvidenceSerializer(obj.evidence_records.all(), many=True).data


class ComparisonSerializer(serializers.ModelSerializer):
    changes_count = serializers.IntegerField(source="changes.count", read_only=True)
    changes = DetailedAPIChangeSerializer(many=True, read_only=True)

    class Meta:
        model = Comparison
        fields = (
            "id",
            "project",
            "old_specification",
            "new_specification",
            "status",
            "summary",
            "error",
            "changes_count",
            "changes",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "status", "summary", "error", "changes_count", "changes", "created_at", "updated_at")

    def validate(self, attrs):
        request = self.context["request"]
        project = attrs["project"]
        old_spec = attrs["old_specification"]
        new_spec = attrs["new_specification"]
        if project.owner != request.user:
            raise serializers.ValidationError({"project": "Project not found."})
        if old_spec.project_id != project.id or new_spec.project_id != project.id:
            raise serializers.ValidationError("Specifications must belong to the selected project.")
        return attrs

    def create(self, validated_data):
        comparison = Comparison.objects.create(**validated_data)
        run_comparison(comparison)
        return comparison


class AnalysisJobSerializer(serializers.ModelSerializer):
    comparison_detail = serializers.SerializerMethodField()

    class Meta:
        model = AnalysisJob
        fields = (
            "id",
            "project",
            "comparison",
            "status",
            "progress",
            "error",
            "result",
            "comparison_detail",
            "started_at",
            "completed_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "status", "progress", "error", "result", "comparison_detail", "started_at", "completed_at", "created_at", "updated_at")

    def get_comparison_detail(self, obj):
        if not obj.comparison_id:
            return None
        return ComparisonSerializer(obj.comparison, context=self.context).data

    def validate(self, attrs):
        request = self.context["request"]
        project = attrs["project"]
        comparison = attrs.get("comparison")
        if project.owner != request.user:
            raise serializers.ValidationError({"project": "Project not found."})
        if comparison and comparison.project_id != project.id:
            raise serializers.ValidationError({"comparison": "Comparison not found."})
        return attrs
