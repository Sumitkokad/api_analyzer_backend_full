from django.test import SimpleTestCase
from django.urls import resolve

from api_app.ci_status_views import CIComparisonStatusView


class CIComparisonStatusURLTests(SimpleTestCase):

    def test_ci_status_url_resolves(self):
        match = resolve(
            "/api/ci/comparisons/123/status/"
        )

        self.assertEqual(
            match.func.view_class,
            CIComparisonStatusView,
        )

        self.assertEqual(
            match.url_name,
            "ci-comparison-status",
        )

        self.assertEqual(
            match.kwargs["comparison_id"],
            123,
        )