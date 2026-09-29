"""
Automatic repository setup planning.

This module converts repository scan results into a reviewable setup plan.
It does not call GitHub and does not modify repositories.

The GitHub write layer can consume the resulting plan to create one setup
branch and one setup pull request. Framework-specific contract generation
remains inside the resolved adapter. This service only validates and wires
that adapter plan into repository configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import re
from textwrap import indent
from typing import Any, Mapping
from urllib.parse import urlparse


@dataclass(frozen=True)
class SetupFile:
    """A file that should be created or updated by the setup PR."""

    path: str
    content: str


@dataclass(frozen=True)
class SetupPlan:
    """Complete reviewable setup plan for a repository."""

    repository: str
    base_branch: str

    branch_name: str
    pull_request_title: str
    pull_request_body: str

    files: tuple[SetupFile, ...]

    adapter_type: str
    framework_name: str
    spec_path: str
    generation_command: str

    warnings: tuple[str, ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    analyzer_action_ref: str = ""
    analyzer_base_url: str = ""
    project_id: str = ""
    token_secret_name: str = "API_ANALYZER_TOKEN"


class RepositorySetupService:
    """
    Build automatic API Analyzer onboarding plans.

    Framework detection and generation are delegated to the adapter layer.
    The setup service does not contain per-framework command branches.
    """

    DEFAULT_WORKFLOW_PATH = ".github/workflows/api-compatibility.yml"
    DEFAULT_CONFIG_PATH = ".api-analyzer.yml"
    SETUP_BRANCH_PREFIX = "api-analyzer/setup"

    DEFAULT_ANALYZER_ACTION_REF = os.getenv(
        "API_ANALYZER_ACTION_REF",
        "Sumitkokad/api_analyzer_full/.github/actions/api-compatibility@main",
    )
    DEFAULT_ANALYZER_BASE_URL = os.getenv(
        "API_ANALYZER_BASE_URL",
        "https://api-analyzer-backend.onrender.com",
    )
    DEFAULT_TOKEN_SECRET_NAME = "API_ANALYZER_TOKEN"
    DEFAULT_PROJECT_VARIABLE_NAME = "API_ANALYZER_PROJECT_ID"

    _SECRET_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    _ACTION_REF_PATTERN = re.compile(
        r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[^@\s]+)?@[A-Za-z0-9_.-]+$"
    )

    def build_plan(
        self,
        *,
        repository: Mapping[str, Any],
        adapter: Any,
        base_branch: str | None = None,
        spec_path: str | None = None,
        project_id: str | int | None = None,
        analyzer_base_url: str | None = None,
        analyzer_action_ref: str | None = None,
        token_secret_name: str | None = None,
    ) -> SetupPlan:
        """Build a setup PR plan for the detected repository."""

        repository_name = self._repository_name(repository)
        branch = self._normalize_branch(
            base_branch or repository.get("default_branch") or "main"
        )

        adapter_type = self._adapter_value(adapter, "adapter_type")
        framework_name = self._adapter_value(
            adapter,
            "framework_name",
            default=adapter_type,
        )

        repository_spec_path = self._repository_spec_path(repository)
        resolved_spec_path = (
            self._normalize_path(spec_path)
            if spec_path
            else repository_spec_path
        ) or "openapi.json"

        commit_sha = str(repository.get("commit_sha") or "").strip()
        generation_plan = self._contract_generation_plan(
            adapter=adapter,
            repository=repository,
            commit_sha=commit_sha,
            spec_path=resolved_spec_path,
            repository_spec_path=repository_spec_path,
        )
        generation_command = generation_plan["command"]

        # For generated contracts, the adapter is authoritative about the
        # repository-relative output path. The workflow must pass the same
        # path to the centralized action; otherwise a nested Django project
        # such as ``backend/manage.py`` would generate ``backend/openapi.json``
        # while the action still searched for ``openapi.json``.
        if generation_plan.get("source") == "generated":
            generated_output_path = self._normalize_path(
                str(generation_plan.get("output_path") or "").strip()
            )
            if not generated_output_path:
                raise ValueError(
                    "Adapter contract generation plan must provide a "
                    "repository-relative output_path."
                )
            resolved_spec_path = generated_output_path
            generation_plan["path"] = generated_output_path

        resolved_project_id = self._resolve_project_id(
            repository=repository,
            project_id=project_id,
        )
        resolved_analyzer_base_url = self._resolve_analyzer_base_url(
            repository=repository,
            analyzer_base_url=analyzer_base_url,
        )
        resolved_action_ref = self._resolve_action_ref(
            repository=repository,
            analyzer_action_ref=analyzer_action_ref,
        )
        resolved_token_secret = self._resolve_token_secret_name(
            repository=repository,
            token_secret_name=token_secret_name,
        )

        self._validate_analyzer_action_ref(resolved_action_ref)

        workflow_content = self._build_workflow(
            adapter_type=adapter_type,
            framework_name=framework_name,
            spec_path=resolved_spec_path,
            generation_command=generation_command,
            analyzer_action_ref=resolved_action_ref,
            analyzer_base_url=resolved_analyzer_base_url,
            project_id=resolved_project_id,
            token_secret_name=resolved_token_secret,
            contract_source=str(generation_plan.get("source") or "generated"),
            generation_plan=generation_plan,
        )

        config_content = self._build_config(
            adapter_type=adapter_type,
            framework_name=framework_name,
            spec_path=resolved_spec_path,
            analyzer_base_url=resolved_analyzer_base_url,
            project_id=resolved_project_id,
            analyzer_action_ref=resolved_action_ref,
            token_secret_name=resolved_token_secret,
            generation_plan=generation_plan,
        )

        files = (
            SetupFile(path=self.DEFAULT_CONFIG_PATH, content=config_content),
            SetupFile(path=self.DEFAULT_WORKFLOW_PATH, content=workflow_content),
        )

        branch_name = self._setup_branch_name(adapter_type=adapter_type)
        pull_request_title = "chore: configure API compatibility analysis"

        pull_request_body = self._build_pull_request_body(
            repository_name=repository_name,
            framework_name=framework_name,
            adapter_type=adapter_type,
            spec_path=resolved_spec_path,
            generation_command=generation_command,
            analyzer_action_ref=resolved_action_ref,
            token_secret_name=resolved_token_secret,
            contract_source=str(generation_plan.get("source") or "generated"),
        )

        warnings = self._collect_warnings(
            repository=repository,
            spec_path=resolved_spec_path,
            project_id=resolved_project_id,
            analyzer_base_url=resolved_analyzer_base_url,
            analyzer_action_ref=resolved_action_ref,
        )

        # Adapter warnings are part of the reviewable setup plan so the setup
        # PR exposes any runtime/configuration caveats before merge.
        generation_warnings = generation_plan.get("warnings") or []
        if isinstance(generation_warnings, (list, tuple, set)):
            warnings.extend(
                str(item).strip()
                for item in generation_warnings
                if str(item).strip()
            )
        elif str(generation_warnings).strip():
            warnings.append(str(generation_warnings).strip())

        metadata = {
            "setup_mode": "automatic",
            "review_required": True,
            "framework_agnostic": True,
            "repository": repository_name,
            "base_branch": branch,
            "project_id": resolved_project_id,
            "analyzer_base_url": resolved_analyzer_base_url,
            "analyzer_action_ref": resolved_action_ref,
            "token_secret_name": resolved_token_secret,
            "project_variable_name": self.DEFAULT_PROJECT_VARIABLE_NAME,
            "contract_generation": generation_plan,
            "contract_source": str(generation_plan.get("source") or "generated"),
            "setup_branch_prefix": self.SETUP_BRANCH_PREFIX,
        }

        return SetupPlan(
            repository=repository_name,
            base_branch=branch,
            branch_name=branch_name,
            pull_request_title=pull_request_title,
            pull_request_body=pull_request_body,
            files=files,
            adapter_type=adapter_type,
            framework_name=framework_name,
            spec_path=resolved_spec_path,
            generation_command=generation_command,
            warnings=tuple(warnings),
            metadata=metadata,
            analyzer_action_ref=resolved_action_ref,
            analyzer_base_url=resolved_analyzer_base_url,
            project_id=resolved_project_id or "",
            token_secret_name=resolved_token_secret,
        )

    @staticmethod
    def _repository_name(repository: Mapping[str, Any]) -> str:
        for key in (
            "repository_full_name",
            "full_name",
            "repository",
            "name",
        ):
            value = repository.get(key)
            if value:
                return str(value).strip()

        raise ValueError("Repository name is required to build a setup plan.")

    @staticmethod
    def _adapter_value(
        adapter: Any,
        attribute: str,
        *,
        default: str | None = None,
    ) -> str:
        value = getattr(adapter, attribute, None)
        if value:
            return str(value).strip()
        if default is not None:
            return default
        raise ValueError(
            f"Adapter does not provide required attribute '{attribute}'."
        )

    @staticmethod
    def _normalize_branch(branch: str) -> str:
        normalized = str(branch).strip()
        if not normalized:
            return "main"
        if any(char in normalized for char in "\r\n"):
            raise ValueError("Repository branch cannot contain newlines.")
        return normalized

    @staticmethod
    def _normalize_path(path: str) -> str:
        normalized = str(path).strip().replace("\\", "/")

        while normalized.startswith("./"):
            normalized = normalized[2:]

        normalized = normalized.lstrip("/")

        if not normalized or normalized == ".":
            raise ValueError("Contract path cannot be empty.")

        parts = normalized.split("/")
        if ".." in parts:
            raise ValueError("Contract path cannot escape the repository root.")

        if any(char in normalized for char in "\r\n"):
            raise ValueError("Contract path cannot contain newlines.")

        return normalized

    def _repository_spec_path(
        self,
        repository: Mapping[str, Any],
    ) -> str | None:
        value = repository.get("spec_path")
        if not value:
            return None
        return self._normalize_path(str(value))

    @staticmethod
    def _contract_generation_plan(
        *,
        adapter: Any,
        repository: Mapping[str, Any],
        commit_sha: str,
        spec_path: str,
        repository_spec_path: str | None,
    ) -> dict[str, Any]:
        """Resolve how the compatibility workflow obtains the API contract.

        If repository scanning found a committed OpenAPI/Swagger contract and
        the selected ``spec_path`` points to that same file, use the file
        directly. This avoids incorrectly running a framework generator for
        repositories whose API contract is already committed.

        Otherwise the resolved framework adapter provides the generation plan.
        For generated contracts, the adapter's repository-relative ``output_path``
        is authoritative because it is coupled to the adapter's generation
        command and working directory.
        """

        if repository_spec_path and spec_path == repository_spec_path:
            return {
                "command": "",
                "source": "committed_file",
                "path": repository_spec_path,
                "language": "",
                "package_manager": "",
                "dependency_files": [],
                "working_directory": ".",
                "install_command": "",
                "environment": {},
                "adapter_name": "committed-contract",
                "adapter_version": "1",
            }

        try:
            plan = adapter.generate_contract(
                repository,
                commit_sha=commit_sha or ("0" * 40),
            )
        except Exception as exc:
            raise ValueError(
                "Unable to obtain contract generation plan from adapter: "
                f"{exc}"
            ) from exc

        command = getattr(plan, "command", None)
        if not command:
            raise ValueError(
                "Adapter contract generation plan did not provide a command."
            )

        output_path = getattr(plan, "output_path", None)
        normalized_output_path = ""
        if output_path:
            normalized_output_path = RepositorySetupService._normalize_path(
                str(output_path)
            )

        result: dict[str, Any] = {
            "command": str(command).strip(),
            "source": "generated",
            "path": normalized_output_path or spec_path,
            "output_path": normalized_output_path or spec_path,
            "adapter_name": str(
                getattr(plan, "adapter_name", None)
                or getattr(adapter, "adapter_type", None)
                or "customer-adapter"
            ).strip(),
            "adapter_version": str(
                getattr(plan, "adapter_version", None)
                or getattr(adapter, "version", None)
                or "1"
            ).strip(),
        }

        plan_warnings = getattr(plan, "warnings", None)
        if plan_warnings is not None:
            if isinstance(plan_warnings, (list, tuple, set)):
                result["warnings"] = [
                    str(item).strip()
                    for item in plan_warnings
                    if str(item).strip()
                ]
            elif str(plan_warnings).strip():
                result["warnings"] = [str(plan_warnings).strip()]

        for attribute in (
            "language",
            "package_manager",
            "dependency_files",
            "working_directory",
            "install_command",
            "environment",
        ):
            value = getattr(plan, attribute, None)
            if value is not None:
                if attribute == "dependency_files" and isinstance(
                    value, (list, tuple, set)
                ):
                    value = [str(item) for item in value]
                elif attribute == "environment" and isinstance(value, Mapping):
                    value = {
                        str(key): str(item)
                        for key, item in value.items()
                    }
                else:
                    value = str(value).strip()
                result[attribute] = value

        return result

    def _resolve_project_id(
        self,
        *,
        repository: Mapping[str, Any],
        project_id: str | int | None,
    ) -> str | None:
        value = project_id if project_id is not None else repository.get("project_id")
        if value is None or str(value).strip() == "":
            return None
        return str(value).strip()

    def _resolve_analyzer_base_url(
        self,
        *,
        repository: Mapping[str, Any],
        analyzer_base_url: str | None,
    ) -> str:
        value = (
            analyzer_base_url
            or repository.get("analyzer_base_url")
            or repository.get("api_analyzer_base_url")
            or self.DEFAULT_ANALYZER_BASE_URL
        )
        return self._normalize_base_url(str(value))

    def _resolve_action_ref(
        self,
        *,
        repository: Mapping[str, Any],
        analyzer_action_ref: str | None,
    ) -> str:
        value = (
            analyzer_action_ref
            or repository.get("analyzer_action_ref")
            or self.DEFAULT_ANALYZER_ACTION_REF
        )
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("API Analyzer action reference cannot be empty.")
        if any(char in normalized for char in "\r\n"):
            raise ValueError("API Analyzer action reference cannot contain newlines.")
        return normalized

    def _resolve_token_secret_name(
        self,
        *,
        repository: Mapping[str, Any],
        token_secret_name: str | None,
    ) -> str:
        value = (
            token_secret_name
            or repository.get("token_secret_name")
            or self.DEFAULT_TOKEN_SECRET_NAME
        )
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("API Analyzer token secret name cannot be empty.")
        if not self._SECRET_NAME_PATTERN.fullmatch(normalized):
            raise ValueError(
                "API Analyzer token secret name must contain only letters, "
                "digits, and underscores and must not start with a digit."
            )
        return normalized

    @staticmethod
    def _normalize_base_url(value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized:
            raise ValueError("API Analyzer base URL cannot be empty.")

        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(
                "API Analyzer base URL must be an absolute HTTP(S) URL."
            )
        return normalized

    @classmethod
    def _validate_analyzer_action_ref(cls, action_ref: str) -> None:
        if not cls._ACTION_REF_PATTERN.fullmatch(action_ref):
            raise ValueError(
                "API Analyzer action reference must look like "
                "'owner/repository/path@ref'."
            )

    @staticmethod
    def _yaml_string(value: str) -> str:
        """Return a YAML-safe JSON string literal."""
        return json.dumps(str(value), ensure_ascii=False)

    @classmethod
    def _build_config(
        cls,
        *,
        adapter_type: str,
        framework_name: str,
        spec_path: str,
        analyzer_base_url: str,
        project_id: str | None,
        analyzer_action_ref: str,
        token_secret_name: str,
        generation_plan: Mapping[str, Any],
    ) -> str:
        project_value = (
            str(project_id)
            if project_id is not None
            else "repository variable: API_ANALYZER_PROJECT_ID"
        )

        source_value = str(generation_plan.get("source") or "generated")

        lines = [
            "# API Analyzer configuration",
            "# Generated automatically by API Analyzer.",
            "# Review this file in the setup pull request before merging.",
            "",
            "api_analyzer:",
            f"  adapter: {cls._yaml_string(adapter_type)}",
            f"  framework: {cls._yaml_string(framework_name)}",
            f"  spec_path: {cls._yaml_string(spec_path)}",
            '  baseline_mode: "merge-base"',
            f"  analyzer_base_url: {cls._yaml_string(analyzer_base_url)}",
            f"  analyzer_action: {cls._yaml_string(analyzer_action_ref)}",
            f"  project_id: {cls._yaml_string(project_value)}",
            f"  token_secret: {cls._yaml_string(token_secret_name)}",
            "",
            "  contract_generation:",
            f"    source: {cls._yaml_string(source_value)}",
            f"    command: {cls._yaml_string(str(generation_plan['command']))}",
        ]

        for key in (
            "language",
            "package_manager",
            "working_directory",
            "install_command",
            "output_path",
        ):
            value = generation_plan.get(key)
            if value not in (None, ""):
                lines.append(f"    {key}: {cls._yaml_string(str(value))}")

        dependency_files = generation_plan.get("dependency_files")
        if dependency_files:
            lines.append("    dependency_files:")
            for item in dependency_files:
                lines.append(f"      - {cls._yaml_string(str(item))}")

        environment = generation_plan.get("environment")
        if environment:
            lines.append("    environment:")
            for key, value in environment.items():
                lines.append(
                    f"      {cls._yaml_string(str(key))}: "
                    f"{cls._yaml_string(str(value))}"
                )

        warnings = generation_plan.get("warnings")
        if warnings:
            lines.append("    warnings:")
            for warning in warnings:
                lines.append(
                    f"      - {cls._yaml_string(str(warning))}"
                )

        return "\n".join(lines) + "\n"

    @classmethod
    def _build_workflow(
        cls,
        *,
        adapter_type: str,
        framework_name: str,
        spec_path: str,
        generation_command: str,
        analyzer_action_ref: str,
        analyzer_base_url: str,
        project_id: str | None,
        token_secret_name: str,
        contract_source: str = "generated",
        generation_plan: Mapping[str, Any] | None = None,
    ) -> str:
        """Build the repository-side compatibility workflow.

        The workflow is technology-neutral. The resolved adapter supplies
        contract-acquisition/runtime metadata, while the centralized action
        performs the common BASE-vs-HEAD comparison and CI gate.

        A committed contract uses an empty generation command and requires no
        framework runtime. Generated contracts receive the adapter's runtime,
        dependency, working-directory, installation, and environment plan.
        """
        plan = dict(generation_plan or {})
        normalized_source = str(contract_source).strip() or "generated"

        project_id_value = (
            cls._yaml_string(str(project_id))
            if project_id is not None
            else "${{ vars.API_ANALYZER_PROJECT_ID }}"
        )
        token_value = "${{ secrets." + token_secret_name + " }}"
        normalized_generation_command = str(
            generation_command or ""
        ).strip()

        if normalized_source == "committed_file":
            if normalized_generation_command:
                raise ValueError(
                    "Committed contract source cannot have a generation command."
                )
            generation_input = '          generate-command: ""'
        elif normalized_generation_command:
            generation_input = (
                "          generate-command: |\n"
                f"{indent(normalized_generation_command, '            ')}"
            )
        else:
            raise ValueError(
                "Generated contract source requires a non-empty generation command."
            )

        language = str(plan.get("language") or "").strip()
        package_manager = str(plan.get("package_manager") or "").strip()
        working_directory = str(plan.get("working_directory") or "").strip()
        install_command = str(plan.get("install_command") or "").strip()
        adapter_name = str(
            plan.get("adapter_name") or adapter_type
        ).strip()
        adapter_version = str(
            plan.get("adapter_version") or "1"
        ).strip()

        dependency_files = plan.get("dependency_files") or []
        if isinstance(dependency_files, str):
            dependency_files = [
                line.strip()
                for line in dependency_files.splitlines()
                if line.strip()
            ]
        else:
            dependency_files = [
                str(item).strip()
                for item in dependency_files
                if str(item).strip()
            ]

        environment = plan.get("environment") or {}
        if not isinstance(environment, Mapping):
            environment = {}

        environment_lines: list[str] = []
        for key, value in environment.items():
            normalized_key = str(key).strip()
            normalized_value = str(value).replace("\r", "").replace("\n", "\\n")
            if normalized_key:
                environment_lines.append(
                    f"{normalized_key}={normalized_value}"
                )

        warnings = plan.get("warnings") or []
        if isinstance(warnings, str):
            warnings = [
                line.strip()
                for line in warnings.splitlines()
                if line.strip()
            ]
        else:
            warnings = [
                str(item).strip()
                for item in warnings
                if str(item).strip()
            ]

        input_lines = [
            "          contract-source: " + cls._yaml_string(normalized_source),
            "          language: " + cls._yaml_string(language),
            "          package-manager: " + cls._yaml_string(package_manager),
            "          working-directory: " + cls._yaml_string(working_directory),
            "          install-command: " + cls._yaml_string(install_command),
            "          adapter-name: " + cls._yaml_string(adapter_name),
            "          adapter-version: " + cls._yaml_string(adapter_version),
        ]

        if dependency_files:
            input_lines.append("          dependency-files: |")
            input_lines.extend(f"            {item}" for item in dependency_files)
        else:
            input_lines.append('          dependency-files: ""')

        if environment_lines:
            input_lines.append("          environment: |")
            input_lines.extend(f"            {item}" for item in environment_lines)
        else:
            input_lines.append('          environment: ""')

        if warnings:
            input_lines.append("          generator-warnings: |")
            input_lines.extend(f"            {item}" for item in warnings)
        else:
            input_lines.append('          generator-warnings: ""')

        action_inputs = "\n".join(input_lines)

        return f"""# API Analyzer compatibility workflow
# Generated automatically by API Analyzer.
#
# Adapter: {adapter_type}
# Framework: {framework_name}
# Contract: {spec_path}
# Contract source: {normalized_source}

name: API Compatibility

on:
  pull_request:
    types:
      - opened
      - synchronize
      - reopened

permissions:
  contents: read
  pull-requests: read

jobs:
  api-compatibility:
    # The one-time setup PR adds the compatibility configuration itself.
    # It must never be analyzed against its pre-setup merge-base.
    if: ${{{{ !startsWith(github.head_ref, '{RepositorySetupService.SETUP_BRANCH_PREFIX}/') }}}}
    runs-on: ubuntu-latest

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Run API Analyzer compatibility check
        id: api-analyzer
        uses: {analyzer_action_ref}
        with:
          api-base-url: {cls._yaml_string(analyzer_base_url)}
          project-id: {project_id_value}
          token: {token_value}
          spec-path: {cls._yaml_string(spec_path)}
{generation_input}
{action_inputs}
          baseline-mode: merge-base
          fail-on-error: "true"
          poll-timeout-seconds: "600"
          poll-interval-seconds: "5"

      - name: Publish API Analyzer summary
        if: always()
        shell: bash
        env:
          GATE_STATUS: ${{{{ steps.api-analyzer.outputs.gate-status }}}}
          REASON_CODE: ${{{{ steps.api-analyzer.outputs.reason-code }}}}
          COMPARISON_ID: ${{{{ steps.api-analyzer.outputs.comparison-id }}}}
          REPORT_URL: ${{{{ steps.api-analyzer.outputs.report-url }}}}
        run: |
          echo "API Analyzer gate: $GATE_STATUS"
          echo "Reason: $REASON_CODE"
          echo "Comparison: $COMPARISON_ID"

          if [[ -n "$REPORT_URL" ]]; then
            echo "Report: $REPORT_URL"
          fi
"""

    @classmethod
    def _build_pull_request_body(
        cls,
        *,
        repository_name: str,
        framework_name: str,
        adapter_type: str,
        spec_path: str,
        generation_command: str,
        analyzer_action_ref: str,
        token_secret_name: str,
        contract_source: str = "generated",
    ) -> str:
        normalized_source = str(contract_source).strip() or "generated"
        source_description = (
            "the committed contract file"
            if normalized_source == "committed_file"
            else "the framework adapter generation command"
        )

        return (
            "## API Analyzer automatic setup\n"
            "\n"
            "This pull request configures API compatibility analysis for:\n"
            "\n"
            f"- Repository: `{repository_name}`\n"
            f"- Framework: `{framework_name}`\n"
            f"- Adapter: `{adapter_type}`\n"
            f"- Contract: `{spec_path}`\n"
            "\n"
            "### What will be added\n"
            "\n"
            "- `.api-analyzer.yml`\n"
            f"- `{cls.DEFAULT_WORKFLOW_PATH}`\n"
            "\n"
            "### Contract source\n"
            "\n"
            f"The compatibility workflow reads {source_description}.\n"
            "\n"
            "### Contract generation\n"
            "\n"
            "```text\n"
            f"{generation_command}\n"
            "```\n"
            "\n"
            "### Runtime\n"
            "\n"
            "The generated workflow receives the adapter runtime and contract "
            "acquisition plan automatically. No repository-specific CI logic "
            "is required.\n"
            "\n"
            "### Platform integration\n"
            "\n"
            f"- API Analyzer action: `{analyzer_action_ref}`\n"
            f"- Actions secret: `{token_secret_name}`\n"
            "- Project identifier: the configured API_ANALYZER_PROJECT_ID "
            "repository variable when it is not embedded by the platform.\n"
            "\n"
            "The setup is intentionally reviewable. API Analyzer does not "
            "modify repository source code outside this setup pull request.\n"
            "\n"
            "The setup pull request is excluded from compatibility analysis. "
            "The first compatibility check runs on the next normal application "
            "pull request after setup is merged.\n"
            "\n"
            "No repository-specific framework logic is embedded in the "
            "analyzer core.\n"
        )

    @staticmethod
    def _setup_branch_name(*, adapter_type: str) -> str:
        normalized_adapter = (
            str(adapter_type)
            .strip()
            .lower()
            .replace("_", "-")
            .replace(" ", "-")
        )

        if not normalized_adapter:
            normalized_adapter = "repository"

        return f"{RepositorySetupService.SETUP_BRANCH_PREFIX}/{normalized_adapter}"

    @staticmethod
    def _collect_warnings(
        *,
        repository: Mapping[str, Any],
        spec_path: str,
        project_id: str | None,
        analyzer_base_url: str,
        analyzer_action_ref: str,
    ) -> list[str]:
        warnings: list[str] = []

        scan_warnings = repository.get("warnings")
        if isinstance(scan_warnings, (list, tuple)):
            warnings.extend(
                str(item)
                for item in scan_warnings
                if str(item).strip()
            )

        if not project_id:
            warnings.append(
                "Project ID was not supplied to the setup planner; "
                "the generated workflow expects the "
                "API_ANALYZER_PROJECT_ID repository variable."
            )

        if not analyzer_base_url:
            warnings.append("No API Analyzer backend URL was resolved.")

        if not analyzer_action_ref:
            warnings.append("No API Analyzer action reference was resolved.")

        if not spec_path:
            warnings.append("No contract path was detected; openapi.json will be used.")

        return warnings
