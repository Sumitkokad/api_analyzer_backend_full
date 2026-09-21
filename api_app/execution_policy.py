from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from django.conf import settings


HTTP_OPERATION_METHODS = {
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "options",
    "head",
    "trace",
}


@dataclass(frozen=True)
class ExecutionDecision:
    """
    Deterministic decision about how a CI analysis should be executed.

    mode:
        - sync  -> execute immediately in the web request
        - async -> leave queued for a background worker
        - error -> cannot safely execute on the current deployment
    """

    mode: str
    reason: str
    base_spec_bytes: int
    head_spec_bytes: int
    base_operation_count: int
    head_operation_count: int


def _serialized_size_bytes(spec: Any) -> int:
    """
    Return the UTF-8 JSON size of an OpenAPI specification.
    """
    return len(
        json.dumps(
            spec,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _count_operations(spec: Any) -> int:
    """
    Count OpenAPI operations.

    Only actual HTTP operation keys under `paths` are counted.
    Path-level metadata such as `parameters` is not counted.
    """

    if not isinstance(spec, dict):
        return 0

    paths = spec.get("paths", {})

    if not isinstance(paths, dict):
        return 0

    operation_count = 0

    for path_item in paths.values():
        if not isinstance(path_item, dict):
            continue

        for method in path_item:
            if method.lower() in HTTP_OPERATION_METHODS:
                operation_count += 1

    return operation_count


def decide_execution(
    base_spec: Any,
    head_spec: Any,
    *,
    worker_enabled: bool | None = None,
) -> ExecutionDecision:
    """
    Decide whether a CI comparison should run synchronously,
    asynchronously, or return a capacity error.

    Rules:

    1. Base and head specifications must each be <= configured
       maximum size.
    2. Base and head specifications must each contain <= configured
       maximum operation count.
    3. A contract within both limits can run synchronously.
    4. A contract exceeding limits:
       - runs asynchronously when a worker is available
       - otherwise returns an execution-capacity error
    """

    max_spec_bytes = getattr(
        settings,
        "API_ANALYZER_MAX_SPEC_BYTES",
        5 * 1024 * 1024,
    )

    max_operations = getattr(
        settings,
        "API_ANALYZER_MAX_OPERATIONS",
        200,
    )

    if worker_enabled is None:
        worker_enabled = getattr(
            settings,
            "API_ANALYZER_WORKER_ENABLED",
            False,
        )

    base_spec_bytes = _serialized_size_bytes(base_spec)
    head_spec_bytes = _serialized_size_bytes(head_spec)

    base_operation_count = _count_operations(base_spec)
    head_operation_count = _count_operations(head_spec)

    base_too_large = base_spec_bytes > max_spec_bytes
    head_too_large = head_spec_bytes > max_spec_bytes

    base_too_many_operations = (
        base_operation_count > max_operations
    )
    head_too_many_operations = (
        head_operation_count > max_operations
    )

    # ---------------------------------------------------------
    # Small enough for synchronous execution
    # ---------------------------------------------------------
    if not (
        base_too_large
        or head_too_large
        or base_too_many_operations
        or head_too_many_operations
    ):
        return ExecutionDecision(
            mode="sync",
            reason="WITHIN_SYNC_LIMITS",
            base_spec_bytes=base_spec_bytes,
            head_spec_bytes=head_spec_bytes,
            base_operation_count=base_operation_count,
            head_operation_count=head_operation_count,
        )

    # ---------------------------------------------------------
    # Contract is large, but a background worker is available
    # ---------------------------------------------------------
    if worker_enabled:
        return ExecutionDecision(
            mode="async",
            reason="EXCEEDS_SYNC_LIMITS",
            base_spec_bytes=base_spec_bytes,
            head_spec_bytes=head_spec_bytes,
            base_operation_count=base_operation_count,
            head_operation_count=head_operation_count,
        )

    # ---------------------------------------------------------
    # Contract is too large and no worker exists
    # ---------------------------------------------------------
    return ExecutionDecision(
        mode="error",
        reason="ANALYZER_CAPACITY_LIMIT",
        base_spec_bytes=base_spec_bytes,
        head_spec_bytes=head_spec_bytes,
        base_operation_count=base_operation_count,
        head_operation_count=head_operation_count,
    )