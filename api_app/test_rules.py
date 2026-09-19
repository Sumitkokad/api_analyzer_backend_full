import unittest

try:
    from .breaking_change_rules import classify_change
    from .schemas import APIChange
except ImportError:
    from breaking_change_rules import classify_change
    from schemas import APIChange


class BreakingChangeRuleTests(unittest.TestCase):
    def classify(self, change_type, **kwargs):
        change = APIChange(
            change_type=change_type,
            endpoint=kwargs.pop("endpoint", "GET /users"),
            **kwargs,
        )
        return classify_change(change)

    def test_endpoint_rules(self):
        self.assertEqual(self.classify("endpoint_removed")["classification"], "breaking")
        self.assertEqual(self.classify("endpoint_added")["classification"], "non-breaking")

    def test_request_required_field_is_breaking(self):
        result = self.classify(
            "request_field_added_required",
            direction="request",
            parameter="email",
            new_value={"type": "string"},
        )
        self.assertEqual(result["classification"], "breaking")
        self.assertEqual(result["severity"], "high")

    def test_response_required_field_is_tolerant_reader_safe_by_default(self):
        result = self.classify(
            "response_field_added_required",
            direction="response",
            parameter="email",
            new_value={"type": "string"},
        )
        self.assertEqual(result["classification"], "non-breaking")

    def test_unknown_change_is_not_silently_non_breaking(self):
        result = self.classify("new_unmapped_change_type")
        self.assertEqual(result["classification"], "potentially-breaking")
        self.assertEqual(result["severity"], "medium")


if __name__ == "__main__":
    unittest.main()
