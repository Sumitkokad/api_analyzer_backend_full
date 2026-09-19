import json
import os
import unittest
from unittest.mock import patch

try:
    from .llm import invoke_llm
except ImportError:
    from llm import invoke_llm


class LLMTests(unittest.TestCase):
    def test_disable_llm_returns_offline_structured_response(self):
        with patch.dict(os.environ, {"API_ANALYZER_DISABLE_LLM": "true"}, clear=False):
            response = invoke_llm("Explain endpoint removal.")

        payload = json.loads(response.content)
        self.assertEqual(payload["classification"], "non-breaking")
        self.assertIn("disabled", payload["reason"].lower())


if __name__ == "__main__":
    unittest.main()
