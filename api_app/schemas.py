from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# Shared vocabularies
# ---------------------------------------------------------------------------

Classification = Literal[
    "breaking",
    "non-breaking",
    "potentially-breaking",
]

Severity = Literal[
    "low",
    "medium",
    "high",
]

Direction = Literal[
    "request",
    "response",
    "endpoint",
    "security",
    "metadata",
    "unknown",
]

GateStatus = Literal[
    "PASS",
    "WARN",
    "FAIL",
    "ERROR",
]

ImpactStatus = Literal[
    "pending",
    "generated",
    "failed",
    "skipped",
]

ConfidenceLabel = Literal[
    "estimated",
]

EvidenceSource = Literal[
    "spec",
    "docs",
    "policy",
    "schema",
    "rule",
    "source",
]

EvidenceRetrieval = Literal[
    "exact",
    "keyword",
    "semantic",
    "none",
]


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _canonical_value(value: Any) -> Any:
    """
    Convert values into a deterministic JSON-compatible representation.

    Used for stable hashing and comparison identity.
    """
    if isinstance(value, dict):
        return {
            str(key): _canonical_value(item)
            for key, item in sorted(
                value.items(),
                key=lambda item: str(item[0]),
            )
        }

    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]

    if isinstance(value, set):
        return sorted(
            (_canonical_value(item) for item in value),
            key=lambda item: json.dumps(
                item,
                sort_keys=True,
                default=str,
            ),
        )

    return value


def _stable_json(value: Any) -> str:
    return json.dumps(
        _canonical_value(value),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(
        _stable_json(value).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# API Change
# ---------------------------------------------------------------------------

class APIChange(BaseModel):
    """
    Canonical semantic API change.

    Compatibility/severity are produced by the deterministic rule engine.
    LLM/RAG must never become the authority for these fields.
    """

    model_config = ConfigDict(
        extra="allow",
    )

    # Identity
    change_id: str | None = None
    stable_hash: str | None = None

    # Change information
    category: str = "contract"
    change_type: str
    endpoint: str
    method: str | None = None
    direction: Direction = "unknown"
    location: str | None = None
    parameter: str | None = None
    schema_name: str | None = None
    schema_path: str | None = None

    # Schema semantics
    keyword: str | None = None
    relation: str | None = None

    # Values
    old_value: Any | None = None
    new_value: Any | None = None

    # Deterministic rule result
    compatibility: Classification | Literal["unknown"] = "unknown"
    severity: Severity | None = None
    rule_id: str | None = None
    reason: str | None = None
    remediation_hint: str | None = None
    flags: list[str] = Field(default_factory=list)
    rules_version: str | None = None

    # Confidence/source
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
    )
    source: dict[str, Any] = Field(default_factory=dict)

    # Evidence attached after deterministic diff
    evidence: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def ensure_stable_identity(self) -> "APIChange":
        if not self.stable_hash:
            object.__setattr__(
                self,
                "stable_hash",
                self.compute_stable_hash(),
            )

        if not self.change_id:
            # Preserve the existing short change_id format for backwards
            # compatibility while stable_hash provides collision-safe identity.
            object.__setattr__(
                self,
                "change_id",
                self.stable_hash[:16],
            )

        return self

    def _identity_payload(self) -> dict[str, Any]:
        """
        Fields that define the semantic identity of a change.

        keyword + relation are deliberately included to prevent two changes
        on the same schema path from collapsing into one change.
        """
        return {
            "change_type": self.change_type,
            "category": self.category,
            "endpoint": self.endpoint,
            "method": self.method,
            "direction": self.direction,
            "location": self.location,
            "parameter": self.parameter,
            "schema_name": self.schema_name,
            "schema_path": self.schema_path,
            "keyword": self.keyword,
            "relation": self.relation,
            "old_value": self.old_value,
            "new_value": self.new_value,
        }

    def compute_stable_hash(self) -> str:
        """
        Full SHA-256 semantic identity.

        This is the preferred identity for deduplication.
        """
        return _sha256(self._identity_payload())

    def compute_change_id(self) -> str:
        """
        Backwards-compatible short ID.

        Existing database rows/API responses can continue using the short ID.
        New collision-safe code should use stable_hash.
        """
        return self.compute_stable_hash()[:16]


# ---------------------------------------------------------------------------
# Gate result
# ---------------------------------------------------------------------------

class GateResult(BaseModel):
    """
    Final deterministic CI decision.

    No LLM/RAG field is used to determine this result.
    """

    gate_status: GateStatus
    reason_code: str

    reason: str

    # Counts used by dashboard/CI output
    total_changes: int = 0
    breaking_changes: int = 0
    potentially_breaking_changes: int = 0
    non_breaking_changes: int = 0

    # Highest observed deterministic severity
    highest_severity: Severity | None = None

    # Useful for CI/dashboard drill-down
    change_ids: list[str] = Field(default_factory=list)
    waived_change_ids: list[str] = Field(default_factory=list)

    # Reproducibility
    rules_version: str | None = None
    policy_version: str | None = None

    # Policy settings used to produce the decision
    fail_severity_threshold: Severity = "high"
    potentially_breaking_policy: Literal["warn", "fail"] = "warn"

    # Errors/warnings that explain a non-PASS result
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Contract quality
# ---------------------------------------------------------------------------

class QualityFinding(BaseModel):
    code: str
    severity: Literal[
        "info",
        "warning",
        "error",
    ]
    message: str
    location: str | None = None
    count: int | None = None


class QualityReport(BaseModel):
    """
    Contract completeness/trustworthiness report.

    Quality is intentionally separate from compatibility classification.
    """

    quality_score: float = Field(
        default=100.0,
        ge=0.0,
        le=100.0,
    )

    min_quality_score: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
    )

    # Contract surface
    operations_count: int = 0
    operations_without_responses: int = 0

    empty_schemas: int = 0
    untyped_schemas: int = 0

    # Ignored/unresolved surface
    dropped_keywords: int = 0
    ignored_links: int = 0
    unresolved_refs: int = 0
    recursive_refs: int = 0

    # Route coverage
    registered_routes: list[str] = Field(default_factory=list)
    documented_routes: list[str] = Field(default_factory=list)
    undocumented_routes: list[str] = Field(default_factory=list)

    route_coverage: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )

    # Generator information
    generator_warnings: list[str] = Field(default_factory=list)

    # Findings
    findings: list[QualityFinding] = Field(default_factory=list)

    # Misc diagnostics
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Impact report
# ---------------------------------------------------------------------------

class ImpactReport(BaseModel):
    """
    Explanation/impact result.

    Classification and severity remain present for backwards compatibility,
    but production code must populate them from the deterministic RuleResult,
    not trust an LLM-generated classification.
    """

    classification: Classification
    severity: Severity

    reason: str
    affected_components: list[str] = Field(default_factory=list)
    impact: str
    recommendation: str

    evidence: list[dict[str, Any]] = Field(default_factory=list)

    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
    )

    # LLM lifecycle
    status: ImpactStatus = "pending"

    # Reproducibility/cache
    input_hash: str | None = None
    prompt_version: str | None = None
    model: str | None = None

    # The target architecture treats model confidence as estimated.
    confidence_label: ConfidenceLabel = "estimated"

    # Optional internal error information.
    error: str | None = None


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

class Evidence(BaseModel):
    """
    Evidence attached to an API change.

    The new source/retrieval fields support the deterministic-evidence-first
    architecture while retaining source_type/source for compatibility.
    """

    source_type: Literal[
        "documentation",
        "source",
        "schema",
        "rule",
        "spec",
        "docs",
        "policy",
    ]

    source: str
    location: str | None = None
    excerpt: str | None = None

    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
    )

    # New evidence classification
    retrieval: EvidenceRetrieval = "none"

    # Optional exact contract metadata
    endpoint: str | None = None
    schema_path: str | None = None
    old_fragment: Any | None = None
    new_fragment: Any | None = None


# ---------------------------------------------------------------------------
# Migration plan
# ---------------------------------------------------------------------------

class MigrationPlan(BaseModel):
    summary: str
    steps: list[str] = Field(default_factory=list)
    versioning_recommendation: str
    rollback: str | None = None


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

class CompatibilityPolicy(BaseModel):
    """
    Project policy consumed by the deterministic gate.
    """

    fail_severity_threshold: Severity = "high"

    potentially_breaking_policy: Literal[
        "warn",
        "fail",
    ] = "warn"

    response_added_required_policy: Literal[
        "tolerant_reader",
        "strict",
    ] = "tolerant_reader"

    min_quality_score: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
    )

    error_mode: Literal[
        "fail_closed",
        "fail_open",
    ] = "fail_closed"

    policy_version: str = "2026.09.1"


# ---------------------------------------------------------------------------
# Analysis/job result
# ---------------------------------------------------------------------------

class AnalysisResult(BaseModel):
    """
    High-level serialized result used by workers/API responses.
    """

    comparison_id: str | int | None = None
    job_id: str | int | None = None

    status: str
    gate: GateResult | None = None
    quality: QualityReport | None = None

    changes: list[APIChange] = Field(default_factory=list)

    error_code: str | None = None
    error_detail: str | None = None