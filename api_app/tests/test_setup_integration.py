from django.test import SimpleTestCase

from api_app.adapters.registry import AdapterRegistry
from api_app.github_scan_service import GitHubRepositoryScanner
from api_app.github_write_service import GitHubWriteService
from api_app.setup_service import RepositorySetupService


class FakeGitHubClient:
    def __init__(self):
        self.created_branches = []
        self.updated_files = []
        self.pull_requests = []

    def get_repository(
        self,
        installation_token,
        repository_full_name,
    ):
        return {
            "full_name": repository_full_name,
            "default_branch": "main",
        }

    def get_repository_tree(
        self,
        installation_token,
        repository_full_name,
        tree_ref,
        recursive=True,
    ):
        return {
            "truncated": False,
            "tree": [
                {
                    "path": "manage.py",
                    "type": "blob",
                },
                {
                    "path": "requirements.txt",
                    "type": "blob",
                },
                {
                    "path": "api/views.py",
                    "type": "blob",
                },
                {
                    "path": "api/urls.py",
                    "type": "blob",
                },
            ],
        }

    def get_repository_file(
        self,
        installation_token,
        repository_full_name,
        path,
        ref,
    ):
        files = {
            "requirements.txt": (
                "Django==5.2\n"
                "djangorestframework==3.16.0\n"
                "drf-spectacular==0.28.0\n"
            ),
        }

        return {
            "content": files.get(path, ""),
        }

    def create_installation_token(
        self,
        installation_id,
    ):
        return {
            "token": "fake-installation-token",
        }

    def create_branch(
        self,
        installation_token,
        repository_full_name,
        branch_name,
        from_sha=None,
        from_branch=None,
    ):
        self.created_branches.append(
            {
                "repository": repository_full_name,
                "branch_name": branch_name,
                "from_sha": from_sha,
                "from_branch": from_branch,
            }
        )

        return {
            "ref": f"refs/heads/{branch_name}",
            "sha": (
                from_sha
                or "a" * 40
            ),
        }

    def create_or_update_repository_file(
        self,
        installation_token,
        repository_full_name,
        path,
        content,
        branch,
        commit_message,
    ):
        self.updated_files.append(
            {
                "repository": repository_full_name,
                "path": path,
                "content": content,
                "branch": branch,
                "commit_message": commit_message,
            }
        )

        return {
            "content": {
                "path": path,
            }
        }

    def create_pull_request(
        self,
        installation_token,
        repository_full_name,
        title,
        head,
        base,
        body,
    ):
        self.pull_requests.append(
            {
                "repository": repository_full_name,
                "title": title,
                "head": head,
                "base": base,
                "body": body,
            }
        )

        return {
            "number": 123,
            "html_url": (
                "https://github.com/example/api/"
                "pull/123"
            ),
            "state": "open",
        }


class SetupIntegrationTests(SimpleTestCase):

    def test_drf_repository_setup_pipeline(self):
        repository_name = "example/api"
        installation_token = "fake-installation-token"

        github_client = FakeGitHubClient()

        # 1. Scan repository
        scanner = GitHubRepositoryScanner(
            github_client=github_client,
            installation_token=installation_token,
        )

        scan_result = scanner.scan(
            repository_name,
            default_branch="main",
        )

        self.assertTrue(
            scan_result.framework.detected
        )

        self.assertEqual(
            scan_result.framework.adapter_type,
            "django-rest-framework",
        )

        self.assertIn(
            "manage.py",
            scan_result.tree_paths,
        )

        self.assertIn(
            "requirements.txt",
            scan_result.manifest_contents,
        )

        # 2. Convert scanner result to adapter metadata
        repository_metadata = {
            "repository_full_name": (
                scan_result.repository
            ),
            "default_branch": (
                scan_result.default_branch
            ),
            "tree_paths": list(
                scan_result.tree_paths
            ),
            "manifest_contents": dict(
                scan_result.manifest_contents
            ),
            "source_files": [],
            "spec_path": "openapi.json",
            "commit_sha": "a" * 40,
            "warnings": list(
                scan_result.warnings
            ),
        }

        # 3. Detect adapter
        registry = (
            AdapterRegistry.with_defaults()
        )

        resolution = registry.detect(
            repository_metadata
        )

        self.assertTrue(
            resolution.supported
        )

        self.assertIsNotNone(
            resolution.adapter
        )

        self.assertEqual(
            resolution.adapter.adapter_type,
            "django-rest-framework",
        )

        # 4. Build setup plan
        setup_service = (
            RepositorySetupService()
        )

        setup_plan = setup_service.build_plan(
            repository=repository_metadata,
            adapter=resolution.adapter,
            base_branch="main",
            spec_path="openapi.json",
        )

        self.assertEqual(
            setup_plan.repository,
            repository_name,
        )

        self.assertEqual(
            setup_plan.base_branch,
            "main",
        )

        self.assertEqual(
            setup_plan.adapter_type,
            "django-rest-framework",
        )

        self.assertEqual(
            setup_plan.spec_path,
            "openapi.json",
        )

        self.assertTrue(
            setup_plan.generation_command
        )

        self.assertGreaterEqual(
            len(setup_plan.files),
            2,
        )

        # 5. Execute setup through GitHub writer
        write_service = GitHubWriteService(
            github_client=github_client
        )

        execution = write_service.execute_setup(
            installation_id=123,
            plan=setup_plan,
        )

        # 6. Verify final result
        self.assertTrue(
            execution.success
        )

        self.assertEqual(
            execution.branch_name,
            setup_plan.branch_name,
        )

        self.assertEqual(
            execution.base_branch,
            "main",
        )

        self.assertEqual(
            execution.pull_request_number,
            123,
        )

        self.assertEqual(
            execution.pull_request_url,
            "https://github.com/example/api/pull/123",
        )

        # One branch must have been created.
        self.assertEqual(
            len(github_client.created_branches),
            1,
        )

        # Every setup file must have been written.
        self.assertEqual(
            len(github_client.updated_files),
            len(setup_plan.files),
        )

        written_paths = {
            item["path"]
            for item in github_client.updated_files
        }

        planned_paths = {
            setup_file.path
            for setup_file in setup_plan.files
        }

        self.assertEqual(
            written_paths,
            planned_paths,
        )

        # Exactly one setup PR must be created.
        self.assertEqual(
            len(github_client.pull_requests),
            1,
        )

        pull_request = (
            github_client.pull_requests[0]
        )

        self.assertEqual(
            pull_request["repository"],
            repository_name,
        )

        self.assertEqual(
            pull_request["head"],
            setup_plan.branch_name,
        )

        self.assertEqual(
            pull_request["base"],
            "main",
        )