from django.contrib.auth import get_user_model
from django.test import TestCase

from api_app.ci_service import (
    CIValidationError,
    build_ci_response,
    create_ci_submission,
)
from api_app.models import Project


class CIServiceTests(TestCase):

    def setUp(self):
        User = get_user_model()

        self.user = User.objects.create_user(
            username="ci-test-user",
            password="testpass123",
        )

        self.project = Project.objects.create(
            owner=self.user,
            name="CI Test Project",
        )

        self.base_spec = {
            "path": "openapi.json",
            "source_type": "generated",
            "content": {
                "openapi": "3.0.3",
                "info": {
                    "title": "Test API",
                    "version": "1.0.0",
                },
                "paths": {
                    "/users": {
                        "get": {
                            "responses": {
                                "200": {
                                    "description": "OK"
                                }
                            }
                        }
                    }
                },
            },
        }

        self.head_spec = {
            "path": "openapi.json",
            "source_type": "generated",
            "content": {
                "openapi": "3.0.3",
                "info": {
                    "title": "Test API",
                    "version": "1.1.0",
                },
                "paths": {
                    "/users": {
                        "get": {
                            "responses": {
                                "200": {
                                    "description": "OK"
                                }
                            }
                        }
                    },
                    "/users/{id}": {
                        "get": {
                            "responses": {
                                "200": {
                                    "description": "OK"
                                }
                            }
                        }
                    },
                },
            },
        }

    def _payload(
        self,
        idempotency_key="repo:pr:abc123:def456",
    ):
        return {
            "project_id": str(self.project.pk),
            "idempotency_key": idempotency_key,
            "repository": "company/test-api",
            "pull_request": 42,
            "base_sha": "abc123",
            "head_sha": "def456",
            "base_spec": self.base_spec,
            "head_spec": self.head_spec,
            "registered_routes": [
                "GET /users",
            ],
            "generator_warnings": [],
            "adapter": {
                "name": "drf-spectacular",
                "version": "1.0",
            },
        }

    def test_creates_ci_submission(self):
        result = create_ci_submission(
            self.project,
            self._payload(),
        )

        self.assertTrue(result["created"])
        self.assertIsNotNone(result["comparison"])
        self.assertIsNotNone(result["job"])

        self.assertEqual(
            result["comparison"].status,
            "queued",
        )

        self.assertEqual(
            result["job"].status,
            "queued",
        )

    def test_same_idempotency_key_returns_existing_submission(self):
        payload = self._payload()

        first = create_ci_submission(
            self.project,
            payload,
        )

        second = create_ci_submission(
            self.project,
            payload,
        )

        self.assertTrue(first["created"])
        self.assertFalse(second["created"])

        self.assertEqual(
            first["comparison"].pk,
            second["comparison"].pk,
        )

        self.assertEqual(
            first["job"].pk,
            second["job"].pk,
        )

    def test_invalid_payload_is_rejected(self):
        payload = self._payload()

        del payload["base_sha"]

        with self.assertRaises(CIValidationError):
            create_ci_submission(
                self.project,
                payload,
            )

    def test_wrong_project_id_is_rejected(self):
        payload = self._payload()
        payload["project_id"] = "different-project-id"

        with self.assertRaises(CIValidationError):
            create_ci_submission(
                self.project,
                payload,
            )

    def test_build_ci_response(self):
        result = create_ci_submission(
            self.project,
            self._payload(),
        )

        response = build_ci_response(result)

        self.assertEqual(
            response["comparison_id"],
            result["comparison"].pk,
        )

        self.assertEqual(
            response["job_id"],
            result["job"].pk,
        )

        self.assertEqual(
            response["status"],
            "queued",
        )

        self.assertIsNone(
            response["gate_status"],
        )

        self.assertTrue(
            response["report_url"].startswith(
                "/api/comparisons/"
            )
        )