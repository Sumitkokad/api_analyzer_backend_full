from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from api_app.models import APISpecification, Comparison, Project
from api_app.services import run_comparison


def make_spec():
    return {
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
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "id": {
                                                "type": "integer"
                                            },
                                            "name": {
                                                "type": "string"
                                            },
                                        },
                                    }
                                }
                            },
                        }
                    }
                }
            }
        },
    }


class RunComparisonGateTests(TestCase):

    def setUp(self):
        User = get_user_model()

        self.user = User.objects.create_user(
            username="gate-test-user",
            password="test-password",
        )

        self.project = Project.objects.create(
            owner=self.user,
            name="Gate Test Project",
        )

    def _create_comparison(self):
        base_spec = APISpecification.objects.create(
            project=self.project,
            name="Base",
            version="1.0.0",
            content=make_spec(),
            raw_text="",
            content_hash="base-hash",
        )

        head_spec = APISpecification.objects.create(
            project=self.project,
            name="Head",
            version="1.0.1",
            content=make_spec(),
            raw_text="",
            content_hash="head-hash",
        )

        return Comparison.objects.create(
            project=self.project,
            old_specification=base_spec,
            new_specification=head_spec,
        )

    def _fake_change(self, change_type):
        return SimpleNamespace(
            change_id="test-change-001",
            stable_hash="test-stable-hash-001",
            rule_id="",
            change_type=change_type,
            category="contract",
            endpoint="/users",
            method="GET",
            direction="endpoint",
            location="",
            parameter="",
            schema_path="",
            relation="",
            flags=[],
            old_value={},
            new_value={},
            confidence=1.0,
            source={},
            compatibility="unknown",
            severity="",
        )

    def _fake_impact(self):
        return {
            "classification": "breaking",
            "severity": "high",
            "reason": "Test impact",
            "affected_components": ["/users"],
            "impact": "Test impact",
            "recommendation": "Review affected clients.",
            "confidence": 1.0,
            "status": "skipped",
            "input_hash": "",
            "prompt_version": "",
            "model": "",
            "evidence": [],
            "error": "",
        }

    def test_breaking_change_stores_fail_gate(self):
        comparison = self._create_comparison()

        fake_change = self._fake_change(
            "endpoint_removed"
        )

        with patch(
            "api_app.services.compare_api_specs",
            return_value=[fake_change],
        ), patch(
            "api_app.services._analyze_change_impact",
            return_value=self._fake_impact(),
        ), patch(
            "api_app.services.build_migration_plan",
        ):
            result = run_comparison(comparison)

        result.refresh_from_db()

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.gate_status, "FAIL")
        self.assertEqual(
            result.gate_reason_code,
            "BREAKING_CHANGES_FOUND",
        )

        self.assertEqual(
            result.quality_report["head"]["quality_score"],
            100,
        )

        self.assertEqual(
            result.summary_counts["breaking"],
            1,
        )

        self.assertEqual(
            result.changes.count(),
            1,
        )

        self.assertEqual(
            result.changes.first().compatibility,
            "breaking",
        )

    def test_non_breaking_change_stores_pass_gate(self):
        comparison = self._create_comparison()

        fake_change = self._fake_change(
            "endpoint_added"
        )

        with patch(
            "api_app.services.compare_api_specs",
            return_value=[fake_change],
        ), patch(
            "api_app.services._analyze_change_impact",
            return_value={
                **self._fake_impact(),
                "classification": "non-breaking",
                "severity": "low",
            },
        ), patch(
            "api_app.services.build_migration_plan",
        ):
            result = run_comparison(comparison)

        result.refresh_from_db()

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.gate_status, "PASS")
        self.assertEqual(
            result.gate_reason_code,
            "NO_BREAKING_CHANGES",
        )

        self.assertEqual(
            result.summary_counts["non_breaking"],
            1,
        )

        self.assertEqual(
            result.changes.first().compatibility,
            "non-breaking",
        )

    def test_quality_report_is_stored(self):
        comparison = self._create_comparison()

        with patch(
            "api_app.services.compare_api_specs",
            return_value=[],
        ), patch(
            "api_app.services.build_migration_plan",
        ):
            result = run_comparison(comparison)

        result.refresh_from_db()

        self.assertEqual(result.status, "completed")

        self.assertIn(
            "base",
            result.quality_report,
        )

        self.assertIn(
            "head",
            result.quality_report,
        )

        self.assertEqual(
            result.quality_report["threshold"],
            70,
        )

        self.assertEqual(
            result.quality_report["head"]["quality_score"],
            100,
        )

        self.assertEqual(
            result.gate_status,
            "PASS",
        )