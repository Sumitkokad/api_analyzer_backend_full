"""
Automatic GitHub Actions credential provisioning.

This service connects an existing GitHub App installation to an API Analyzer
project by creating the repository-level GitHub Actions values required by
an automatically generated CI workflow.

Security properties:
    - the raw project token exists only in memory during provisioning;
    - the database stores only the SHA-256 token hash;
    - the raw token is never returned, logged, or written to audit metadata;
    - GitHub receives the token only through the encrypted Actions secret API;
    - a successful rotation revokes older active project CI tokens.

The service is framework-agnostic. Repository framework detection and
contract generation remain outside this module.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import secrets
from typing import Any

from django.db import transaction
from django.utils import timezone

from .github_service import (
    GitHubAPIError,
    GitHubAppClient,
    GitHubConfigurationError,
)
from .models import Project, ProjectToken


@dataclass(frozen=True)
class GitHubActionsProvisioningResult:
    """
    Public result of GitHub Actions credential provisioning.

    The raw project token is intentionally absent from this object.
    """

    project_id: int
    repository_full_name: str
    secret_name: str
    variable_name: str
    token_id: int
    rotated_existing_tokens: int
    secret_configured: bool
    variable_configured: bool


class GitHubActionsProvisioningService:
    """
    Provision the project-scoped CI credential required by GitHub Actions.

    The caller supplies an already-connected GitHub App installation and a
    selected repository. This service does not handle OAuth, repository
    selection, framework detection, or setup-PR creation.
    """

    DEFAULT_SECRET_NAME = "API_ANALYZER_TOKEN"
    DEFAULT_VARIABLE_NAME = "API_ANALYZER_PROJECT_ID"
    DEFAULT_TOKEN_NAME = "GitHub Actions CI"

    def __init__(
        self,
        *,
        github_client: GitHubAppClient | None = None,
        secret_name: str = DEFAULT_SECRET_NAME,
        variable_name: str = DEFAULT_VARIABLE_NAME,
        token_name: str = DEFAULT_TOKEN_NAME,
    ) -> None:
        self.github_client = github_client or GitHubAppClient()
        self.secret_name = self._normalize_actions_name(
            secret_name,
            kind="secret",
        )
        self.variable_name = self._normalize_actions_name(
            variable_name,
            kind="variable",
        )
        self.token_name = self._normalize_token_name(token_name)

    def provision(
        self,
        *,
        project: Project,
        installation_id: int,
        repository_full_name: str,
    ) -> GitHubActionsProvisioningResult:
        """
        Generate, provision, and persist a new project CI credential.

        GitHub repository configuration is completed before the raw token is
        persisted as a hash in the database. If remote provisioning fails,
        no new ProjectToken database record is created.
        """
        if project is None:
            raise ValueError("project is required.")

        try:
            project_id = int(project.pk)
        except (TypeError, ValueError) as exc:
            raise ValueError("Project must have a valid primary key.") from exc

        if project_id <= 0:
            raise ValueError("Project must have a valid primary key.")

        try:
            installation_id = int(installation_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("installation_id must be an integer.") from exc

        if installation_id <= 0:
            raise ValueError("installation_id must be positive.")

        repository = self._normalize_repository(
            repository_full_name
        )

        raw_token = secrets.token_urlsafe(48)
        token_hash = hashlib.sha256(
            raw_token.encode("utf-8")
        ).hexdigest()

        installation_token = self._create_installation_token(
            installation_id
        )

        try:
            variable_result = (
                self.github_client.create_or_update_actions_variable(
                    installation_token,
                    repository,
                    variable_name=self.variable_name,
                    value=str(project_id),
                )
            )

            secret_result = (
                self.github_client.create_or_update_actions_secret(
                    installation_token,
                    repository,
                    secret_name=self.secret_name,
                    secret_value=raw_token,
                )
            )
        except (
            GitHubAPIError,
            GitHubConfigurationError,
        ):
            # Deliberately do not log or attach the plaintext token.
            raise
        finally:
            # Drop our local reference as early as Python's reference model
            # allows after the remote provisioning calls complete.
            raw_token = ""

        if not variable_result.get("configured"):
            raise GitHubAPIError(
                "GitHub Actions project variable was not configured."
            )

        if not secret_result.get("configured"):
            raise GitHubAPIError(
                "GitHub Actions project secret was not configured."
            )

        with transaction.atomic():
            revoked_count = (
                ProjectToken.objects
                .filter(
                    project=project,
                    name=self.token_name,
                    revoked_at__isnull=True,
                )
                .update(
                    revoked_at=timezone.now(),
                    updated_at=timezone.now(),
                )
            )

            token = ProjectToken.objects.create(
                project=project,
                name=self.token_name,
                token_hash=token_hash,
            )

        return GitHubActionsProvisioningResult(
            project_id=project_id,
            repository_full_name=repository,
            secret_name=self.secret_name,
            variable_name=self.variable_name,
            token_id=int(token.pk),
            rotated_existing_tokens=int(revoked_count),
            secret_configured=True,
            variable_configured=True,
        )

    def _create_installation_token(
        self,
        installation_id: int,
    ) -> str:
        response = self.github_client.create_installation_token(
            installation_id
        )

        token = str(
            response.get("token") or ""
        ).strip()

        if not token:
            raise GitHubAPIError(
                "GitHub installation token response is invalid."
            )

        return token

    @staticmethod
    def _normalize_repository(
        repository_full_name: str,
    ) -> str:
        value = str(repository_full_name or "").strip()
        parts = value.split("/", 1)

        if len(parts) != 2 or not all(part.strip() for part in parts):
            raise ValueError(
                "repository_full_name must use the owner/repository format."
            )

        owner = parts[0].strip()
        repository = parts[1].strip()

        return f"{owner}/{repository}"

    @staticmethod
    def _normalize_actions_name(
        name: str,
        *,
        kind: str,
    ) -> str:
        value = str(name or "").strip()

        if not value:
            raise ValueError(
                f"GitHub Actions {kind} name is required."
            )

        if value.startswith("GITHUB_"):
            raise ValueError(
                f"GitHub Actions {kind} name cannot start with GITHUB_."
            )

        if not all(
            character.isascii()
            and (
                character.isalnum()
                or character == "_"
            )
            for character in value
        ):
            raise ValueError(
                f"GitHub Actions {kind} name may contain only "
                "ASCII letters, numbers, and underscores."
            )

        return value

    @staticmethod
    def _normalize_token_name(
        name: str,
    ) -> str:
        value = str(name or "").strip()

        if not value:
            raise ValueError(
                "Project token name is required."
            )

        return value


__all__ = [
    "GitHubActionsProvisioningResult",
    "GitHubActionsProvisioningService",
]
