"""
Base abstractions for API contract sources.

Contract sources define how the analyzer obtains an API contract for an
exact repository revision. The compatibility engine does not depend on
any framework-specific implementation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class ContractSourceResult:
    """
    Result returned by a contract source.

    A contract source must never silently return an unusable contract.
    Failures are represented explicitly so the caller can fail safely.
    """

    success: bool
    source_type: str
    commit_sha: str

    contract: Mapping[str, Any] | None = None
    raw_text: str | None = None
    source_path: str | None = None

    warnings: tuple[str, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)

    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def success_result(
        cls,
        *,
        source_type: str,
        commit_sha: str,
        contract: Mapping[str, Any],
        raw_text: str | None = None,
        source_path: str | None = None,
        warnings: tuple[str, ...] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> "ContractSourceResult":
        """
        Construct a successful contract-source result.
        """

        return cls(
            success=True,
            source_type=source_type,
            commit_sha=commit_sha,
            contract=contract,
            raw_text=raw_text,
            source_path=source_path,
            warnings=tuple(warnings),
            errors=(),
            metadata=dict(metadata or {}),
        )

    @classmethod
    def error_result(
        cls,
        *,
        source_type: str,
        commit_sha: str,
        errors: tuple[str, ...],
        warnings: tuple[str, ...] = (),
        source_path: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "ContractSourceResult":
        """
        Construct a failed contract-source result.
        """

        return cls(
            success=False,
            source_type=source_type,
            commit_sha=commit_sha,
            contract=None,
            raw_text=None,
            source_path=source_path,
            warnings=tuple(warnings),
            errors=tuple(errors),
            metadata=dict(metadata or {}),
        )


class ContractSource(ABC):
    """
    Framework-agnostic interface for obtaining an API contract.

    Implementations may read an already committed OpenAPI document,
    generate a contract from application code, or use another supported
    acquisition mechanism.
    """

    @property
    @abstractmethod
    def source_type(self) -> str:
        """
        Stable identifier for this contract-source implementation.
        """

        raise NotImplementedError

    @property
    @abstractmethod
    def display_name(self) -> str:
        """
        Human-readable name for UI, logs, and diagnostics.
        """

        raise NotImplementedError

    @abstractmethod
    def can_resolve(
        self,
        repository: Mapping[str, Any],
        *,
        spec_path: str | None = None,
    ) -> bool:
        """
        Return whether this source can obtain a contract from the
        supplied repository information.
        """

        raise NotImplementedError

    @abstractmethod
    def resolve(
        self,
        repository: Mapping[str, Any],
        *,
        commit_sha: str,
        spec_path: str | None = None,
    ) -> ContractSourceResult:
        """
        Resolve the API contract for the exact requested commit.

        Implementations must return an explicit error result when the
        contract cannot be obtained or validated.
        """

        raise NotImplementedError

    def describe(
        self,
        repository: Mapping[str, Any],
        *,
        spec_path: str | None = None,
    ) -> str:
        """
        Return a short human-readable description of how this source
        obtains the contract.
        """

        del repository
        del spec_path

        return self.display_name

    def metadata(self) -> Mapping[str, Any]:
        """
        Return static metadata describing this contract source.
        """

        return {
            "source_type": self.source_type,
            "display_name": self.display_name,
        }