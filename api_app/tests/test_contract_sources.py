from unittest import TestCase

from api_app.contract_sources.committed import CommittedContractSource
from api_app.contract_sources.registry import ContractSourceRegistry
from api_app.contract_sources.drf import DjangoRESTFrameworkContractSource

class CommittedContractSourceTests(TestCase):
    def setUp(self):
        self.source = CommittedContractSource()

    def test_source_metadata(self):
        self.assertEqual(
            self.source.source_type,
            "committed",
        )
        self.assertEqual(
            self.source.display_name,
            "Committed OpenAPI document",
        )

    def test_can_resolve_default_openapi_json(self):
        repository = {
            "tree_paths": [
                "openapi.json",
            ],
            "file_contents": {},
        }

        self.assertTrue(
            self.source.can_resolve(repository)
        )

    def test_can_resolve_explicit_spec_path(self):
        repository = {
            "tree_paths": [
                "docs/api-contract.json",
            ],
            "file_contents": {},
        }

        self.assertTrue(
            self.source.can_resolve(
                repository,
                spec_path="docs/api-contract.json",
            )
        )

    def test_cannot_resolve_missing_contract(self):
        repository = {
            "tree_paths": [
                "README.md",
            ],
            "file_contents": {},
        }

        self.assertFalse(
            self.source.can_resolve(repository)
        )

    def test_resolve_valid_json_contract(self):
        repository = {
            "tree_paths": [
                "openapi.json",
            ],
            "file_contents": {
                "openapi.json": """
                {
                    "openapi": "3.1.0",
                    "info": {
                        "title": "Example API",
                        "version": "1.0.0"
                    },
                    "paths": {}
                }
                """,
            },
        }

        result = self.source.resolve(
            repository,
            commit_sha="a" * 40,
        )

        self.assertTrue(result.success)
        self.assertEqual(
            result.source_type,
            "committed",
        )
        self.assertEqual(
            result.commit_sha,
            "a" * 40,
        )
        self.assertEqual(
            result.source_path,
            "openapi.json",
        )
        self.assertIsNotNone(result.contract)
        self.assertEqual(
            result.contract["openapi"],
            "3.1.0",
        )
        self.assertEqual(
            result.metadata["format"],
            "json",
        )
        self.assertTrue(
            result.metadata["revision_pinned"]
        )

    def test_resolve_invalid_json_returns_error(self):
        repository = {
            "tree_paths": [
                "openapi.json",
            ],
            "file_contents": {
                "openapi.json": "{ invalid json",
            },
        }

        result = self.source.resolve(
            repository,
            commit_sha="a" * 40,
        )

        self.assertFalse(result.success)
        self.assertIsNone(result.contract)
        self.assertTrue(result.errors)

    def test_resolve_invalid_openapi_structure_returns_error(self):
        repository = {
            "tree_paths": [
                "openapi.json",
            ],
            "file_contents": {
                "openapi.json": """
                {
                    "info": {
                        "title": "Example API",
                        "version": "1.0.0"
                    },
                    "paths": {}
                }
                """,
            },
        }

        result = self.source.resolve(
            repository,
            commit_sha="a" * 40,
        )

        self.assertFalse(result.success)
        self.assertIsNone(result.contract)
        self.assertTrue(
            any(
                "openapi" in error.lower()
                or "swagger" in error.lower()
                for error in result.errors
            )
        )

    def test_resolve_missing_contract_returns_error(self):
        repository = {
            "tree_paths": [
                "README.md",
            ],
            "file_contents": {},
        }

        result = self.source.resolve(
            repository,
            commit_sha="a" * 40,
        )

        self.assertFalse(result.success)
        self.assertIsNone(result.contract)
        self.assertTrue(result.errors)

    def test_resolve_missing_file_content_returns_error(self):
        repository = {
            "tree_paths": [
                "openapi.json",
            ],
            "file_contents": {},
        }

        result = self.source.resolve(
            repository,
            commit_sha="a" * 40,
        )

        self.assertFalse(result.success)
        self.assertIsNone(result.contract)
        self.assertTrue(
            any(
                "content" in error.lower()
                for error in result.errors
            )
        )

    def test_resolve_requires_commit_sha(self):
        repository = {
            "tree_paths": [
                "openapi.json",
            ],
            "file_contents": {
                "openapi.json": """
                {
                    "openapi": "3.1.0",
                    "info": {
                        "title": "Example API",
                        "version": "1.0.0"
                    },
                    "paths": {}
                }
                """,
            },
        }

        result = self.source.resolve(
            repository,
            commit_sha="",
        )

        self.assertFalse(result.success)
        self.assertIsNone(result.contract)
        self.assertTrue(
            any(
                "commit_sha" in error
                for error in result.errors
            )
        )


class ContractSourceRegistryTests(TestCase):
    def test_default_registry_contains_committed_source(self):
        registry = ContractSourceRegistry.with_defaults()

        self.assertTrue(
            registry.has("committed")
        )

    def test_registry_resolves_committed_source(self):
        registry = ContractSourceRegistry.with_defaults()

        repository = {
            "tree_paths": [
                "openapi.json",
            ],
            "file_contents": {
                "openapi.json": """
                {
                    "openapi": "3.1.0",
                    "info": {
                        "title": "Example API",
                        "version": "1.0.0"
                    },
                    "paths": {}
                }
                """,
            },
        }

        result = registry.resolve(repository)

        self.assertTrue(result.supported)
        self.assertIsNotNone(result.source)
        self.assertEqual(
            result.source.source_type,
            "committed",
        )

    def test_registry_returns_unsupported_when_no_source_matches(self):
        registry = ContractSourceRegistry.with_defaults()

        repository = {
            "tree_paths": [
                "README.md",
            ],
            "file_contents": {},
        }

        result = registry.resolve(repository)

        self.assertFalse(result.supported)
        self.assertIsNone(result.source)
        self.assertTrue(result.reason)


class DjangoRESTFrameworkContractSourceTests(TestCase):
    def setUp(self):
        self.source = DjangoRESTFrameworkContractSource()

        self.repository = {
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

        self.contract = {
            "openapi": "3.1.0",
            "info": {
                "title": "Example API",
                "version": "1.0.0",
            },
            "paths": {},
        }

    def test_source_metadata(self):
        self.assertEqual(
            self.source.source_type,
            "django-rest-framework",
        )
        self.assertEqual(
            self.source.display_name,
            "Django REST Framework generated contract",
        )

    def test_can_resolve_with_generated_contract(self):
        repository = {
            **self.repository,
            "generated_contract": self.contract,
        }

        self.assertTrue(
            self.source.can_resolve(repository)
        )

    def test_cannot_resolve_non_drf_repository(self):
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
            "generated_contract": self.contract,
        }

        self.assertFalse(
            self.source.can_resolve(repository)
        )

    def test_resolve_generated_contract(self):
        repository = {
            **self.repository,
            "generated_contract": self.contract,
        }

        result = self.source.resolve(
            repository,
            commit_sha="a" * 40,
        )

        self.assertTrue(result.success)
        self.assertEqual(
            result.source_type,
            "django-rest-framework",
        )
        self.assertEqual(
            result.commit_sha,
            "a" * 40,
        )
        self.assertEqual(
            result.source_path,
            "openapi.json",
        )
        self.assertIsNotNone(result.contract)
        self.assertEqual(
            result.contract["openapi"],
            "3.1.0",
        )
        self.assertEqual(
            result.metadata["adapter_type"],
            "django-rest-framework",
        )
        self.assertTrue(
            result.metadata["revision_pinned"]
        )

    def test_resolve_rejects_invalid_generated_contract(self):
        repository = {
            **self.repository,
            "generated_contract": {
                "info": {
                    "title": "Example API",
                    "version": "1.0.0",
                },
                "paths": {},
            },
        }

        result = self.source.resolve(
            repository,
            commit_sha="a" * 40,
        )

        self.assertFalse(result.success)
        self.assertIsNone(result.contract)
        self.assertTrue(result.errors)

    def test_resolve_rejects_missing_generated_contract(self):
        repository = {
            **self.repository,
        }

        result = self.source.resolve(
            repository,
            commit_sha="a" * 40,
        )

        self.assertFalse(result.success)
        self.assertIsNone(result.contract)
        self.assertTrue(result.errors)

    def test_resolve_rejects_commit_mismatch(self):
        repository = {
            **self.repository,
            "generated_contract": self.contract,
            "generated_contract_commit_sha": "b" * 40,
        }

        result = self.source.resolve(
            repository,
            commit_sha="a" * 40,
        )

        self.assertFalse(result.success)
        self.assertIsNone(result.contract)
        self.assertTrue(
            any(
                "commit SHA" in error
                for error in result.errors
            )
        )

    def test_resolve_accepts_matching_commit_sha(self):
        commit_sha = "a" * 40

        repository = {
            **self.repository,
            "generated_contract": self.contract,
            "generated_contract_commit_sha": commit_sha,
        }

        result = self.source.resolve(
            repository,
            commit_sha=commit_sha,
        )

        self.assertTrue(result.success)
        self.assertEqual(
            result.commit_sha,
            commit_sha,
        )
        self.assertEqual(
            result.errors,
            (),
        )
        
if __name__ == "__main__":
    import unittest

    unittest.main()