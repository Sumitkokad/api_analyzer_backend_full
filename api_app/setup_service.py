"""
Automatic repository setup planning.

This module converts repository scan results into a reviewable setup plan.
It does not call GitHub and does not modify repositories.

The resulting plan can later be consumed by a GitHub write service that
creates a setup branch and pull request.

The design is framework-agnostic. Framework-specific behavior comes from
the detected adapter and its contract-generation plan.

The generated workflow delegates compatibility execution to the
API Analyzer composite action. Repository-specific framework knowledge
stays inside the adapter; this service only wires the detected plan into
a reviewable GitHub Actions workflow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from typing import Any, Mapping
from urllib.parse import urlparse


@dataclass(frozen=True)
class SetupFile:
    """
    A file that should be created or updated by the setup PR.
    """

    path: str
    content: str


@dataclass(frozen=True)
class SetupPlan:
    """
    Complete reviewable setup plan for a repository.
    """

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

    This service does not assume a particular repository such as CodeForge.
    It operates entirely from detected repository metadata.

    The API Analyzer action reference and backend URL are configurable so
    the same setup logic can be used across environments.
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
        """
        Build a setup PR plan for the detected repository.

        Parameters
        ----------
        repository:
            Repository metadata returned by the GitHub scanner.

        adapter:
            Resolved contract adapter instance.

        base_branch:
            Repository default branch.

        spec_path:
            Optional explicitly configured contract path.

        project_id:
            Optional API Analyzer project identifier. When omitted, the
            generated workflow reads it from the repository variable
            API_ANALYZER_PROJECT_ID.

        analyzer_base_url:
            Optional API Analyzer backend URL. When omitted, repository
            metadata or the configured service default is used.

        analyzer_action_ref:
            Optional GitHub Action reference. When omitted, repository
            metadata or the configured service default is used.

        token_secret_name:
            Repository Actions secret that contains the project-scoped
            API Analyzer token.
        """

        repository_name = self._repository_name(repository)

        branch = self._normalize_branch(
            base_branch
            or repository.get("default_branch")
            or "main"
        )

        adapter_type = self._adapter_value(
            adapter,
            "adapter_type",
        )

        framework_name = self._adapter_value(
            adapter,
            "framework_name",
            default=adapter_type,
        )

        resolved_spec_path = (
            self._normalize_path(spec_path)
            if spec_path
            else self._repository_spec_path(repository)
        )

        if not resolved_spec_path:
            resolved_spec_path = "openapi.json"

        commit_sha = str(
            repository.get("commit_sha")
            or ""
        ).strip()

        generation_command = self._generation_command(
            adapter=adapter,
            repository=repository,
            commit_sha=commit_sha,
        )

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

        resolved_token_secret = (
            str(
                token_secret_name
                or repository.get("token_secret_name")
                or self.DEFAULT_TOKEN_SECRET_NAME
            ).strip()
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
        )

        config_content = self._build_config(
            adapter_type=adapter_type,
            framework_name=framework_name,
            spec_path=resolved_spec_path,
            analyzer_base_url=resolved_analyzer_base_url,
            project_id=resolved_project_id,
            analyzer_action_ref=resolved_action_ref,
            token_secret_name=resolved_token_secret,
        )

        files = (
            SetupFile(
                path=self.DEFAULT_CONFIG_PATH,
                content=config_content,
            ),
            SetupFile(
                path=self.DEFAULT_WORKFLOW_PATH,
                content=workflow_content,
            ),
        )

        branch_name = self._setup_branch_name(
            adapter_type=adapter_type,
        )

        pull_request_title = "chore: configure API compatibility analysis"

        pull_request_body = self._build_pull_request_body(
            repository_name=repository_name,
            framework_name=framework_name,
            adapter_type=adapter_type,
            spec_path=resolved_spec_path,
            generation_command=generation_command,
            analyzer_action_ref=resolved_action_ref,
            token_secret_name=resolved_token_secret,
        )

        warnings = self._collect_warnings(
            repository=repository,
            adapter=adapter,
            spec_path=resolved_spec_path,
            project_id=resolved_project_id,
            analyzer_base_url=resolved_analyzer_base_url,
            analyzer_action_ref=resolved_action_ref,
        )

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
    def _repository_name(
        repository: Mapping[str, Any],
    ) -> str:
        for key in (
            "repository_full_name",
            "full_name",
            "repository",
            "name",
        ):
            value = repository.get(key)

            if value:
                return str(value).strip()

        raise ValueError(
            "Repository name is required to build a setup plan."
        )

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

        return normalized

    @staticmethod
    def _normalize_path(path: str) -> str:
        normalized = (
            str(path)
            .strip()
            .replace("\\", "/")
        )

        while normalized.startswith("./"):
            normalized = normalized[2:]

        normalized = normalized.lstrip("/")

        if not normalized or normalized == ".":
            raise ValueError("Contract path cannot be empty.")

        parts = normalized.split("/")

        if ".." in parts:
            raise ValueError(
                "Contract path cannot escape the repository root."
            )

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
    def _generation_command(
        *,
        adapter: Any,
        repository: Mapping[str, Any],
        commit_sha: str,
    ) -> str:
        """
        Ask the adapter for its generation plan.

        The adapter remains the only component that knows the framework-
        specific generation command.
        """

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
                "Adapter contract generation plan did not provide "
                "a command."
            )

        return str(command).strip()

    def _resolve_project_id(
        self,
        *,
        repository: Mapping[str, Any],
        project_id: str | int | None,
    ) -> str | None:
        value = (
            project_id
            if project_id is not None
            else repository.get("project_id")
        )

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

        return normalized

    @staticmethod
    def _normalize_base_url(value: str) -> str:
        normalized = value.strip().rstrip("/")

        if not normalized:
            raise ValueError(
                "API Analyzer base URL cannot be empty."
            )

        parsed = urlparse(normalized)

        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(
                "API Analyzer base URL must be an absolute HTTP(S) URL."
            )

        return normalized

    @staticmethod
    def _validate_analyzer_action_ref(action_ref: str) -> None:
        if "/" not in action_ref or "@" not in action_ref:
            raise ValueError(
                "API Analyzer action reference must look like "
                "'owner/repository/path@ref'."
            )

    @staticmethod
    def _build_config(
        *,
        adapter_type: str,
        framework_name: str,
        spec_path: str,
        analyzer_base_url: str,
        project_id: str | None,
        analyzer_action_ref: str,
        token_secret_name: str,
    ) -> str:
        project_value = (
            project_id
            if project_id is not None
            else "repository variable: API_ANALYZER_PROJECT_ID"
        )

        return (
            "# API Analyzer configuration\n"
            "# Generated automatically by API Analyzer.\n"
            "# Review this file in the setup pull request before merging.\n"
            "\n"
            "api_analyzer:\n"
            f'  adapter: "{adapter_type}"\n'
            f'  framework: "{framework_name}"\n'
            f'  spec_path: "{spec_path}"\n'
            '  baseline_mode: "merge-base"\n'
            f'  analyzer_base_url: "{analyzer_base_url}"\n'
            f'  analyzer_action: "{analyzer_action_ref}"\n'
            f'  project_id: "{project_value}"\n'
            f'  token_secret: "{token_secret_name}"\n'
        )

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
    ) -> str:
        """
        Build the real repository-side compatibility workflow.

        The generated workflow delegates comparison execution to the
        platform-maintained composite action. The action is responsible
        for creating the exact base/head snapshots, submitting the
        analysis, preserving queue behavior, polling, and enforcing the
        final gate.

        The repository setup service only supplies detected configuration.
        """

        project_id_value = (
            json.dumps(str(project_id))
            if project_id is not None
            else "${{ vars.API_ANALYZER_PROJECT_ID }}"
        )

        token_value = (
            "${{ secrets." + token_secret_name + " }}"
        )

        return f"""# API Analyzer compatibility workflow
# Generated automatically by API Analyzer.
#
# Adapter: {adapter_type}
# Framework: {framework_name}
# Contract: {spec_path}

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
    # The setup PR introduces the contract-generation dependencies/configuration
    # that the compatibility check itself needs. Do not compare that PR before
    # those changes are merged; the first real compatibility check starts on the
    # next application PR.
    if: ${{ !startsWith(github.head_ref, 'api-analyzer/setup/') }}
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
          api-base-url: {json.dumps(analyzer_base_url)}
          project-id: {project_id_value}
          token: {token_value}
          spec-path: {json.dumps(spec_path)}
          generate-command: |
            {generation_command}
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
    ) -> str:
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
            "### Detected contract generation\n"
            "\n"
            "```text\n"
            f"{generation_command}\n"
            "```\n"
            "\n"
            "### Platform integration\n"
            "\n"
            f"- API Analyzer action: `{analyzer_action_ref}`\n"
            f"- Actions secret: `{token_secret_name}`\n"
            "- Project identifier: the configured API_ANALYZER_PROJECT_ID "
            "repository variable when it is not embedded by the platform.\n"
            "\n"
            "The setup is intentionally reviewable. "
            "API Analyzer does not modify repository source code outside "
            "this setup pull request.\n"
            "\n"
            "After this pull request is merged, future pull requests will "
            "run the compatibility workflow automatically.\n"
            "\n"
            "No repository-specific framework logic is embedded in the "
            "analyzer core.\n"
        )

    @staticmethod
    def _setup_branch_name(
        *,
        adapter_type: str,
    ) -> str:
        normalized_adapter = (
            str(adapter_type)
            .strip()
            .lower()
            .replace("_", "-")
            .replace(" ", "-")
        )

        if not normalized_adapter:
            normalized_adapter = "repository"

        return (
            f"{RepositorySetupService.SETUP_BRANCH_PREFIX}/"
            f"{normalized_adapter}"
        )

    @staticmethod
    def _collect_warnings(
        *,
        repository: Mapping[str, Any],
        adapter: Any,
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
            warnings.append(
                "No API Analyzer backend URL was resolved."
            )

        if not analyzer_action_ref:
            warnings.append(
                "No API Analyzer action reference was resolved."
            )

        if not spec_path:
            warnings.append(
                "No contract path was detected; openapi.json will be used."
            )

        return warnings
