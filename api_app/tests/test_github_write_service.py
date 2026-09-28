from unittest import TestCase
from unittest.mock import Mock

from api_app.github_write_service import (
    GitHubWriteService,
    SetupExecutionResult,
)
from api_app.setup_service import (
    SetupFile,
    SetupPlan,
)


class GitHubWriteServiceTests(TestCase):
    def setUp(self):
        self.github_client = Mock()

        self.service = GitHubWriteService(
            github_client=self.github_client,
        )

        self.plan = SetupPlan(
            repository="example/api-project",
            base_branch="main",
            branch_name="api-analyzer/setup/django-rest-framework",
            pull_request_title=(
                "chore: configure API compatibility analysis"
            ),
            pull_request_body=(
                "Automatic API Analyzer setup."
            ),
            files=(
                SetupFile(
                    path=".api-analyzer.yml",
                    content=(
                        "api_analyzer:\n"
                        '  adapter: "django-rest-framework"\n'
                    ),
                ),
                SetupFile(
                    path=".github/workflows/api-compatibility.yml",
                    content=(
                        "name: API Compatibility\n"
                    ),
                ),
            ),
            adapter_type="django-rest-framework",
            framework_name="Django REST Framework",
            spec_path="openapi.json",
            generation_command=(
                "python manage.py spectacular "
                "--file openapi.json --validate"
            ),
        )

        self.github_client.create_installation_token.return_value = {
            "token": "installation-token",
        }

        self.github_client.create_branch.return_value = {
            "ref": (
                "refs/heads/"
                "api-analyzer/setup/django-rest-framework"
            )
        }

        self.github_client.create_or_update_repository_file.return_value = {
            "content": {
                "path": ".api-analyzer.yml",
            }
        }

        self.github_client.create_pull_request.return_value = {
            "number": 42,
            "html_url": (
                "https://github.com/"
                "example/api-project/pull/42"
            ),
        }

    def test_execute_setup_success(self):
        result = self.service.execute_setup(
            installation_id=123,
            plan=self.plan,
        )

        self.assertIsInstance(
            result,
            SetupExecutionResult,
        )

        self.assertTrue(result.success)

        self.assertEqual(
            result.repository,
            "example/api-project",
        )

        self.assertEqual(
            result.branch_name,
            "api-analyzer/setup/django-rest-framework",
        )

        self.assertEqual(
            result.base_branch,
            "main",
        )

        self.assertEqual(
            result.files_written,
            (
                ".api-analyzer.yml",
                ".github/workflows/api-compatibility.yml",
            ),
        )

        self.assertEqual(
            result.pull_request_number,
            42,
        )

        self.assertEqual(
            result.pull_request_url,
            (
                "https://github.com/"
                "example/api-project/pull/42"
            ),
        )

        self.assertIsNone(
            result.error
        )

    def test_installation_token_is_created(self):
        self.service.execute_setup(
            installation_id=123,
            plan=self.plan,
        )

        self.github_client.create_installation_token.assert_called_once_with(
            123
        )

    def test_branch_is_created_from_base_branch(self):
        self.service.execute_setup(
            installation_id=123,
            plan=self.plan,
        )

        self.github_client.create_branch.assert_called_once_with(
            "installation-token",
            "example/api-project",
            branch_name=(
                "api-analyzer/setup/"
                "django-rest-framework"
            ),
            from_branch="main",
        )

    def test_all_setup_files_are_written(self):
        self.service.execute_setup(
            installation_id=123,
            plan=self.plan,
        )

        self.assertEqual(
            self.github_client.create_or_update_repository_file.call_count,
            2,
        )

        calls = (
            self.github_client
            .create_or_update_repository_file
            .call_args_list
        )

        self.assertEqual(
            calls[0].kwargs["path"],
            ".api-analyzer.yml",
        )

        self.assertEqual(
            calls[0].kwargs["branch"],
            "api-analyzer/setup/django-rest-framework",
        )

        self.assertEqual(
            calls[1].kwargs["path"],
            ".github/workflows/api-compatibility.yml",
        )

    def test_pull_request_is_created_after_files(self):
        self.service.execute_setup(
            installation_id=123,
            plan=self.plan,
        )

        self.github_client.create_pull_request.assert_called_once_with(
            "installation-token",
            "example/api-project",
            title=(
                "chore: configure API compatibility analysis"
            ),
            head=(
                "api-analyzer/setup/"
                "django-rest-framework"
            ),
            base="main",
            body="Automatic API Analyzer setup.",
        )

    def test_missing_installation_token_returns_failure(self):
        self.github_client.create_installation_token.return_value = {}

        result = self.service.execute_setup(
            installation_id=123,
            plan=self.plan,
        )

        self.assertFalse(result.success)

        self.assertEqual(
            result.error,
            "GitHub installation token was not returned.",
        )

        self.github_client.create_branch.assert_not_called()

    def test_branch_creation_failure_returns_failure(self):
        self.github_client.create_branch.side_effect = Exception(
            "branch creation failed"
        )

        result = self.service.execute_setup(
            installation_id=123,
            plan=self.plan,
        )

        self.assertFalse(result.success)

        self.assertIn(
            "Unexpected setup execution failure",
            result.error,
        )

        self.assertEqual(
            result.metadata["failed_stage"],
            "branch_creation",
        )

    def test_file_write_failure_returns_partial_files(self):
        self.github_client.create_or_update_repository_file.side_effect = [
            {
                "content": {
                    "path": ".api-analyzer.yml",
                }
            },
            Exception("file write failed"),
        ]

        result = self.service.execute_setup(
            installation_id=123,
            plan=self.plan,
        )

        self.assertFalse(result.success)

        self.assertEqual(
            result.files_written,
            (".api-analyzer.yml",),
        )

        self.assertEqual(
            result.metadata["failed_stage"],
            "file_write",
        )

        self.github_client.create_pull_request.assert_not_called()

    def test_pull_request_failure_returns_failure(self):
        self.github_client.create_pull_request.side_effect = Exception(
            "pull request failed"
        )

        result = self.service.execute_setup(
            installation_id=123,
            plan=self.plan,
        )

        self.assertFalse(result.success)

        self.assertEqual(
            result.files_written,
            (
                ".api-analyzer.yml",
                ".github/workflows/api-compatibility.yml",
            ),
        )

        self.assertEqual(
            result.metadata["failed_stage"],
            "pull_request_creation",
        )

    def test_invalid_installation_id_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.service.execute_setup(
                installation_id=0,
                plan=self.plan,
            )

        self.github_client.create_installation_token.assert_not_called()

    def test_empty_plan_files_raises_value_error(self):
        invalid_plan = SetupPlan(
            repository=self.plan.repository,
            base_branch=self.plan.base_branch,
            branch_name=self.plan.branch_name,
            pull_request_title=self.plan.pull_request_title,
            pull_request_body=self.plan.pull_request_body,
            files=(),
            adapter_type=self.plan.adapter_type,
            framework_name=self.plan.framework_name,
            spec_path=self.plan.spec_path,
            generation_command=self.plan.generation_command,
        )

        with self.assertRaises(ValueError):
            self.service.execute_setup(
                installation_id=123,
                plan=invalid_plan,
            )

    def test_duplicate_setup_file_paths_raise_value_error(self):
        invalid_plan = SetupPlan(
            repository=self.plan.repository,
            base_branch=self.plan.base_branch,
            branch_name=self.plan.branch_name,
            pull_request_title=self.plan.pull_request_title,
            pull_request_body=self.plan.pull_request_body,
            files=(
                SetupFile(
                    path=".api-analyzer.yml",
                    content="first",
                ),
                SetupFile(
                    path=".api-analyzer.yml",
                    content="second",
                ),
            ),
            adapter_type=self.plan.adapter_type,
            framework_name=self.plan.framework_name,
            spec_path=self.plan.spec_path,
            generation_command=self.plan.generation_command,
        )

        with self.assertRaises(ValueError):
            self.service.execute_setup(
                installation_id=123,
                plan=invalid_plan,
            )


if __name__ == "__main__":
    import unittest

    unittest.main()