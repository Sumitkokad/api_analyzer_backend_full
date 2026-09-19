import os
import unittest
from unittest.mock import patch

try:
    from .classification import classify_change
except ImportError:
    from classification import classify_change


class LLMClassificationTests(unittest.TestCase):
    def test_classification_can_run_offline(self):
        with patch.dict(os.environ, {"API_ANALYZER_DISABLE_LLM": "true"}, clear=False):
            result = classify_change("The username field was removed from POST /users.")

        self.assertEqual(result.classification, "non-breaking")
        self.assertEqual(result.severity, "low")


if __name__ == "__main__":
    unittest.main()
