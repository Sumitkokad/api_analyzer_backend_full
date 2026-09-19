from __future__ import annotations

from typing import Any

from .api_diff import compare_api_specs
from .breaking_change_rules import classify_change
from .impact_analysis import analyze_impact


def _change_to_dict(change: Any) -> dict[str, Any]:
    if isinstance(change, dict):
        return change

    if hasattr(change, "model_dump"):
        return change.model_dump()

    if hasattr(change, "dict"):
        return change.dict()

    if hasattr(change, "__dict__"):
        return dict(change.__dict__)

    return {"change": str(change)}


def _retrieve_documentation(
    change: Any,
    documentation_file: str | None,
) -> str:
    """
    RAG is optional enrichment only.

    Failure to retrieve documentation must not prevent the
    deterministic compatibility result from being produced.
    """
    if not documentation_file:
        return ""

    try:
        from .vector_store import create_vector_store

        vector_store = create_vector_store(documentation_file)

        retriever = vector_store.as_retriever(
            search_kwargs={"k": 2}
        )

        documents = retriever.invoke(
            str(_change_to_dict(change))
        )

        return "\n\n".join(
            getattr(document, "page_content", "")
            for document in documents
            if getattr(document, "page_content", "")
        )

    except Exception:
        return ""


def analyze_all_changes(
    old_api,
    new_api,
    documentation_file: str | None = None,
):
    """
    Common compatibility analysis flow.

    Order:
        diff -> deterministic rules -> optional RAG -> explanation

    RAG/LLM never determines classification or severity.
    """
    changes = compare_api_specs(old_api, new_api)

    results = []

    for change in changes:
        # ---------------------------------------------------------------
        # 1. Deterministic classification
        # ---------------------------------------------------------------
        rule_result = classify_change(change)

        # ---------------------------------------------------------------
        # 2. Optional documentation retrieval
        # ---------------------------------------------------------------
        documentation = _retrieve_documentation(
            change,
            documentation_file,
        )

        # ---------------------------------------------------------------
        # 3. Explanation only
        # ---------------------------------------------------------------
        impact_result = analyze_impact(
            change,
            documentation,
            rule_result,
        )

        results.append(
            {
                "change": change,
                "rule_result": rule_result,
                "impact_report": impact_result,
            }
        )

    return results


__all__ = [
    "analyze_all_changes",
]