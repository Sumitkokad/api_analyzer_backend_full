from __future__ import annotations

from typing import Any

from .breaking_change_rules import classify_change
from .impact_analysis import analyze_impact


def _change_text(change: Any) -> str:
    if isinstance(change, dict):
        return str(change)

    if hasattr(change, "model_dump"):
        return str(change.model_dump())

    if hasattr(change, "dict"):
        return str(change.dict())

    return str(change)


def _retrieve_documentation(
    change: Any,
    documentation_file: str | None,
) -> str:
    """
    RAG is optional enrichment only.

    Retrieval failure must not prevent deterministic analysis.
    """
    if not documentation_file:
        return ""

    try:
        from .vector_store import create_vector_store

        vector_store = create_vector_store(
            documentation_file
        )

        retriever = vector_store.as_retriever(
            search_kwargs={"k": 2}
        )

        documents = retriever.invoke(
            _change_text(change)
        )

        return "\n\n".join(
            getattr(document, "page_content", "")
            for document in documents
            if getattr(document, "page_content", "")
        )

    except Exception:
        return ""


def analyze_api_change(
    change,
    documentation_file: str | None = None,
):
    """
    Backward-compatible single-change RAG pipeline.

    Flow:
        deterministic rule -> optional retrieval -> explanation

    RAG/LLM never determines classification or severity.
    """
    rule_result = classify_change(change)

    documentation = _retrieve_documentation(
        change,
        documentation_file,
    )

    return analyze_impact(
        change,
        documentation,
        rule_result,
    )


__all__ = [
    "analyze_api_change",
]