from __future__ import annotations

"""
Base contract-adapter interface for API Analyzer.

The API Analyzer core must remain framework-agnostic.

A framework adapter is responsible for answering framework-specific questions
such as:

    - Is this repository compatible with this adapter?
    - Can this adapter acquire/generate an OpenAPI contract?
    - What command/configuration is required to generate the contract?
    - How should the generated/committed contract be validated?
    - What source description should be shown to the user?

The adapter must NOT decide whether an API change is breaking.

Compatibility classification remains the responsibility of the shared
deterministic comparison/rules engine.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping


# ============================================================================
# Adapter result objects
# ============================================================================


@dataclass(frozen=True)
class AdapterDetectionResult:
    """
    Result of framework detection for one adapter.

    `detected=False` means this adapter does not claim the repository.

    `confidence` is intentionally a descriptive value rather than a numeric
    score because the scanner may use different evidence sources.
    """

    detected: bool

    adapter_type: str = ""

    framework_name: str = ""

    language: str = ""

    confidence: str = "none"

    evidence: tuple[str, ...] = field(default_factory=tuple)

    reason: str = ""


@dataclass(frozen=True)
class ContractGenerationPlan:
    """
    Describes how a framework-specific OpenAPI contract should be generated.

    The adapter does not execute customer code inside the API Analyzer backend.

    Instead, it returns the deterministic information required by the CI
    workflow to generate the contract for an exact repository revision.
    """

    supported: bool

    adapter_type: str

    command: str = ""

    output_path: str = "openapi.json"

    working_directory: str = ""

    environment: Mapping[str, str] = field(default_factory=dict)

    warnings: tuple[str, ...] = field(default_factory=tuple)

    reason: str = ""


@dataclass(frozen=True)
class ContractValidationResult:
    """
    Result of validating an OpenAPI/Swagger contract.

    Validation is deliberately separate from compatibility classification.

    A contract that cannot be validated must never be treated as a successful
    compatibility analysis.
    """

    valid: bool

    contract_type: str = ""

    openapi_version: str = ""

    errors: tuple[str, ...] = field(default_factory=tuple)

    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ContractSourceDescription:
    """
    Human-readable description of where the API contract comes from.
    """

    source_type: str

    name: str

    description: str

    path: str = ""

    generated: bool = False


# ============================================================================
# Base adapter
# ============================================================================


class ContractAdapter(ABC):
    """
    Framework-specific contract acquisition adapter.

    Every supported framework should implement this interface.

    Example future adapters:

        Django REST Framework
        FastAPI
        Flask
        Express
        NestJS
        Spring Boot
        ASP.NET Core
        Laravel
        Go
        ...

    Important architectural rule:

        Adapter -> obtains/describes the contract
        Analyzer core -> compares contracts and applies rules

    The adapter MUST NOT classify compatibility changes.
    """

    # ------------------------------------------------------------------
    # Stable adapter identity
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def adapter_type(self) -> str:
        """
        Stable machine-readable adapter identifier.

        Example:

            "django-rest-framework"
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def framework_name(self) -> str:
        """
        Human-readable framework name.

        Example:

            "Django REST Framework"
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def language(self) -> str:
        """
        Primary programming language used by the framework.

        Example:

            "Python"
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Repository detection
    # ------------------------------------------------------------------

    @abstractmethod
    def detect(
        self,
        repository: Mapping[str, Any],
    ) -> AdapterDetectionResult:
        """
        Determine whether this adapter applies to a repository.

        Parameters
        ----------
        repository:
            Normalized repository information produced by the repository
            scanner.

        The method must be:

            - deterministic
            - read-only
            - safe on incomplete repository metadata
            - free of LLM/network side effects

        Returns
        -------
        AdapterDetectionResult
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Contract generation capability
    # ------------------------------------------------------------------

    @abstractmethod
    def can_generate_contract(
        self,
        repository: Mapping[str, Any],
    ) -> bool:
        """
        Return whether this adapter can automatically generate a contract.

        This does NOT execute the generation.

        The answer should depend only on deterministic repository evidence and
        adapter capabilities.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Contract generation plan
    # ------------------------------------------------------------------

    @abstractmethod
    def generate_contract(
        self,
        repository: Mapping[str, Any],
        *,
        commit_sha: str,
    ) -> ContractGenerationPlan:
        """
        Produce a deterministic generation plan for an exact commit.

        The commit SHA is required because branches can move.

        The resulting plan is consumed by the CI/setup layer.

        The adapter should not silently generate a contract from another
        revision.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Contract validation
    # ------------------------------------------------------------------

    @abstractmethod
    def validate_contract(
        self,
        contract: Mapping[str, Any],
    ) -> ContractValidationResult:
        """
        Validate a contract before compatibility analysis.

        Validation should check at least the adapter-relevant contract
        expectations and the basic OpenAPI/Swagger structure.

        A validation failure must remain distinguishable from:

            - a compatibility failure
            - a GitHub failure
            - an analyzer execution failure
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Source description
    # ------------------------------------------------------------------

    @abstractmethod
    def describe_source(
        self,
        repository: Mapping[str, Any],
    ) -> ContractSourceDescription:
        """
        Describe where the adapter obtains the API contract.

        This is used by setup planning and the frontend.

        Example:

            "Generated by Django REST Framework schema tooling during CI."
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Capability metadata
    # ------------------------------------------------------------------

    def capabilities(self) -> dict[str, bool]:
        """
        Return adapter capabilities.

        Keep this small and deterministic.

        Additional capabilities can be added later without changing the core
        comparison engine.
        """
        return {
            "detect": True,
            "generate_contract": self.can_generate_contract({}),
            "validate_contract": True,
        }

    # ------------------------------------------------------------------
    # Debug/support metadata
    # ------------------------------------------------------------------

    def metadata(self) -> dict[str, Any]:
        """
        Return stable adapter metadata for logs, setup plans and audit data.

        Do not include secrets, tokens, repository contents, or credentials.
        """
        return {
            "adapter_type": self.adapter_type,
            "framework_name": self.framework_name,
            "language": self.language,
        }


__all__ = [
    "AdapterDetectionResult",
    "ContractAdapter",
    "ContractGenerationPlan",
    "ContractSourceDescription",
    "ContractValidationResult",
]