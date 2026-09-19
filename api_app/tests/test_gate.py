from django.test import SimpleTestCase

from api_app.contracts.gate import evaluate_gate


class GateEngineTests(SimpleTestCase):

    def test_high_severity_breaking_change_fails(self):
        result = evaluate_gate(
            [
                {
                    "classification": "breaking",
                    "severity": "high",
                }
            ]
        )

        self.assertEqual(result.status, "FAIL")
        self.assertEqual(
            result.reason_code,
            "BREAKING_CHANGES_FOUND",
        )
        self.assertEqual(result.blocking_change_count, 1)

    def test_medium_breaking_change_warns_when_threshold_is_high(self):
        result = evaluate_gate(
            [
                {
                    "classification": "breaking",
                    "severity": "medium",
                }
            ],
            fail_severity_threshold="high",
        )

        self.assertEqual(result.status, "WARN")
        self.assertEqual(
            result.reason_code,
            "BREAKING_CHANGES_FOUND",
        )
        self.assertEqual(result.breaking_change_count, 1)

    def test_low_breaking_change_warns_when_threshold_is_medium(self):
        result = evaluate_gate(
            [
                {
                    "classification": "breaking",
                    "severity": "low",
                }
            ],
            fail_severity_threshold="medium",
        )

        self.assertEqual(result.status, "WARN")
        self.assertEqual(result.breaking_change_count, 1)

    def test_potentially_breaking_warns_by_default(self):
        result = evaluate_gate(
            [
                {
                    "classification": "potentially-breaking",
                    "severity": "medium",
                }
            ]
        )

        self.assertEqual(result.status, "WARN")
        self.assertEqual(
            result.reason_code,
            "POTENTIAL_BREAKING_CHANGES",
        )
        self.assertEqual(
            result.potentially_breaking_count,
            1,
        )

    def test_potentially_breaking_can_fail_in_strict_policy(self):
        result = evaluate_gate(
            [
                {
                    "classification": "potentially-breaking",
                    "severity": "medium",
                }
            ],
            potentially_breaking_policy="fail",
        )

        self.assertEqual(result.status, "FAIL")
        self.assertEqual(
            result.reason_code,
            "BREAKING_CHANGES_FOUND",
        )
        self.assertEqual(result.blocking_change_count, 1)

    def test_non_breaking_change_passes(self):
        result = evaluate_gate(
            [
                {
                    "classification": "non-breaking",
                    "severity": "low",
                }
            ]
        )

        self.assertEqual(result.status, "PASS")
        self.assertEqual(
            result.reason_code,
            "NO_BREAKING_CHANGES",
        )

    def test_waived_breaking_change_warns(self):
        result = evaluate_gate(
            [
                {
                    "classification": "breaking",
                    "severity": "high",
                    "waived": True,
                }
            ]
        )

        self.assertEqual(result.status, "WARN")
        self.assertEqual(
            result.reason_code,
            "WAIVED_BREAKING_CHANGES",
        )
        self.assertEqual(
            result.waived_breaking_count,
            1,
        )
        self.assertEqual(
            result.blocking_change_count,
            0,
        )

    def test_invalid_classification_returns_error(self):
        result = evaluate_gate(
            [
                {
                    "classification": "unknown",
                    "severity": "high",
                }
            ]
        )

        self.assertEqual(result.status, "ERROR")
        self.assertEqual(
            result.reason_code,
            "ANALYZER_INTERNAL_ERROR",
        )

    def test_invalid_severity_returns_error(self):
        result = evaluate_gate(
            [
                {
                    "classification": "breaking",
                    "severity": "critical",
                }
            ]
        )

        self.assertEqual(result.status, "ERROR")
        self.assertEqual(
            result.reason_code,
            "ANALYZER_INTERNAL_ERROR",
        )

    def test_low_quality_contract_warns(self):
        result = evaluate_gate(
            [],
            quality_report={
                "quality_score": 55,
                "threshold": 70,
            },
        )

        self.assertEqual(result.status, "WARN")
        self.assertEqual(
            result.reason_code,
            "LOW_CONTRACT_COVERAGE",
        )
        self.assertEqual(result.quality_score, 55)

    def test_low_quality_contract_errors_in_strict_mode(self):
        result = evaluate_gate(
            [],
            quality_report={
                "quality_score": 55,
                "threshold": 70,
            },
            strict_quality=True,
        )

        self.assertEqual(result.status, "ERROR")
        self.assertEqual(
            result.reason_code,
            "LOW_CONTRACT_COVERAGE",
        )

    def test_breaking_change_has_priority_over_quality_warning(self):
        result = evaluate_gate(
            [
                {
                    "classification": "breaking",
                    "severity": "high",
                }
            ],
            quality_report={
                "quality_score": 40,
                "threshold": 70,
            },
        )

        self.assertEqual(result.status, "FAIL")
        self.assertEqual(
            result.reason_code,
            "BREAKING_CHANGES_FOUND",
        )

    def test_empty_changes_and_no_quality_report_pass(self):
        result = evaluate_gate([])

        self.assertEqual(result.status, "PASS")
        self.assertEqual(
            result.reason_code,
            "NO_BREAKING_CHANGES",
        )