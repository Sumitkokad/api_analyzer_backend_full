"""
GitHub repository setup execution.

This service executes a previously generated RepositorySetupService plan
against GitHub using a GitHub App installation token.

The service does not contain framework-specific logic and does not create
the setup plan itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .github_service import (
    GitHubAPIError,
    GitHubAppClient,
)
from .setup_service import SetupPlan


@dataclass(frozen=True)
class SetupExecutionResult:
    """
    Result of executing a repository setup plan.
    """

    success: bool

    repository: str
    branch_name: str
    base_branch: str

    files_written: tuple[str, ...] = ()
    pull_request_number: int | None = None
    pull_request_url: str | None = None

    warnings: tuple[str, ...] = field(
        default_factory=tuple
    )

    error: str | None = None

    metadata: dict[str, Any] = field(
        default_factory=dict
    )


class GitHubWriteService:
    """
    Execute repository setup plans through a GitHub App installation.

    The GitHub App installation token is created internally from the
    installation ID. The token is never returned in the execution result.
    """

    def __init__(
        self,
        *,
        github_client: GitHubAppClient | None = None,
    ) -> None:
        self.github_client = (
            github_client
            or GitHubAppClient()
        )

    def execute_setup(
        self,
        *,
        installation_id: int,
        plan: SetupPlan,
    ) -> SetupExecutionResult:
        """
        Execute a setup plan.

        Steps:
        1. Create a GitHub App installation token.
        2. Create the setup branch from the configured base branch.
        3. Write every setup file to that branch.
        4. Create the setup pull request.

        If any operation fails, the result is unsuccessful and the
        exception details are returned as a safe error message.
        """

        self._validate_installation_id(
            installation_id
        )

        self._validate_plan(plan)

        files_written: list[str] = []

        try:
            token_data = (
                self.github_client.create_installation_token(
                    installation_id
                )
            )

            installation_token = str(
                token_data.get("token") or ""
            ).strip()

            if not installation_token:
                raise GitHubAPIError(
                    "GitHub installation token was not returned."
                )

            self.github_client.create_branch(
                installation_token,
                plan.repository,
                branch_name=plan.branch_name,
                from_branch=plan.base_branch,
            )

            for setup_file in plan.files:
                self.github_client.create_or_update_repository_file(
                    installation_token,
                    plan.repository,
                    path=setup_file.path,
                    content=setup_file.content,
                    branch=plan.branch_name,
                    commit_message=(
                        "chore: configure API compatibility analysis"
                    ),
                )

                files_written.append(
                    setup_file.path
                )

            pull_request = (
                self.github_client.create_pull_request(
                    installation_token,
                    plan.repository,
                    title=plan.pull_request_title,
                    head=plan.branch_name,
                    base=plan.base_branch,
                    body=plan.pull_request_body,
                )
            )

            pull_request_number = self._pull_request_number(
                pull_request
            )

            pull_request_url = self._pull_request_url(
                pull_request
            )

            return SetupExecutionResult(
                success=True,
                repository=plan.repository,
                branch_name=plan.branch_name,
                base_branch=plan.base_branch,
                files_written=tuple(files_written),
                pull_request_number=pull_request_number,
                pull_request_url=pull_request_url,
                warnings=tuple(plan.warnings),
                metadata={
                    "setup_mode": "automatic",
                    "review_required": True,
                    "adapter_type": plan.adapter_type,
                    "framework_name": plan.framework_name,
                    "spec_path": plan.spec_path,
                },
            )

        except GitHubAPIError as exc:
            return SetupExecutionResult(
                success=False,
                repository=plan.repository,
                branch_name=plan.branch_name,
                base_branch=plan.base_branch,
                files_written=tuple(files_written),
                warnings=tuple(plan.warnings),
                error=str(exc),
                metadata={
                    "setup_mode": "automatic",
                    "failed_stage": self._failed_stage(
                        files_written=files_written,
                        total_files=len(plan.files),
                    ),
                    "adapter_type": plan.adapter_type,
                    "framework_name": plan.framework_name,
                },
            )

        except Exception as exc:
            return SetupExecutionResult(
                success=False,
                repository=plan.repository,
                branch_name=plan.branch_name,
                base_branch=plan.base_branch,
                files_written=tuple(files_written),
                warnings=tuple(plan.warnings),
                error=(
                    "Unexpected setup execution failure: "
                    f"{exc}"
                ),
                metadata={
                    "setup_mode": "automatic",
                    "failed_stage": self._failed_stage(
                        files_written=files_written,
                        total_files=len(plan.files),
                    ),
                    "adapter_type": plan.adapter_type,
                    "framework_name": plan.framework_name,
                },
            )

    @staticmethod
    def _validate_installation_id(
        installation_id: int,
    ) -> None:
        try:
            normalized = int(installation_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "installation_id must be a positive integer."
            ) from exc

        if normalized <= 0:
            raise ValueError(
                "installation_id must be a positive integer."
            )

    @staticmethod
    def _validate_plan(
        plan: SetupPlan,
    ) -> None:
        if not plan.repository.strip():
            raise ValueError(
                "Setup plan repository cannot be empty."
            )

        if not plan.base_branch.strip():
            raise ValueError(
                "Setup plan base_branch cannot be empty."
            )

        if not plan.branch_name.strip():
            raise ValueError(
                "Setup plan branch_name cannot be empty."
            )

        if not plan.pull_request_title.strip():
            raise ValueError(
                "Setup plan pull_request_title cannot be empty."
            )

        if not plan.files:
            raise ValueError(
                "Setup plan must contain at least one file."
            )

        paths: set[str] = set()

        for setup_file in plan.files:
            path = str(setup_file.path).strip()

            if not path:
                raise ValueError(
                    "Setup plan contains a file with an empty path."
                )

            if path in paths:
                raise ValueError(
                    f"Setup plan contains duplicate file path: {path}"
                )

            paths.add(path)

            if not isinstance(
                setup_file.content,
                str,
            ):
                raise ValueError(
                    f"Setup file '{path}' content must be text."
                )

    @staticmethod
    def _pull_request_number(
        pull_request: Any,
    ) -> int | None:
        if not isinstance(
            pull_request,
            dict,
        ):
            return None

        raw_number = pull_request.get(
            "number"
        )

        if raw_number is None:
            return None

        try:
            return int(raw_number)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _pull_request_url(
        pull_request: Any,
    ) -> str | None:
        if not isinstance(
            pull_request,
            dict,
        ):
            return None

        url = str(
            pull_request.get("html_url")
            or ""
        ).strip()

        return url or None

    @staticmethod
    def _failed_stage(
        *,
        files_written: list[str],
        total_files: int,
    ) -> str:
        if not files_written:
            return "branch_creation"

        if len(files_written) < total_files:
            return "file_write"

        return "pull_request_creation"


__all__ = [
    "GitHubWriteService",
    "SetupExecutionResult",
]