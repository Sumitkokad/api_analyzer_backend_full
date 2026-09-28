from types import SimpleNamespace

from django.test import SimpleTestCase

from api_app.adapters.registry import AdapterRegistry
from api_app.github_scan_service import (
    ContractDetection,
    FrameworkDetection,
    RepositoryScanResult,
)
from api_app.setup_views import GitHubRepositorySetupView


class RepositoryMetadataTests(SimpleTestCase):
    def _scan_result(self):
        return RepositoryScanResult(
            repository="example/api",
            default_branch="main",
            contract=ContractDetection(
                found=False,
            ),
            framework=FrameworkDetection(
                detected=True,
                name="Django REST Framework",
                language="Python",
                adapter_type="django-rest-framework",
                confidence="high",
                evidence=(
                    "Django REST Framework dependency detected.",
                ),
            ),
            scanned_files=3,
            tree_paths=(
                "manage.py",
                "requirements.txt",
                "api/views.py",
            ),
            manifest_contents={
                "requirements.txt": (
                    "Django==5.2\n"
                    "djangorestframework==3.16.0\n"
                )
            },
        )

    def test_repository_metadata_preserves_scanner_evidence(self):
        scan_result = self._scan_result()

        project = SimpleNamespace(
            default_branch="main",
        )

        metadata = GitHubRepositorySetupView._repository_metadata(
            scan_result=scan_result,
            repository_full_name="example/api",
            project=project,
        )

        self.assertEqual(
            metadata["repository_full_name"],
            "example/api",
        )

        self.assertEqual(
            metadata["default_branch"],
            "main",
        )

        self.assertEqual(
            metadata["tree_paths"],
            [
                "manage.py",
                "requirements.txt",
                "api/views.py",
            ],
        )

        self.assertEqual(
            metadata["manifest_contents"]["requirements.txt"],
            (
                "Django==5.2\n"
                "djangorestframework==3.16.0\n"
            ),
        )

        self.assertEqual(
            metadata["detected_adapter_type"],
            "django-rest-framework",
        )

    def test_repository_metadata_allows_adapter_detection(self):
        scan_result = self._scan_result()

        project = SimpleNamespace(
            default_branch="main",
        )

        metadata = GitHubRepositorySetupView._repository_metadata(
            scan_result=scan_result,
            repository_full_name="example/api",
            project=project,
        )

        registry = AdapterRegistry.with_defaults()

        resolution = registry.detect(metadata)

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

    def test_sensitive_scanner_evidence_is_not_exposed_by_scan_dict(self):
        scan_result = self._scan_result()

        public_data = scan_result.as_dict()

        self.assertNotIn(
            "tree_paths",
            public_data,
        )

        self.assertNotIn(
            "manifest_contents",
            public_data,
        )