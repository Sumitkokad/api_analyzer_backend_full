from unittest import TestCase

from api_app.adapters.drf import DjangoRESTFrameworkAdapter
from api_app.setup_service import RepositorySetupService


class RepositorySetupServiceTests(TestCase):
    def setUp(self):
        self.service = RepositorySetupService()
        self.adapter = DjangoRESTFrameworkAdapter()

        self.repository = {
            "repository_full_name": "example/api-project",
            "default_branch": "main",
            "tree_paths": [
                "manage.py",
                "requirements.txt",
            ],
            "manifest_contents": {
                "requirements.txt": (
                    "Django==6.0\n"
                    "djangorestframework==3.17.0\n"
                    "drf-spectacular==0.28.0\n"
                ),
            },
            "source_files": [],
        }

    def test_builds_setup_plan(self):
        plan = self.service.build_plan(
            repository=self.repository,
            adapter=self.adapter,
        )

        self.assertEqual(
            plan.repository,
            "example/api-project",
        )

        self.assertEqual(
            plan.base_branch,
            "main",
        )

        self.assertEqual(
            plan.adapter_type,
            "django-rest-framework",
        )

        self.assertEqual(
            plan.framework_name,
            "Django REST Framework",
        )

        self.assertEqual(
            plan.spec_path,
            "openapi.json",
        )

    def test_setup_plan_contains_required_files(self):
        plan = self.service.build_plan(
            repository=self.repository,
            adapter=self.adapter,
        )

        paths = {
            setup_file.path
            for setup_file in plan.files
        }

        self.assertIn(
            ".api-analyzer.yml",
            paths,
        )

        self.assertIn(
            ".github/workflows/api-compatibility.yml",
            paths,
        )

    def test_setup_plan_contains_generation_command(self):
        plan = self.service.build_plan(
            repository=self.repository,
            adapter=self.adapter,
        )

        self.assertIn(
            "manage.py",
            plan.generation_command,
        )

        self.assertIn(
            "spectacular",
            plan.generation_command,
        )

    def test_setup_branch_is_deterministic(self):
        plan = self.service.build_plan(
            repository=self.repository,
            adapter=self.adapter,
        )

        self.assertEqual(
            plan.branch_name,
            "api-analyzer/setup/django-rest-framework",
        )

    def test_setup_requires_review(self):
        plan = self.service.build_plan(
            repository=self.repository,
            adapter=self.adapter,
        )

        self.assertTrue(
            plan.metadata["review_required"]
        )

        self.assertTrue(
            plan.metadata["framework_agnostic"]
        )

    def test_custom_spec_path_is_used(self):
        plan = self.service.build_plan(
            repository=self.repository,
            adapter=self.adapter,
            spec_path="docs/api/openapi.json",
        )

        self.assertEqual(
            plan.spec_path,
            "docs/api/openapi.json",
        )

        config = next(
            item.content
            for item in plan.files
            if item.path == ".api-analyzer.yml"
        )

        self.assertIn(
            "docs/api/openapi.json",
            config,
        )

    def test_custom_default_branch_is_used(self):
        plan = self.service.build_plan(
            repository=self.repository,
            adapter=self.adapter,
            base_branch="develop",
        )

        self.assertEqual(
            plan.base_branch,
            "develop",
        )

    def test_repository_name_is_required(self):
        repository = {
            **self.repository,
        }

        repository.pop(
            "repository_full_name"
        )

        with self.assertRaises(ValueError):
            self.service.build_plan(
                repository=repository,
                adapter=self.adapter,
            )

    def test_contract_path_cannot_escape_repository(self):
        with self.assertRaises(ValueError):
            self.service.build_plan(
                repository=self.repository,
                adapter=self.adapter,
                spec_path="../openapi.json",
            )

    def test_pull_request_content_is_reviewable(self):
        plan = self.service.build_plan(
            repository=self.repository,
            adapter=self.adapter,
        )

        self.assertIn(
            "API Analyzer automatic setup",
            plan.pull_request_body,
        )

        self.assertIn(
            "example/api-project",
            plan.pull_request_body,
        )

        self.assertIn(
            "Django REST Framework",
            plan.pull_request_body,
        )


if __name__ == "__main__":
    import unittest

    unittest.main()