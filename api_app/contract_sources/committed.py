"""
Committed API contract source.

This source reads an already committed OpenAPI/Swagger document from the
repository at the exact requested revision. It is framework-agnostic and
can therefore be used by Django, FastAPI, Flask, Node, Java, .NET, Go,
or any other technology that commits its contract to the repository.
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any, Mapping

from .base import ContractSource, ContractSourceResult


class CommittedContractSource(ContractSource):
    """
    Contract source for repositories containing a committed API document.
    """

    DEFAULT_PATHS = (
        "openapi.json",
        "openapi.yaml",
        "openapi.yml",
        "swagger.json",
        "swagger.yaml",
        "swagger.yml",
        "docs/openapi.json",
        "docs/openapi.yaml",
        "docs/openapi.yml",
    )

    SUPPORTED_EXTENSIONS = (
        ".json",
        ".yaml",
        ".yml",
    )

    @property
    def source_type(self) -> str:
        return "committed"

    @property
    def display_name(self) -> str:
        return "Committed OpenAPI document"

    def can_resolve(
        self,
        repository: Mapping[str, Any],
        *,
        spec_path: str | None = None,
    ) -> bool:
        """
        Determine whether a committed contract exists in the repository
        metadata.

        The caller may provide an explicit spec_path. Otherwise, known
        conventional OpenAPI/Swagger paths are checked.
        """

        file_contents = self._file_contents(repository)
        tree_paths = self._tree_paths(repository)

        if spec_path:
            normalized_path = self._normalize_path(spec_path)

            return (
                normalized_path in file_contents
                or normalized_path in tree_paths
            )

        for candidate in self.DEFAULT_PATHS:
            if candidate in file_contents or candidate in tree_paths:
                return True

        return False

    def resolve(
        self,
        repository: Mapping[str, Any],
        *,
        commit_sha: str,
        spec_path: str | None = None,
    ) -> ContractSourceResult:
        """
        Read and parse the committed contract for the exact commit SHA.
        """

        errors: list[str] = []
        warnings: list[str] = []

        normalized_commit_sha = str(commit_sha).strip()

        if not normalized_commit_sha:
            errors.append("commit_sha is required.")

        if errors:
            return ContractSourceResult.error_result(
                source_type=self.source_type,
                commit_sha=normalized_commit_sha,
                errors=tuple(errors),
                warnings=tuple(warnings),
                source_path=spec_path,
            )

        resolved_path = self._resolve_path(
            repository,
            spec_path=spec_path,
        )

        if resolved_path is None:
            return ContractSourceResult.error_result(
                source_type=self.source_type,
                commit_sha=normalized_commit_sha,
                errors=(
                    "Committed API contract was not found in the "
                    "repository revision.",
                ),
                warnings=tuple(warnings),
                source_path=spec_path,
            )

        raw_content = self._file_contents(repository).get(resolved_path)

        if raw_content is None:
            return ContractSourceResult.error_result(
                source_type=self.source_type,
                commit_sha=normalized_commit_sha,
                errors=(
                    f"Contract file '{resolved_path}' is listed in the "
                    "repository tree but its content was not provided.",
                ),
                warnings=tuple(warnings),
                source_path=resolved_path,
            )

        if not isinstance(raw_content, str):
            return ContractSourceResult.error_result(
                source_type=self.source_type,
                commit_sha=normalized_commit_sha,
                errors=(
                    f"Contract file '{resolved_path}' must contain text "
                    "content.",
                ),
                warnings=tuple(warnings),
                source_path=resolved_path,
            )

        raw_content = raw_content.strip()

        if not raw_content:
            return ContractSourceResult.error_result(
                source_type=self.source_type,
                commit_sha=normalized_commit_sha,
                errors=(
                    f"Contract file '{resolved_path}' is empty.",
                ),
                source_path=resolved_path,
            )

        try:
            contract = self._parse_contract(
                raw_content,
                resolved_path,
            )
        except ValueError as exc:
            return ContractSourceResult.error_result(
                source_type=self.source_type,
                commit_sha=normalized_commit_sha,
                errors=(str(exc),),
                source_path=resolved_path,
            )

        if not isinstance(contract, Mapping):
            return ContractSourceResult.error_result(
                source_type=self.source_type,
                commit_sha=normalized_commit_sha,
                errors=(
                    f"Contract file '{resolved_path}' must contain an "
                    "object at the document root.",
                ),
                source_path=resolved_path,
            )

        validation_errors = self._validate_contract_structure(contract)

        if validation_errors:
            return ContractSourceResult.error_result(
                source_type=self.source_type,
                commit_sha=normalized_commit_sha,
                errors=tuple(validation_errors),
                source_path=resolved_path,
            )

        return ContractSourceResult.success_result(
            source_type=self.source_type,
            commit_sha=normalized_commit_sha,
            contract=dict(contract),
            raw_text=raw_content,
            source_path=resolved_path,
            warnings=tuple(warnings),
            metadata={
                "format": self._detect_format(resolved_path),
                "revision_pinned": True,
            },
        )

    def describe(
        self,
        repository: Mapping[str, Any],
        *,
        spec_path: str | None = None,
    ) -> str:
        resolved_path = self._resolve_path(
            repository,
            spec_path=spec_path,
        )

        if resolved_path:
            return (
                f"Read the committed API contract from "
                f"'{resolved_path}'."
            )

        if spec_path:
            return (
                f"Read the committed API contract from "
                f"'{self._normalize_path(spec_path)}'."
            )

        return (
            "Read a committed OpenAPI or Swagger document from "
            "the repository."
        )

    def metadata(self) -> Mapping[str, Any]:
        return {
            "source_type": self.source_type,
            "display_name": self.display_name,
            "supported_extensions": self.SUPPORTED_EXTENSIONS,
            "default_paths": self.DEFAULT_PATHS,
            "requires_exact_commit": True,
        }

    @staticmethod
    def _normalize_path(path: str) -> str:
        """
        Normalize Windows/Unix separators without allowing the path to
        escape the repository root.
        """

        normalized = str(path).strip().replace("\\", "/")

        while normalized.startswith("./"):
            normalized = normalized[2:]

        normalized = str(
            PurePosixPath(normalized)
        ).lstrip("/")

        if normalized == "." or ".." in PurePosixPath(normalized).parts:
            raise ValueError(
                f"Invalid repository-relative contract path: {path}"
            )

        return normalized

    def _resolve_path(
        self,
        repository: Mapping[str, Any],
        *,
        spec_path: str | None,
    ) -> str | None:
        file_contents = self._file_contents(repository)
        tree_paths = self._tree_paths(repository)

        if spec_path:
            normalized_path = self._normalize_path(spec_path)

            if (
                normalized_path in file_contents
                or normalized_path in tree_paths
            ):
                return normalized_path

            return None

        for candidate in self.DEFAULT_PATHS:
            if candidate in file_contents or candidate in tree_paths:
                return candidate

        return None

    @staticmethod
    def _tree_paths(
        repository: Mapping[str, Any],
    ) -> set[str]:
        paths = repository.get("tree_paths", ())

        if not isinstance(paths, (list, tuple, set)):
            return set()

        result: set[str] = set()

        for path in paths:
            try:
                result.add(
                    CommittedContractSource._normalize_path(str(path))
                )
            except ValueError:
                continue

        return result

    @staticmethod
    def _file_contents(
        repository: Mapping[str, Any],
    ) -> dict[str, str]:
        contents = repository.get("file_contents", {})

        if not isinstance(contents, Mapping):
            return {}

        result: dict[str, str] = {}

        for path, content in contents.items():
            try:
                normalized_path = (
                    CommittedContractSource._normalize_path(str(path))
                )
            except ValueError:
                continue

            if isinstance(content, str):
                result[normalized_path] = content

        return result

    @staticmethod
    def _detect_format(path: str) -> str:
        suffix = PurePosixPath(path).suffix.lower()

        if suffix == ".json":
            return "json"

        if suffix in {".yaml", ".yml"}:
            return "yaml"

        return "unknown"

    @staticmethod
    def _parse_contract(
        raw_content: str,
        path: str,
    ) -> Mapping[str, Any]:
        document_format = CommittedContractSource._detect_format(path)

        if document_format == "json":
            try:
                parsed = json.loads(raw_content)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON in committed contract '{path}': "
                    f"line {exc.lineno}, column {exc.colno}."
                ) from exc

            if not isinstance(parsed, Mapping):
                raise ValueError(
                    f"Committed contract '{path}' must contain a JSON "
                    "object at the document root."
                )

            return parsed

        if document_format == "yaml":
            try:
                import yaml
            except ImportError as exc:
                raise ValueError(
                    "PyYAML is required to parse YAML OpenAPI documents. "
                    "Install it with: pip install pyyaml"
                ) from exc

            try:
                parsed = yaml.safe_load(raw_content)
            except yaml.YAMLError as exc:
                raise ValueError(
                    f"Invalid YAML in committed contract '{path}': {exc}"
                ) from exc

            if not isinstance(parsed, Mapping):
                raise ValueError(
                    f"Committed contract '{path}' must contain a YAML "
                    "object at the document root."
                )

            return parsed

        raise ValueError(
            f"Unsupported committed contract format for '{path}'."
        )

    @staticmethod
    def _validate_contract_structure(
        contract: Mapping[str, Any],
    ) -> list[str]:
        errors: list[str] = []

        has_openapi = bool(contract.get("openapi"))
        has_swagger = bool(contract.get("swagger"))

        if not has_openapi and not has_swagger:
            errors.append(
                "Committed contract must contain either 'openapi' "
                "or 'swagger'."
            )

        info = contract.get("info")

        if not isinstance(info, Mapping):
            errors.append(
                "Committed contract must contain an 'info' object."
            )

        paths = contract.get("paths")

        if paths is not None and not isinstance(paths, Mapping):
            errors.append(
                "Committed contract 'paths' must be an object."
            )

        return errors