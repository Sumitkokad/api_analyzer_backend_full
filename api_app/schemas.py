import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, Field


class APIChange(BaseModel):
    change_id: str | None = None
    category: str = "contract"
    change_type: str
    endpoint: str
    method: str | None = None
    direction: Literal[
        "request",
        "response",
        "endpoint",
        "security",
        "metadata",
        "unknown",
    ] = "unknown"
    location: str | None = None
    parameter: str | None = None
    schema_name: str | None = None
    schema_path: str | None = None
    old_value: Any | None = None
    new_value: Any | None = None
    compatibility: Literal[
        "breaking",
        "non-breaking",
        "potentially-breaking",
        "unknown",
    ] = "unknown"
    severity: Literal["low", "medium", "high", "critical"] | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)

    def __init__(self, **data):
        super().__init__(**data)
        if not self.change_id:
            object.__setattr__(self, "change_id", self.compute_change_id())

    def compute_change_id(self) -> str:
        payload = {
            "change_type": self.change_type,
            "endpoint": self.endpoint,
            "method": self.method,
            "direction": self.direction,
            "location": self.location,
            "parameter": self.parameter,
            "schema_name": self.schema_name,
            "schema_path": self.schema_path,
            "old_value": self.old_value,
            "new_value": self.new_value,
        }
        raw = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class ImpactReport(BaseModel):
    classification: Literal["breaking", "non-breaking", "potentially-breaking"]
    severity: Literal["low", "medium", "high", "critical"]
    reason: str
    affected_components: list[str]
    impact: str
    recommendation: str
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class Evidence(BaseModel):
    source_type: Literal["documentation", "source", "schema", "rule"]
    source: str
    location: str | None = None
    excerpt: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class MigrationPlan(BaseModel):
    summary: str
    steps: list[str]
    versioning_recommendation: str
    rollback: str | None = None
