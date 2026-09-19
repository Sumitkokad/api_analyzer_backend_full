from __future__ import annotations

from pathlib import Path
from typing import Any

from .api_normalization import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_NODES,
    DEFAULT_MAX_YAML_ALIASES,
    DEFAULT_MAX_YAML_ANCHORS,
    load_openapi_document,
    load_openapi_from_bytes,
    load_openapi_from_dict,
)


def load_api_spec(
    file_path: str | Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
    max_aliases: int = DEFAULT_MAX_YAML_ALIASES,
    max_anchors: int = DEFAULT_MAX_YAML_ANCHORS,
) -> dict[str, Any]:
    """
    Backwards-compatible file-path API.

    Existing callers can continue using:

        load_api_spec("openapi.json")
    """
    return load_openapi_document(
        file_path,
        max_bytes=max_bytes,
        max_depth=max_depth,
        max_nodes=max_nodes,
        max_aliases=max_aliases,
        max_anchors=max_anchors,
    )


def load_api_spec_from_file(
    file_path: str | Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
    max_aliases: int = DEFAULT_MAX_YAML_ALIASES,
    max_anchors: int = DEFAULT_MAX_YAML_ANCHORS,
) -> dict[str, Any]:
    """
    Explicit file-based ingestion API.

    Kept separate from load_api_spec() so future code can use a clearer
    function name while old callers remain unchanged.
    """
    return load_openapi_document(
        file_path,
        max_bytes=max_bytes,
        max_depth=max_depth,
        max_nodes=max_nodes,
        max_aliases=max_aliases,
        max_anchors=max_anchors,
    )


def load_api_spec_from_bytes(
    data: bytes,
    *,
    filename: str | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
    max_aliases: int = DEFAULT_MAX_YAML_ALIASES,
    max_anchors: int = DEFAULT_MAX_YAML_ANCHORS,
) -> dict[str, Any]:
    """
    CI/upload ingestion API.

    This is the function the future /api/ci/analyze endpoint can use when
    OpenAPI contracts arrive as uploaded bytes.
    """
    return load_openapi_from_bytes(
        data,
        filename=filename,
        max_bytes=max_bytes,
        max_depth=max_depth,
        max_nodes=max_nodes,
        max_aliases=max_aliases,
        max_anchors=max_anchors,
    )


def load_api_spec_from_dict(
    spec: dict[str, Any],
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> dict[str, Any]:
    """
    CI/API ingestion API for already-parsed JSON request bodies.
    """
    return load_openapi_from_dict(
        spec,
        max_depth=max_depth,
        max_nodes=max_nodes,
    )