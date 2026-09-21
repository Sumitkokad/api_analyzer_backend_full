import hashlib

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from api_app.models import Project, ProjectToken


class CIAnalyzeURLTests(TestCase):

    def setUp(self):
        User = get_user_model()

        self.user = User.objects.create_user(
            username="ci-url-user",
            password="testpass123",
        )

        self.project = Project.objects.create(
            owner=self.user,
            name="CI URL Project",
        )

        self.raw_token = "ci_url_token_12345"

        ProjectToken.objects.create(
            project=self.project,
            name="URL Test Token",
            token_hash=hashlib.sha256(
                self.raw_token.encode("utf-8")
            ).hexdigest(),
        )

        self.client = APIClient()

        self.payload = {
            "project_id": str(self.project.pk),
            "idempotency_key": "repo:pr:url-test:base",
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
                    "paths": {},
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
                    "paths": {},
                },
            },
            "registered_routes": [],
            "generator_warnings": [],
            "adapter": {
                "name": "drf-spectacular",
                "version": "1.0",
            },
        }

    def test_ci_route_is_registered(self):
        url = reverse("ci-analyze")

        self.assertTrue(
            url.endswith("/ci/analyze")
        )

    def test_ci_endpoint_returns_200(self):
        self.client.credentials(
            HTTP_AUTHORIZATION=(
                f"Bearer {self.raw_token}"
            )
        )

        url = reverse("ci-analyze")

        response = self.client.post(
            url,
            self.payload,
            format="json",
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.data["status"],
            "completed",
        )

        self.assertIsNotNone(
            response.data["comparison_id"]
        )

        self.assertIsNotNone(
            response.data["job_id"]
        )