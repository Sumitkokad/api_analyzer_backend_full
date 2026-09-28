"""
Django REST Framework contract source.

This source uses the DRF adapter to determine how a contract should be
generated, then consumes the generated contract supplied for the exact
repository revision.

The actual command execution belongs to the CI/repository execution layer.
This keeps the analyzer backend deterministic and framework-aware only
through the adapter contract.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from api_app.adapters.drf import DjangoRESTFrameworkAdapter

from .base import ContractSource, ContractSourceResult


class DjangoRESTFrameworkContractSource(ContractSource):
    """
    Contract source for Django REST Framework applications.
    """

    @property
    def source_type(self) -> str:
        return "django-rest-framework"

    @property
    def display_name(self) -> str:
        return "Django REST Framework generated contract"

    def can_resolve(
        self,
        repository: Mapping[str, Any],
        *,
        spec_path: str | None = None,
    ) -> bool:
        """
        Return True when the repository is detected as DRF and a generated
        contract is available or can be located at the requested path.
        """

        adapter = DjangoRESTFrameworkAdapter()

        detection = adapter.detect(repository)

        if not detection.detected:
            return False

        if repository.get("generated_contract") is not None:
            return True

        generated_path = self._generated_path(
            repository,
            spec_path=spec_path,
        )

        file_contents = repository.get("file_contents", {})

        if not isinstance(file_contents, Mapping):
            return False

        return generated_path in file_contents

    def resolve(
        self,
        repository: Mapping[str, Any],
        *,
        commit_sha: str,
        spec_path: str | None = None,
    ) -> ContractSourceResult:
        """
        Resolve a generated DRF contract for the exact commit.

        The execution layer is expected to run the adapter's generation
        command and provide the generated contract through repository
        metadata.
        """

        normalized_commit_sha = str(commit_sha).strip()

        if not normalized_commit_sha:
            return ContractSourceResult.error_result(
                source_type=self.source_type,
                commit_sha=normalized_commit_sha,
                errors=(
                    "commit_sha is required.",
                ),
                source_path=spec_path,
            )

        adapter = DjangoRESTFrameworkAdapter()

        detection = adapter.detect(repository)

        if not detection.detected:
            return ContractSourceResult.error_result(
                source_type=self.source_type,
                commit_sha=normalized_commit_sha,
                errors=(
                    "Repository was not detected as Django REST Framework.",
                ),
                source_path=spec_path,
            )

        try:
            plan = adapter.generate_contract(
                repository,
                commit_sha=normalized_commit_sha,
            )
        except Exception as exc:
            return ContractSourceResult.error_result(
                source_type=self.source_type,
                commit_sha=normalized_commit_sha,
                errors=(
                    f"Unable to create the DRF contract generation plan: {exc}",
                ),
                source_path=spec_path,
            )

        resolved_path = self._generated_path(
            repository,
            spec_path=spec_path,
            default_path=plan.output_path,
        )

        generated_contract = repository.get("generated_contract")

        if generated_contract is not None:
            return self._build_result_from_contract(
                generated_contract=generated_contract,
                repository=repository,
                commit_sha=normalized_commit_sha,
                source_path=resolved_path,
                plan_command=plan.command,
            )

        file_contents = repository.get("file_contents", {})

        if not isinstance(file_contents, Mapping):
            file_contents = {}

        raw_content = file_contents.get(resolved_path)

        if raw_content is None:
            return ContractSourceResult.error_result(
                source_type=self.source_type,
                commit_sha=normalized_commit_sha,
                errors=(
                    (
                        f"Generated DRF contract '{resolved_path}' "
                        "was not provided."
                    ),
                ),
                source_path=resolved_path,
                metadata={
                    "generation_command": plan.command,
                    "adapter_type": adapter.adapter_type,
                },
            )

        if not isinstance(raw_content, str):
            return ContractSourceResult.error_result(
                source_type=self.source_type,
                commit_sha=normalized_commit_sha,
                errors=(
                    (
                        f"Generated DRF contract '{resolved_path}' "
                        "must be text content."
                    ),
                ),
                source_path=resolved_path,
                metadata={
                    "generation_command": plan.command,
                    "adapter_type": adapter.adapter_type,
                },
            )

        parsed = self._parse_json(
            raw_content,
            resolved_path,
        )

        if parsed is None:
            return ContractSourceResult.error_result(
                source_type=self.source_type,
                commit_sha=normalized_commit_sha,
                errors=(
                    (
                        f"Generated DRF contract '{resolved_path}' "
                        "is not valid JSON."
                    ),
                ),
                source_path=resolved_path,
                metadata={
                    "generation_command": plan.command,
                    "adapter_type": adapter.adapter_type,
                },
            )

        return self._build_result_from_contract(
            generated_contract=parsed,
            repository=repository,
            commit_sha=normalized_commit_sha,
            source_path=resolved_path,
            raw_text=raw_content,
            plan_command=plan.command,
        )

    def describe(
        self,
        repository: Mapping[str, Any],
        *,
        spec_path: str | None = None,
    ) -> str:
        adapter = DjangoRESTFrameworkAdapter()

        try:
            plan = adapter.generate_contract(
                repository,
                commit_sha="0" * 40,
            )
            path = spec_path or plan.output_path
        except Exception:
            path = spec_path or "openapi.json"

        return (
            "Generate the Django REST Framework contract using the "
            f"adapter command and read '{path}' from the exact revision."
        )

    def metadata(self) -> Mapping[str, Any]:
        return {
            "source_type": self.source_type,
            "display_name": self.display_name,
            "adapter_type": "django-rest-framework",
            "generated_by": "adapter",
            "execution_outside_analyzer": True,
            "requires_exact_commit": True,
        }

    @staticmethod
    def _generated_path(
        repository: Mapping[str, Any],
        *,
        spec_path: str | None,
        default_path: str = "openapi.json",
    ) -> str:
        if spec_path:
            return str(spec_path).strip().replace("\\", "/").lstrip("./")

        configured_path = repository.get("spec_path")

        if configured_path:
            return (
                str(configured_path)
                .strip()
                .replace("\\", "/")
                .lstrip("./")
            )

        return default_path

    @staticmethod
    def _parse_json(
        raw_content: str,
        path: str,
    ) -> Mapping[str, Any] | None:
        del path

        try:
            parsed = json.loads(raw_content)
        except json.JSONDecodeError:
            return None

        if not isinstance(parsed, Mapping):
            return None

        return parsed

    def _build_result_from_contract(
        self,
        *,
        generated_contract: Any,
        repository: Mapping[str, Any],
        commit_sha: str,
        source_path: str,
        raw_text: str | None = None,
        plan_command: str,
    ) -> ContractSourceResult:
        if not isinstance(generated_contract, Mapping):
            return ContractSourceResult.error_result(
                source_type=self.source_type,
                commit_sha=commit_sha,
                errors=(
                    "Generated DRF contract must be a JSON object.",
                ),
                source_path=source_path,
                metadata={
                    "generation_command": plan_command,
                },
            )

        adapter = DjangoRESTFrameworkAdapter()

        validation = adapter.validate_contract(
            generated_contract
        )

        if not validation.valid:
            return ContractSourceResult.error_result(
                source_type=self.source_type,
                commit_sha=commit_sha,
                errors=tuple(validation.errors),
                warnings=tuple(validation.warnings),
                source_path=source_path,
                metadata={
                    "generation_command": plan_command,
                    "adapter_type": adapter.adapter_type,
                },
            )

        generated_commit = repository.get(
            "generated_contract_commit_sha"
        )

        warnings = list(validation.warnings)

        if generated_commit:
            if str(generated_commit).strip() != commit_sha:
                return ContractSourceResult.error_result(
                    source_type=self.source_type,
                    commit_sha=commit_sha,
                    errors=(
                        "Generated contract commit SHA does not match "
                        "the requested repository revision.",
                    ),
                    warnings=tuple(warnings),
                    source_path=source_path,
                    metadata={
                        "generation_command": plan_command,
                        "generated_contract_commit_sha": str(
                            generated_commit
                        ).strip(),
                    },
                )
        else:
            warnings.append(
                "Generated contract did not include explicit commit "
                "metadata; caller must ensure the artifact came from "
                "the requested revision."
            )

        return ContractSourceResult.success_result(
            source_type=self.source_type,
            commit_sha=commit_sha,
            contract=dict(generated_contract),
            raw_text=raw_text,
            source_path=source_path,
            warnings=tuple(warnings),
            metadata={
                "generation_command": plan_command,
                "adapter_type": adapter.adapter_type,
                "revision_pinned": True,
                "generation_mode": "adapter",
            },
        )