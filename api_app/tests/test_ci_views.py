import hashlib

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory

from api_app.ci_views import CIAnalyzeView
from api_app.models import Project, ProjectToken


class CIAnalyzeViewTests(TestCase):

    def setUp(self):
        User = get_user_model()

        self.user = User.objects.create_user(
            username="ci-view-user",
            password="testpass123",
        )

        self.project = Project.objects.create(
            owner=self.user,
            name="CI View Project",
        )

        self.raw_token = "ci_test_token_12345"

        ProjectToken.objects.create(
            project=self.project,
            name="Test CI Token",
            token_hash=hashlib.sha256(
                self.raw_token.encode("utf-8")
            ).hexdigest(),
        )

        self.factory = APIRequestFactory()
        self.view = CIAnalyzeView.as_view()

        self.payload = {
            "project_id": str(self.project.pk),
            "idempotency_key": "repo:pr:abc123:def456",
            "repository": "company/test-api",
            "pull_request": 42,
            "base_sha": "abc123",
            "head_sha": "def456",
            "base_spec": {
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
            },
            "head_spec": {
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
            },
            "registered_routes": [
                "GET /users",
            ],
            "generator_warnings": [],
            "adapter": {
                "name": "drf-spectacular",
                "version": "1.0",
            },
        }

    def _request(self, payload=None, token=None):
        headers = {}

        if token:
            headers["HTTP_AUTHORIZATION"] = (
                f"Bearer {token}"
            )

        request = self.factory.post(
            "/api/ci/analyze",
            payload or self.payload,
            format="json",
            **headers,
        )

        request.META["CONTENT_LENGTH"] = str(
            len(
                str(payload or self.payload).encode("utf-8")
            )
        )

        return request

    def test_valid_ci_request_returns_202(self):
        request = self._request(
            token=self.raw_token
        )

        response = self.view(request)

        self.assertEqual(
            response.status_code,
            202,
        )

        self.assertEqual(
            response.data["status"],
            "queued",
        )

        self.assertIsNotNone(
            response.data["comparison_id"]
        )

        self.assertIsNotNone(
            response.data["job_id"]
        )

    def test_invalid_token_returns_401(self):
        request = self._request(
            token="invalid-token"
        )

        response = self.view(request)

        self.assertEqual(
            response.status_code,
            401,
        )

    def test_missing_token_returns_401(self):
        request = self._request()

        response = self.view(request)

        self.assertEqual(
            response.status_code,
            401,
        )

    def test_wrong_project_returns_403(self):
        payload = dict(self.payload)

        payload["project_id"] = "wrong-project"

        request = self._request(
            payload=payload,
            token=self.raw_token,
        )

        response = self.view(request)

        self.assertEqual(
            response.status_code,
            403,
        )

    def test_invalid_payload_returns_400(self):
        payload = dict(self.payload)

        del payload["base_sha"]

        request = self._request(
            payload=payload,
            token=self.raw_token,
        )

        response = self.view(request)

        self.assertEqual(
            response.status_code,
            400,
        )

    def test_same_request_is_idempotent(self):
        first_request = self._request(
            token=self.raw_token
        )

        first_response = self.view(
            first_request
        )

        second_request = self._request(
            token=self.raw_token
        )

        second_response = self.view(
            second_request
        )

        self.assertEqual(
            first_response.status_code,
            202,
        )

        self.assertEqual(
            second_response.status_code,
            202,
        )

        self.assertEqual(
            first_response.data["comparison_id"],
            second_response.data["comparison_id"],
        )

        self.assertEqual(
            first_response.data["job_id"],
            second_response.data["job_id"],
        )

        self.assertTrue(
            first_response.data["created"]
        )

        self.assertFalse(
            second_response.data["created"]
        )

    def test_revoked_token_returns_401(self):
        token = ProjectToken.objects.get(
            project=self.project
        )

        from django.utils import timezone

        token.revoked_at = timezone.now()
        token.save(
            update_fields=["revoked_at"]
        )

        request = self._request(
            token=self.raw_token
        )

        response = self.view(request)

        self.assertEqual(
            response.status_code,
            401,
        )