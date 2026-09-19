import unittest
from unittest.mock import patch

try:
    from .rag_pipeline import analyze_api_change
    from .schemas import APIChange, ImpactReport
except ImportError:
    from rag_pipeline import analyze_api_change
    from schemas import APIChange, ImpactReport


class _FakeRetriever:
    def invoke(self, query):
        return [type("Document", (), {"page_content": "GET /users documentation"})()]


class _FakeVectorStore:
    def as_retriever(self, search_kwargs):
        return _FakeRetriever()


class RAGPipelineTests(unittest.TestCase):
    def test_rag_pipeline_passes_retrieved_context_to_impact_analysis(self):
        change = APIChange(
            change_type="endpoint_removed",
            endpoint="GET /users",
            old_value="GET /users",
            new_value=None,
        )
        report = ImpactReport(
            classification="breaking",
            severity="high",
            reason="Endpoint removed.",
            affected_components=["GET /users"],
            impact="Existing callers fail.",
            recommendation="Restore or version the endpoint.",
        )

        with patch("api_app.rag_pipeline.create_vector_store", return_value=_FakeVectorStore()):
            with patch("api_app.rag_pipeline.analyze_impact", return_value=report) as analyze:
                result = analyze_api_change(change, "./project/data/old_api.yaml")

        self.assertEqual(result.classification, "breaking")
        self.assertIn("GET /users documentation", analyze.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
