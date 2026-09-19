from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional


QUALITY_ENGINE_VERSION = "2026.09.1"


@dataclass(frozen=True)
class QualityFinding:
    code: str
    severity: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QualityReport:
    quality_score: int
    operations_count: int
    operations_missing_responses: int
    empty_schema_count: int
    ignored_surface_count: int
    unresolved_ref_count: int
    recursive_ref_count: int
    registered_routes: Optional[int]
    covered_routes: Optional[int]
    route_coverage_percent: Optional[float]
    generator_warning_count: int
    findings: List[QualityFinding]
    engine_version: str = QUALITY_ENGINE_VERSION

    @property
    def is_low_quality(self) -> bool:
        return any(
            finding.code == "LOW_CONTRACT_COVERAGE"
            for finding in self.findings
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "quality_score": self.quality_score,
            "operations_count": self.operations_count,
            "operations_missing_responses": self.operations_missing_responses,
            "empty_schema_count": self.empty_schema_count,
            "ignored_surface_count": self.ignored_surface_count,
            "unresolved_ref_count": self.unresolved_ref_count,
            "recursive_ref_count": self.recursive_ref_count,
            "registered_routes": self.registered_routes,
            "covered_routes": self.covered_routes,
            "route_coverage_percent": self.route_coverage_percent,
            "generator_warning_count": self.generator_warning_count,
            "findings": [asdict(item) for item in self.findings],
            "engine_version": self.engine_version,
        }


def evaluate_contract_quality(
    spec: Mapping[str, Any],
    *,
    normalization_quality: Optional[Mapping[str, Any]] = None,
    registered_routes: Optional[Iterable[str]] = None,
    generator_warnings: Optional[Iterable[str]] = None,
    min_quality_score: int = 70,
    strict: bool = False,
) -> QualityReport:
    """
    Deterministically evaluate the quality of an OpenAPI contract.

    This function does not perform network calls, LLM calls, code execution,
    or database operations.

    Parameters
    ----------
    spec:
        Normalized OpenAPI document.

    normalization_quality:
        Optional counters produced by api_normalization.py.

    registered_routes:
        Optional route list from the running application's route registry.

    generator_warnings:
        Optional warnings produced by a contract generator.

    min_quality_score:
        Project quality threshold.

    strict:
        Used only to determine the finding severity for low-quality contracts.
    """

    if not isinstance(spec, Mapping):
        return _error_report(
            code="SCHEMA_PARSE_ERROR",
            message="Contract quality engine received a non-object specification.",
        )

    normalization_quality = normalization_quality or {}
    generator_warning_list = list(generator_warnings or [])

    operations = _collect_operations(spec)

    operations_count = len(operations)
    operations_missing_responses = sum(
        1 for _, operation in operations if _operation_has_missing_responses(operation)
    )

    empty_schema_count = _count_empty_schemas(spec)

    unresolved_ref_count = _counter(
        normalization_quality,
        "unresolved_refs",
        "unresolved_ref_count",
        "unresolved_references",
    )

    recursive_ref_count = _counter(
        normalization_quality,
        "recursive_refs",
        "recursive_ref_count",
        "recursive_ref",
    )

    dropped_keyword_count = _counter(
        normalization_quality,
        "dropped_keywords",
        "dropped_keyword_count",
        "ignored_keywords",
    )

    ignored_links_count = _counter(
        normalization_quality,
        "ignored_links",
        "ignored_link_count",
        "links_ignored",
    )

    ignored_surface_count = (
        dropped_keyword_count
        + ignored_links_count
        + unresolved_ref_count
        + recursive_ref_count
    )

    registered_route_list = (
        list(registered_routes) if registered_routes is not None else None
    )

    covered_routes = None
    route_coverage_percent = None

    if registered_route_list is not None:
        covered_routes = _count_covered_routes(
            registered_route_list,
            spec,
        )

        if len(registered_route_list) == 0:
            route_coverage_percent = 100.0 if operations_count == 0 else 0.0
        else:
            route_coverage_percent = round(
                (covered_routes / len(registered_route_list)) * 100,
                2,
            )

    findings: List[QualityFinding] = []

    if operations_count == 0:
        findings.append(
            QualityFinding(
                code="LOW_CONTRACT_COVERAGE",
                severity="error" if strict else "warning",
                message="The contract contains no documented API operations.",
                details={"operations_count": 0},
            )
        )

    if operations_missing_responses > 0:
        findings.append(
            QualityFinding(
                code="MISSING_RESPONSES",
                severity="warning",
                message="Some documented operations have no responses.",
                details={
                    "operations_missing_responses": operations_missing_responses,
                    "operations_count": operations_count,
                },
            )
        )

    if empty_schema_count > 0:
        findings.append(
            QualityFinding(
                code="EMPTY_SCHEMAS",
                severity="warning",
                message="The contract contains empty or effectively untyped schemas.",
                details={"empty_schema_count": empty_schema_count},
            )
        )

    if dropped_keyword_count > 0:
        findings.append(
            QualityFinding(
                code="IGNORED_NORMALIZATION_KEYWORDS",
                severity="warning",
                message="Some schema keywords were ignored during normalization.",
                details={"dropped_keyword_count": dropped_keyword_count},
            )
        )

    if ignored_links_count > 0:
        findings.append(
            QualityFinding(
                code="IGNORED_RESPONSE_LINKS",
                severity="warning",
                message="Response links were ignored during normalization.",
                details={"ignored_links_count": ignored_links_count},
            )
        )

    if unresolved_ref_count > 0:
        findings.append(
            QualityFinding(
                code="UNRESOLVED_REFS",
                severity="error",
                message="The contract contains unresolved references.",
                details={"unresolved_ref_count": unresolved_ref_count},
            )
        )

    if recursive_ref_count > 0:
        findings.append(
            QualityFinding(
                code="RECURSIVE_REFS",
                severity="warning",
                message="Recursive schema references were detected.",
                details={"recursive_ref_count": recursive_ref_count},
            )
        )

    if registered_route_list is not None and route_coverage_percent is not None:
        if route_coverage_percent < 100:
            findings.append(
                QualityFinding(
                    code="LOW_ROUTE_COVERAGE",
                    severity="warning",
                    message="The contract does not document all registered routes.",
                    details={
                        "registered_routes": len(registered_route_list),
                        "covered_routes": covered_routes,
                        "coverage_percent": route_coverage_percent,
                    },
                )
            )

    if generator_warning_list:
        findings.append(
            QualityFinding(
                code="GENERATOR_WARNINGS",
                severity="warning",
                message="The contract generator reported warnings.",
                details={
                    "generator_warning_count": len(generator_warning_list),
                    "warnings": generator_warning_list[:20],
                },
            )
        )

    quality_score = _calculate_quality_score(
        operations_count=operations_count,
        operations_missing_responses=operations_missing_responses,
        empty_schema_count=empty_schema_count,
        ignored_surface_count=ignored_surface_count,
        route_coverage_percent=route_coverage_percent,
        generator_warning_count=len(generator_warning_list),
    )

    if quality_score < min_quality_score:
        findings.append(
            QualityFinding(
                code="LOW_CONTRACT_COVERAGE",
                severity="error" if strict else "warning",
                message="Contract quality is below the configured minimum threshold.",
                details={
                    "quality_score": quality_score,
                    "min_quality_score": min_quality_score,
                },
            )
        )

    return QualityReport(
        quality_score=quality_score,
        operations_count=operations_count,
        operations_missing_responses=operations_missing_responses,
        empty_schema_count=empty_schema_count,
        ignored_surface_count=ignored_surface_count,
        unresolved_ref_count=unresolved_ref_count,
        recursive_ref_count=recursive_ref_count,
        registered_routes=(
            len(registered_route_list)
            if registered_route_list is not None
            else None
        ),
        covered_routes=covered_routes,
        route_coverage_percent=route_coverage_percent,
        generator_warning_count=len(generator_warning_list),
        findings=findings,
    )


def compute_quality_score(
    spec: Mapping[str, Any],
    *,
    normalization_quality: Optional[Mapping[str, Any]] = None,
    registered_routes: Optional[Iterable[str]] = None,
    generator_warnings: Optional[Iterable[str]] = None,
) -> int:
    """
    Convenience wrapper returning only the deterministic score.
    """
    report = evaluate_contract_quality(
        spec,
        normalization_quality=normalization_quality,
        registered_routes=registered_routes,
        generator_warnings=generator_warnings,
    )
    return report.quality_score


def _calculate_quality_score(
    *,
    operations_count: int,
    operations_missing_responses: int,
    empty_schema_count: int,
    ignored_surface_count: int,
    route_coverage_percent: Optional[float],
    generator_warning_count: int,
) -> int:
    """
    Score is intentionally conservative.

    Maximum = 100.

    Penalties:
    - missing responses: up to 30
    - empty/untyped schemas: up to 20
    - ignored normalization surfaces: up to 15
    - route coverage: up to 25
    - generator warnings: up to 10
    """

    if operations_count == 0:
        return 0

    score = 100.0

    missing_response_ratio = (
        operations_missing_responses / operations_count
        if operations_count
        else 1.0
    )
    score -= min(30.0, missing_response_ratio * 30.0)

    empty_schema_ratio = min(
        1.0,
        empty_schema_count / max(operations_count, 1),
    )
    score -= min(20.0, empty_schema_ratio * 20.0)

    score -= min(15.0, float(ignored_surface_count))

    if route_coverage_percent is not None:
        route_penalty = max(0.0, 100.0 - route_coverage_percent) * 0.25
        score -= min(25.0, route_penalty)

    score -= min(10.0, float(generator_warning_count))

    return max(0, min(100, round(score)))


def _collect_operations(
    spec: Mapping[str, Any],
) -> List[tuple[str, Mapping[str, Any]]]:
    operations: List[tuple[str, Mapping[str, Any]]] = []

    paths = spec.get("paths", {})

    if not isinstance(paths, Mapping):
        return operations

    valid_methods = {
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "head",
        "options",
        "trace",
    }

    for path, path_item in paths.items():
        if not isinstance(path_item, Mapping):
            continue

        for method, operation in path_item.items():
            if str(method).lower() not in valid_methods:
                continue

            if isinstance(operation, Mapping):
                operations.append(
                    (f"{method.upper()} {path}", operation)
                )

    return operations


def _operation_has_missing_responses(operation: Mapping[str, Any]) -> bool:
    responses = operation.get("responses")

    if not isinstance(responses, Mapping):
        return True

    return len(responses) == 0


def _count_empty_schemas(value: Any) -> int:
    count = 0

    if isinstance(value, Mapping):
        if _is_effectively_empty_schema(value):
            count += 1

        for child in value.values():
            count += _count_empty_schemas(child)

    elif isinstance(value, list):
        for item in value:
            count += _count_empty_schemas(item)

    return count


def _is_effectively_empty_schema(value: Mapping[str, Any]) -> bool:
    """
    Detect schemas that provide little/no usable type information.

    Examples:
        {}
        {"type": "object"}
        {"type": "object", "properties": {}}
        {"type": "object", "additionalProperties": True}
    """

    if not value:
        return True

    ignored = {
        "description",
        "title",
        "example",
        "examples",
        "default",
        "deprecated",
        "readOnly",
        "writeOnly",
        "nullable",
        "xml",
    }

    meaningful = {
        key: item
        for key, item in value.items()
        if key not in ignored
    }

    if not meaningful:
        return True

    if meaningful == {"type": "object"}:
        return True

    if meaningful.get("type") == "object":
        properties = meaningful.get("properties")

        if properties == {}:
            remaining = {
                key: item
                for key, item in meaningful.items()
                if key not in {"type", "properties"}
            }

            if not remaining:
                return True

        if (
            meaningful.get("additionalProperties") is True
            and set(meaningful.keys()) <= {
                "type",
                "additionalProperties",
            }
        ):
            return True

    return False


def _counter(
    source: Mapping[str, Any],
    *keys: str,
) -> int:
    total = 0

    for key in keys:
        value = source.get(key)

        if isinstance(value, bool):
            continue

        if isinstance(value, int):
            total = max(total, value)

    return total


def _count_covered_routes(
    registered_routes: Iterable[str],
    spec: Mapping[str, Any],
) -> int:
    documented_paths = {
        _normalize_route(str(path))
        for path in (spec.get("paths") or {})
        if isinstance(path, str)
    }

    covered = 0

    for route in registered_routes:
        if _normalize_route(str(route)) in documented_paths:
            covered += 1

    return covered


def _normalize_route(route: str) -> str:
    route = route.strip()

    if not route:
        return "/"

    if not route.startswith("/"):
        route = "/" + route

    if route != "/":
        route = route.rstrip("/")

    return route


def _error_report(
    *,
    code: str,
    message: str,
) -> QualityReport:
    return QualityReport(
        quality_score=0,
        operations_count=0,
        operations_missing_responses=0,
        empty_schema_count=0,
        ignored_surface_count=0,
        unresolved_ref_count=0,
        recursive_ref_count=0,
        registered_routes=None,
        covered_routes=None,
        route_coverage_percent=None,
        generator_warning_count=0,
        findings=[
            QualityFinding(
                code=code,
                severity="error",
                message=message,
            )
        ],
    )


__all__ = [
    "QUALITY_ENGINE_VERSION",
    "QualityFinding",
    "QualityReport",
    "compute_quality_score",
    "evaluate_contract_quality",
]