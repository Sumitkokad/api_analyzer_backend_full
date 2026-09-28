from __future__ import annotations

import os
import hashlib
import unittest
from dataclasses import fields
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Django must be initialized before importing modules that import models.
os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "project.settings",
)

import django

django.setup()

from api_app.github_actions_provisioning_service import (
    GitHubActionsProvisioningResult,
    GitHubActionsProvisioningService,
)
from api_app.github_service import GitHubAPIError


class TestGitHubActionsProvisioningService(unittest.TestCase):
    def setUp(self):
        self.project = SimpleNamespace(pk=42)

        self.github_client = MagicMock()

        self.github_client.create_installation_token.return_value = {
            "token": "github-installation-token"
        }

        self.github_client.create_or_update_actions_variable.return_value = {
            "name": "API_ANALYZER_PROJECT_ID",
            "configured": True,
        }

        self.github_client.create_or_update_actions_secret.return_value = {
            "name": "API_ANALYZER_TOKEN",
            "configured": True,
        }

        self.service = GitHubActionsProvisioningService(
            github_client=self.github_client
        )

    @patch(
        "api_app.github_actions_provisioning_service.ProjectToken.objects"
    )
    @patch(
        "api_app.github_actions_provisioning_service.transaction.atomic"
    )
    @patch(
        "api_app.github_actions_provisioning_service.secrets.token_urlsafe",
        return_value="generated-project-token",
    )
    def test_provision_creates_token_after_github_configuration(
        self,
        mock_token_urlsafe,
        mock_atomic,
        mock_project_tokens,
    ):
        mock_atomic.return_value.__enter__.return_value = None
        mock_atomic.return_value.__exit__.return_value = False

        active_tokens = MagicMock()
        active_tokens.update.return_value = 2
        mock_project_tokens.filter.return_value = active_tokens

        created_token = SimpleNamespace(pk=99)
        mock_project_tokens.create.return_value = created_token

        result = self.service.provision(
            project=self.project,
            installation_id=123,
            repository_full_name="owner/repository",
        )

        self.assertIsInstance(
            result,
            GitHubActionsProvisioningResult,
        )
        self.assertEqual(result.project_id, 42)
        self.assertEqual(
            result.repository_full_name,
            "owner/repository",
        )
        self.assertEqual(result.token_id, 99)
        self.assertEqual(result.rotated_existing_tokens, 2)
        self.assertTrue(result.secret_configured)
        self.assertTrue(result.variable_configured)

        self.github_client.create_installation_token.assert_called_once_with(
            123
        )

        self.github_client.create_or_update_actions_variable.assert_called_once_with(
            "github-installation-token",
            "owner/repository",
            variable_name="API_ANALYZER_PROJECT_ID",
            value="42",
        )

        self.github_client.create_or_update_actions_secret.assert_called_once_with(
            "github-installation-token",
            "owner/repository",
            secret_name="API_ANALYZER_TOKEN",
            secret_value="generated-project-token",
        )

        expected_hash = hashlib.sha256(
            b"generated-project-token"
        ).hexdigest()

        mock_project_tokens.create.assert_called_once_with(
            project=self.project,
            name="GitHub Actions CI",
            token_hash=expected_hash,
        )

        mock_token_urlsafe.assert_called_once_with(48)

    @patch(
        "api_app.github_actions_provisioning_service.ProjectToken.objects"
    )
    @patch(
        "api_app.github_actions_provisioning_service.transaction.atomic"
    )
    @patch(
        "api_app.github_actions_provisioning_service.secrets.token_urlsafe",
        return_value="generated-project-token",
    )
    def test_github_failure_does_not_create_database_token(
        self,
        mock_token_urlsafe,
        mock_atomic,
        mock_project_tokens,
    ):
        self.github_client.create_or_update_actions_secret.side_effect = (
            GitHubAPIError("GitHub secret failed")
        )

        with self.assertRaises(GitHubAPIError):
            self.service.provision(
                project=self.project,
                installation_id=123,
                repository_full_name="owner/repository",
            )

        mock_project_tokens.create.assert_not_called()
        mock_project_tokens.filter.assert_not_called()
        mock_atomic.assert_not_called()
        mock_token_urlsafe.assert_called_once_with(48)

    def test_invalid_repository_is_rejected_before_token_generation(self):
        with patch(
            "api_app.github_actions_provisioning_service.secrets.token_urlsafe"
        ) as mock_token_urlsafe:
            with self.assertRaises(ValueError):
                self.service.provision(
                    project=self.project,
                    installation_id=123,
                    repository_full_name="invalid-repository-name",
                )

            mock_token_urlsafe.assert_not_called()

    def test_result_schema_does_not_expose_raw_token(self):
        field_names = {
            field.name
            for field in fields(GitHubActionsProvisioningResult)
        }

        self.assertNotIn("raw_token", field_names)
        self.assertNotIn("token", field_names)

        expected_fields = {
            "project_id",
            "repository_full_name",
            "secret_name",
            "variable_name",
            "token_id",
            "rotated_existing_tokens",
            "secret_configured",
            "variable_configured",
        }

        self.assertEqual(field_names, expected_fields)


if __name__ == "__main__":
    unittest.main()
