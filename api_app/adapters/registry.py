from __future__ import annotations

"""
Contract adapter registry for API Analyzer.

The registry provides a framework-agnostic way to discover and select the
correct contract adapter for a repository.

Architecture:

    GitHub repository
          ↓
    Repository scanner
          ↓
    AdapterRegistry.detect()
          ↓
    Selected ContractAdapter
          ↓
    Contract acquisition
          ↓
    Common API Analyzer engine

The registry does not:
    - compare OpenAPI documents
    - classify breaking changes
    - run LLM/RAG
    - call GitHub APIs
    - execute customer code
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .base import (
    AdapterDetectionResult,
    ContractAdapter,
)


# ============================================================================
# Resolution result
# ============================================================================


@dataclass(frozen=True)
class AdapterResolution:
    """
    Result of adapter detection.

    supported:
        True when a registered adapter claims the repository.

    adapter:
        The selected adapter, or None when unsupported.

    detection:
        Detection evidence returned by the selected adapter.

    reason:
        Human-readable explanation when no adapter is selected.
    """

    supported: bool

    adapter: ContractAdapter | None = None

    detection: AdapterDetectionResult | None = None

    reason: str = ""


# ============================================================================
# Adapter registry
# ============================================================================


class AdapterRegistry:
    """
    Registry containing all available API contract adapters.

    Detection order is deterministic and follows registration order.

    More specific adapters should be registered before generic adapters.

    Example:

        DRF adapter
        FastAPI adapter
        Flask adapter
        generic Python adapter

    This allows the same analyzer core to support many technologies without
    embedding framework-specific logic inside the comparison engine.
    """

    def __init__(
        self,
        adapters: Iterable[ContractAdapter] | None = None,
    ) -> None:
        self._adapters: dict[str, ContractAdapter] = {}

        if adapters is not None:
            for adapter in adapters:
                self.register(adapter)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        adapter: ContractAdapter,
    ) -> ContractAdapter:
        """
        Register an adapter.

        Adapter types must be unique. Silent replacement is forbidden because
        it could cause different contract-acquisition behavior between runs.
        """

        if not isinstance(
            adapter,
            ContractAdapter,
        ):
            raise TypeError(
                "adapter must be an instance of ContractAdapter."
            )

        adapter_type = str(
            adapter.adapter_type or ""
        ).strip()

        if not adapter_type:
            raise ValueError(
                "Adapter type cannot be empty."
            )

        if adapter_type in self._adapters:
            raise ValueError(
                f"Adapter '{adapter_type}' is already registered."
            )

        self._adapters[adapter_type] = adapter

        return adapter

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(
        self,
        adapter_type: str,
    ) -> ContractAdapter | None:
        """
        Return a registered adapter by stable adapter type.
        """

        key = str(
            adapter_type or ""
        ).strip()

        if not key:
            return None

        return self._adapters.get(key)

    def has(
        self,
        adapter_type: str,
    ) -> bool:
        """
        Return True when an adapter is registered.
        """

        return self.get(
            adapter_type
        ) is not None

    # ------------------------------------------------------------------
    # Collection
    # ------------------------------------------------------------------

    def all(
        self,
    ) -> tuple[ContractAdapter, ...]:
        """
        Return all registered adapters.

        Registration order is preserved.
        """

        return tuple(
            self._adapters.values()
        )

    def types(
        self,
    ) -> tuple[str, ...]:
        """
        Return registered adapter types.
        """

        return tuple(
            self._adapters.keys()
        )

    def __len__(
        self,
    ) -> int:
        return len(
            self._adapters
        )

    def __iter__(
        self,
    ):
        return iter(
            self._adapters.values()
        )

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def detect(
        self,
        repository: Mapping[str, Any],
    ) -> AdapterResolution:
        """
        Determine which adapter supports the repository.

        Detection is deterministic.

        Each registered adapter is given the normalized repository evidence.
        The first adapter returning `detected=True` is selected.

        Adapter exceptions are handled as detection failures for that adapter
        so one faulty adapter does not prevent unrelated adapters from being
        checked.

        The exception information is returned in the final reason instead of
        being silently swallowed.
        """

        if not isinstance(
            repository,
            Mapping,
        ):
            raise TypeError(
                "repository must be a mapping."
            )

        if not self._adapters:
            return AdapterResolution(
                supported=False,
                reason=(
                    "No contract adapters are registered."
                ),
            )

        detection_errors: list[str] = []

        for adapter in self._adapters.values():
            try:
                detection = adapter.detect(
                    repository
                )
            except Exception as exc:
                detection_errors.append(
                    f"{adapter.adapter_type}: "
                    f"{type(exc).__name__}: {exc}"
                )
                continue

            if not isinstance(
                detection,
                AdapterDetectionResult,
            ):
                detection_errors.append(
                    f"{adapter.adapter_type}: "
                    "adapter returned an invalid detection result."
                )
                continue

            if detection.detected:
                return AdapterResolution(
                    supported=True,
                    adapter=adapter,
                    detection=detection,
                    reason=(
                        detection.reason
                        or (
                            f"Adapter '{adapter.adapter_type}' "
                            "detected the repository."
                        )
                    ),
                )

        if detection_errors:
            return AdapterResolution(
                supported=False,
                reason=(
                    "No registered adapter detected the repository. "
                    "Adapter detection errors: "
                    + "; ".join(detection_errors)
                ),
            )

        return AdapterResolution(
            supported=False,
            reason=(
                "No registered adapter detected a supported "
                "framework or contract source."
            ),
        )

    # ------------------------------------------------------------------
    # Default registry
    # ------------------------------------------------------------------

    @classmethod
    def with_defaults(
        cls,
    ) -> "AdapterRegistry":
        """
        Create the standard API Analyzer adapter registry.

        The registry remains framework-agnostic.

        Django REST Framework is currently the first concrete implementation.
        Additional adapters will be registered here as they are implemented.

        IMPORTANT:
            CodeForge is NOT referenced here.

        CodeForge is only an end-to-end integration/test repository.
        """

        registry = cls()

        # Keep imports local to avoid adapter-package circular imports.
        from .drf import DjangoRESTFrameworkAdapter

        registry.register(
            DjangoRESTFrameworkAdapter()
        )

        return registry

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def metadata(
        self,
    ) -> list[dict[str, Any]]:
        """
        Return safe metadata for registered adapters.

        No repository contents, tokens or credentials are included.
        """

        return [
            adapter.metadata()
            for adapter in self._adapters.values()
        ]


__all__ = [
    "AdapterRegistry",
    "AdapterResolution",
]