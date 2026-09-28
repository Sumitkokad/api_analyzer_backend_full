from unittest import TestCase
from unittest.mock import Mock

from api_app.github_service import (
    GitHubAPIError,
    GitHubAppClient,
)


class GitHubWriteMethodsTests(TestCase):
    def setUp(self):
        # The write methods under test do not require Django settings,
        # so avoid initializing the real client configuration.
        self.client = GitHubAppClient.__new__(
            GitHubAppClient
        )

        self.installation_token = "installation-token"
        self.repository = "example/api-project"

    def test_create_branch_from_sha(self):
        self.client._request = Mock(
            return_value={
                "ref": "refs/heads/api-analyzer/setup/test",
                "object": {
                    "sha": "b" * 40,
                },
            }
        )

        result = self.client.create_branch(
            self.installation_token,
            self.repository,
            branch_name="api-analyzer/setup/test",
            from_sha="a" * 40,
        )

        self.assertEqual(
            result["ref"],
            "refs/heads/api-analyzer/setup/test",
        )

        self.client._request.assert_called_once_with(
            "POST",
            "/repos/example/api-project/git/refs",
            authorization=(
                f"Bearer {self.installation_token}"
            ),
            json={
                "ref": "refs/heads/api-analyzer/setup/test",
                "sha": "a" * 40,
            },
        )

    def test_create_branch_from_branch(self):
        self.client._request = Mock(
            side_effect=[
                {
                    "ref": "refs/heads/main",
                    "object": {
                        "sha": "a" * 40,
                    },
                },
                {
                    "ref": "refs/heads/api-analyzer/setup/test",
                    "object": {
                        "sha": "b" * 40,
                    },
                },
            ]
        )

        result = self.client.create_branch(
            self.installation_token,
            self.repository,
            branch_name="api-analyzer/setup/test",
            from_branch="main",
        )

        self.assertEqual(
            result["object"]["sha"],
            "b" * 40,
        )

        self.assertEqual(
            self.client._request.call_count,
            2,
        )

        first_call = self.client._request.call_args_list[0]

        self.assertEqual(
            first_call.args[0],
            "GET",
        )

        self.assertEqual(
            first_call.args[1],
            "/repos/example/api-project/git/ref/heads/main",
        )

        second_call = self.client._request.call_args_list[1]

        self.assertEqual(
            second_call.args[0],
            "POST",
        )

        self.assertEqual(
            second_call.args[1],
            "/repos/example/api-project/git/refs",
        )

        self.assertEqual(
            second_call.kwargs["json"],
            {
                "ref": "refs/heads/api-analyzer/setup/test",
                "sha": "a" * 40,
            },
        )

    def test_create_branch_rejects_both_sources(self):
        with self.assertRaises(GitHubAPIError):
            self.client.create_branch(
                self.installation_token,
                self.repository,
                branch_name="api-analyzer/setup/test",
                from_sha="a" * 40,
                from_branch="main",
            )

    def test_create_branch_rejects_missing_source(self):
        with self.assertRaises(GitHubAPIError):
            self.client.create_branch(
                self.installation_token,
                self.repository,
                branch_name="api-analyzer/setup/test",
            )

    def test_create_new_repository_file(self):
        self.client.get_repository_file = Mock(
            side_effect=GitHubAPIError(
                "File not found.",
                status_code=404,
            )
        )

        self.client._request = Mock(
            return_value={
                "content": {
                    "name": ".api-analyzer.yml",
                    "path": ".api-analyzer.yml",
                    "sha": "c" * 40,
                }
            }
        )

        content = "api_analyzer:\n  adapter: \"django-rest-framework\"\n"

        result = self.client.create_or_update_repository_file(
            self.installation_token,
            self.repository,
            path=".api-analyzer.yml",
            content=content,
            branch="api-analyzer/setup/test",
            commit_message="chore: configure API Analyzer",
        )

        self.assertEqual(
            result["content"]["path"],
            ".api-analyzer.yml",
        )

        encoded_content = (
            self.client._request.call_args.kwargs["json"]["content"]
        )

        import base64

        self.assertEqual(
            base64.b64decode(
                encoded_content
            ).decode("utf-8"),
            content,
        )

        payload = self.client._request.call_args.kwargs["json"]

        self.assertNotIn(
            "sha",
            payload,
        )

        self.client._request.assert_called_once_with(
            "PUT",
            (
                "/repos/example/api-project/"
                "contents/.api-analyzer.yml"
            ),
            authorization=(
                f"Bearer {self.installation_token}"
            ),
            json=payload,
        )

    def test_update_existing_repository_file(self):
        self.client.get_repository_file = Mock(
            return_value={
                "path": ".api-analyzer.yml",
                "sha": "d" * 40,
                "content": "old content",
            }
        )

        self.client._request = Mock(
            return_value={
                "content": {
                    "path": ".api-analyzer.yml",
                    "sha": "e" * 40,
                }
            }
        )

        content = "new configuration\n"

        self.client.create_or_update_repository_file(
            self.installation_token,
            self.repository,
            path=".api-analyzer.yml",
            content=content,
            branch="api-analyzer/setup/test",
            commit_message="chore: update API Analyzer config",
        )

        payload = self.client._request.call_args.kwargs["json"]

        self.assertEqual(
            payload["sha"],
            "d" * 40,
        )

        self.assertEqual(
            payload["message"],
            "chore: update API Analyzer config",
        )

        self.assertEqual(
            payload["branch"],
            "api-analyzer/setup/test",
        )

    def test_repository_file_propagates_non_404_error(self):
        self.client.get_repository_file = Mock(
            side_effect=GitHubAPIError(
                "Permission denied.",
                status_code=403,
            )
        )

        with self.assertRaises(GitHubAPIError):
            self.client.create_or_update_repository_file(
                self.installation_token,
                self.repository,
                path=".api-analyzer.yml",
                content="test\n",
                branch="api-analyzer/setup/test",
                commit_message="test",
            )

    def test_create_pull_request(self):
        self.client._request = Mock(
            return_value={
                "number": 42,
                "html_url": (
                    "https://github.com/"
                    "example/api-project/pull/42"
                ),
                "state": "open",
            }
        )

        result = self.client.create_pull_request(
            self.installation_token,
            self.repository,
            title="chore: configure API compatibility",
            head="api-analyzer/setup/test",
            base="main",
            body="Automatic API Analyzer setup.",
        )

        self.assertEqual(
            result["number"],
            42,
        )

        self.client._request.assert_called_once_with(
            "POST",
            "/repos/example/api-project/pulls",
            authorization=(
                f"Bearer {self.installation_token}"
            ),
            json={
                "title": (
                    "chore: configure API compatibility"
                ),
                "head": "api-analyzer/setup/test",
                "base": "main",
                "body": (
                    "Automatic API Analyzer setup."
                ),
            },
        )

    def test_create_pull_request_requires_title(self):
        with self.assertRaises(GitHubAPIError):
            self.client.create_pull_request(
                self.installation_token,
                self.repository,
                title="",
                head="api-analyzer/setup/test",
                base="main",
            )

    def test_create_pull_request_requires_head(self):
        with self.assertRaises(GitHubAPIError):
            self.client.create_pull_request(
                self.installation_token,
                self.repository,
                title="Test",
                head="",
                base="main",
            )

    def test_create_pull_request_requires_base(self):
        with self.assertRaises(GitHubAPIError):
            self.client.create_pull_request(
                self.installation_token,
                self.repository,
                title="Test",
                head="api-analyzer/setup/test",
                base="",
            )


if __name__ == "__main__":
    import unittest

    unittest.main()