from unittest import TestCase

from api_app.adapters.drf import DjangoRESTFrameworkAdapter
from api_app.adapters.registry import AdapterRegistry


class DjangoRESTFrameworkAdapterTests(TestCase):
    def setUp(self):
        self.adapter = DjangoRESTFrameworkAdapter()

    def test_adapter_metadata(self):
        self.assertEqual(
            self.adapter.adapter_type,
            "django-rest-framework",
        )
        self.assertEqual(
            self.adapter.framework_name,
            "Django REST Framework",
        )
        self.assertEqual(
            self.adapter.language,
            "Python",
        )

    def test_detects_django_rest_framework_repository(self):
        repository = {
            "tree_paths": [
                "manage.py",
                "requirements.txt",
            ],
            "manifest_contents": {
                "requirements.txt": (
                    "Django==6.0\n"
                    "djangorestframework==3.17.0\n"
                ),
            },
            "source_files": [],
        }

        result = self.adapter.detect(repository)

        self.assertTrue(result.detected)
        self.assertEqual(
            result.adapter_type,
            "django-rest-framework",
        )

    def test_does_not_detect_plain_express_repository(self):
        repository = {
            "tree_paths": [
                "package.json",
            ],
            "manifest_contents": {
                "package.json": (
                    '{"dependencies":{"express":"5.0.0"}}'
                ),
            },
            "source_files": [],
        }

        result = self.adapter.detect(repository)

        self.assertFalse(result.detected)

    def test_can_generate_contract_when_manage_py_exists(self):
        repository = {
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

        self.assertTrue(
            self.adapter.can_generate_contract(repository)
        )

    def test_generate_contract_returns_plan(self):
        repository = {
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
            "commit_sha": "a" * 40,
        }

        plan = self.adapter.generate_contract(
    repository,
    commit_sha="a" * 40,
)

        self.assertEqual(
            plan.adapter_type,
            "django-rest-framework",
        )

        self.assertEqual(
            plan.output_path,
            "openapi.json",
        )

        self.assertIn(
            "manage.py",
            plan.command,
        )

        self.assertIn(
            "spectacular",
            plan.command,
        )

    def test_validate_contract_accepts_valid_openapi(self):
        contract = {
            "openapi": "3.1.0",
            "info": {
                "title": "Example API",
                "version": "1.0.0",
            },
            "paths": {},
        }

        result = self.adapter.validate_contract(contract)

        self.assertTrue(result.valid)
        self.assertEqual(result.errors, ())

    def test_validate_contract_rejects_missing_openapi_version(self):
        contract = {
            "info": {
                "title": "Example API",
                "version": "1.0.0",
            },
            "paths": {},
        }

        result = self.adapter.validate_contract(contract)

        self.assertFalse(result.valid)
        self.assertTrue(result.errors)

        self.assertTrue(
            any(
                "OpenAPI" in error
                for error in result.errors
            )
        )


class AdapterRegistryTests(TestCase):
    def test_default_registry_contains_drf(self):
        registry = AdapterRegistry.with_defaults()

        self.assertTrue(
            registry.has("django-rest-framework")
        )

    def test_registry_detects_drf(self):
        registry = AdapterRegistry.with_defaults()

        repository = {
            "tree_paths": [
                "manage.py",
                "requirements.txt",
            ],
            "manifest_contents": {
                "requirements.txt": (
                    "Django==6.0\n"
                    "djangorestframework==3.17.0\n"
                ),
            },
            "source_files": [],
        }

        result = registry.detect(repository)

        self.assertTrue(result.supported)
        self.assertIsNotNone(result.adapter)
        self.assertEqual(
            result.adapter.adapter_type,
            "django-rest-framework",
        )

    def test_registry_returns_unsupported_for_unknown_repository(self):
        registry = AdapterRegistry.with_defaults()

        repository = {
            "tree_paths": [
                "README.md",
            ],
            "manifest_contents": {},
            "source_files": [],
        }

        result = registry.detect(repository)

        self.assertFalse(result.supported)
        self.assertIsNone(result.adapter)


if __name__ == "__main__":
    import unittest

    unittest.main()