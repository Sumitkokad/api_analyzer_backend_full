import time
from pathlib import Path

import yaml
from django.test import TestCase
from rest_framework.test import APIClient


class AuthAndOwnershipTests(TestCase):
    def register(self, username):
        client = APIClient()
        response = client.post(
            "/api/auth/register/",
            {"username": username, "password": "StrongPass123!"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        client.credentials(HTTP_AUTHORIZATION=f"Token {response.data['token']}")
        return client

    def test_authenticated_comparison_and_cross_user_denial(self):
        suffix = str(int(time.time()))
        owner = self.register(f"owner_{suffix}")
        other = self.register(f"other_{suffix}")

        response = owner.post("/api/projects/", {"name": f"Private {suffix}"}, format="json")
        self.assertEqual(response.status_code, 201)
        project_id = response.data["id"]

        old_spec = yaml.safe_load(Path("project/data/old_api.yaml").read_text())
        new_spec = yaml.safe_load(Path("project/data/new_api.yaml").read_text())

        response = owner.post(
            "/api/specifications/",
            {"project": project_id, "name": "old", "version": "1", "content": old_spec},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        old_id = response.data["id"]

        response = owner.post(
            "/api/specifications/",
            {"project": project_id, "name": "new", "version": "2", "content": new_spec},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        new_id = response.data["id"]

        response = owner.post(
            "/api/comparisons/",
            {
                "project": project_id,
                "old_specification": old_id,
                "new_specification": new_id,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["summary"]["total"], 7)

        response = other.get(f"/api/projects/{project_id}/")
        self.assertEqual(response.status_code, 404)

        anonymous = APIClient()
        response = anonymous.get("/api/projects/")
        self.assertEqual(response.status_code, 401)
