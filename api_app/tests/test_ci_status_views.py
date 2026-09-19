import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.test.client import RequestFactory

from api_app.ci_status_views import CIComparisonStatusView
from api_app.models import Comparison


class CIComparisonStatusViewTests(SimpleTestCase):

    def setUp(self):
        self.factory = RequestFactory()
        self.view = CIComparisonStatusView.as_view()

    def _request(self, token="test-token"):
        return self.factory.get(
            "/api/ci/comparisons/1/status/",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    @patch.object(
        CIComparisonStatusView,
        "_authenticate_token",
        return_value=None,
    )
    def test_missing_token_returns_401(self, mock_auth):
        request = self.factory.get(
            "/api/ci/comparisons/1/status/"
        )

        response = self.view(
            request,
            comparison_id=1,
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            json.loads(response.content),
            {"detail": "Invalid or missing project token."},
        )

        mock_auth.assert_called_once()

    @patch.object(
        CIComparisonStatusView,
        "_authenticate_token",
        return_value=None,
    )
    def test_invalid_token_returns_401(self, mock_auth):
        response = self.view(
            self._request("invalid-token"),
            comparison_id=1,
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            json.loads(response.content),
            {"detail": "Invalid or missing project token."},
        )

        mock_auth.assert_called_once()

    @patch("api_app.ci_status_views.Comparison.objects")
    @patch.object(
        CIComparisonStatusView,
        "_authenticate_token",
    )
    def test_comparison_not_found_returns_404(
        self,
        mock_auth,
        mock_comparison_objects,
    ):
        token = SimpleNamespace(
            project_id=7,
        )

        mock_auth.return_value = token

        mock_queryset = MagicMock()
        mock_queryset.get.side_effect = Comparison.DoesNotExist

        mock_comparison_objects.select_related.return_value = (
            mock_queryset
        )

        response = self.view(
            self._request(),
            comparison_id=999,
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            json.loads(response.content),
            {"detail": "Comparison not found."},
        )

        mock_comparison_objects.select_related.assert_called_once_with(
            "project"
        )
        mock_queryset.get.assert_called_once_with(
            pk=999
        )

    @patch("api_app.ci_status_views.Comparison.objects")
    @patch.object(
        CIComparisonStatusView,
        "_authenticate_token",
    )
    def test_wrong_project_returns_403(
        self,
        mock_auth,
        mock_comparison_objects,
    ):
        token = SimpleNamespace(
            project_id=7,
        )

        comparison = SimpleNamespace(
            project_id=99,
        )

        mock_auth.return_value = token

        mock_queryset = MagicMock()
        mock_queryset.get.return_value = comparison

        mock_comparison_objects.select_related.return_value = (
            mock_queryset
        )

        response = self.view(
            self._request(),
            comparison_id=1,
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            json.loads(response.content),
            {"detail": "You do not have access to this comparison."},
        )

    @patch("api_app.ci_status_views.AnalysisJob.objects")
    @patch("api_app.ci_status_views.Comparison.objects")
    @patch.object(
        CIComparisonStatusView,
        "_authenticate_token",
    )
    def test_success_returns_ci_status(
        self,
        mock_auth,
        mock_comparison_objects,
        mock_job_objects,
    ):
        token = SimpleNamespace(
            project_id=7,
        )

        comparison = SimpleNamespace(
            id=1,
            project_id=7,
            gate_status="FAIL",
            gate_reason_code="BREAKING_CHANGES_FOUND",
            summary_counts={
                "total": 3,
                "breaking": 1,
                "non_breaking": 2,
            },
            quality_report={
                "score": 0.98,
            },
            report_url=None,
            status="completed",
        )

        job = SimpleNamespace(
            id=10,
            status="completed",
            progress=100,
            stage="completed",
            error_code=None,
            error_detail=None,
        )

        mock_auth.return_value = token

        comparison_queryset = MagicMock()
        comparison_queryset.get.return_value = comparison
        mock_comparison_objects.select_related.return_value = (
            comparison_queryset
        )

        job_queryset = MagicMock()
        job_queryset.order_by.return_value.first.return_value = job
        mock_job_objects.filter.return_value = job_queryset

        response = self.view(
            self._request(),
            comparison_id=1,
        )

        self.assertEqual(response.status_code, 200)

        data = json.loads(response.content)

        self.assertEqual(data["comparison_id"], 1)
        self.assertEqual(data["job_id"], 10)
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["gate_status"], "FAIL")
        self.assertEqual(
            data["reason_code"],
            "BREAKING_CHANGES_FOUND",
        )
        self.assertEqual(data["progress"], 100)
        self.assertEqual(data["stage"], "completed")

        self.assertEqual(
            data["counts"]["breaking"],
            1,
        )

        self.assertEqual(
            data["quality"]["score"],
            0.98,
        )

        self.assertIsNone(data["error"])
        self.assertIsNone(data["report_url"])

    @patch("api_app.ci_status_views.AnalysisJob.objects")
    @patch("api_app.ci_status_views.Comparison.objects")
    @patch.object(
        CIComparisonStatusView,
        "_authenticate_token",
    )
    def test_success_without_job_returns_null_job_fields(
        self,
        mock_auth,
        mock_comparison_objects,
        mock_job_objects,
    ):
        token = SimpleNamespace(
            project_id=7,
        )

        comparison = SimpleNamespace(
            id=1,
            project_id=7,
            gate_status=None,
            gate_reason_code=None,
            summary_counts={},
            quality_report={},
            report_url=None,
            status="queued",
        )

        mock_auth.return_value = token

        comparison_queryset = MagicMock()
        comparison_queryset.get.return_value = comparison
        mock_comparison_objects.select_related.return_value = (
            comparison_queryset
        )

        job_queryset = MagicMock()
        job_queryset.order_by.return_value.first.return_value = None
        mock_job_objects.filter.return_value = job_queryset

        response = self.view(
            self._request(),
            comparison_id=1,
        )

        self.assertEqual(response.status_code, 200)

        data = json.loads(response.content)

        self.assertEqual(data["comparison_id"], 1)
        self.assertIsNone(data["job_id"])
        self.assertEqual(data["status"], "queued")
        self.assertIsNone(data["gate_status"])
        self.assertIsNone(data["progress"])
        self.assertIsNone(data["stage"])