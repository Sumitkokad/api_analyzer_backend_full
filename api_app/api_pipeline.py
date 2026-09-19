from __future__ import annotations

from typing import Any

from .api_diff import compare_api_specs
from .api_parser import load_api_spec
from .breaking_change_rules import classify_change
from .impact_analysis import analyze_impact


def _retrieve_documentation(documentation_file: str | None, change: Any) -> str:
    """
    Retrieve optional documentation evidence for a single API change.

    RAG/LLM is explanatory only. Retrieval failures must not block
    deterministic API compatibility analysis.
    """
    if not documentation_file:
        return ""

    try:
        from .vector_store import create_vector_store

        vector_store = create_vector_store(documentation_file)
        retriever = vector_store.as_retriever(
            search_kwargs={"k": 2}
        )

        documents = retriever.invoke(str(change))

        return "\n\n".join(
            getattr(document, "page_content", "")
            for document in documents
            if getattr(document, "page_content", "")
        )
    except Exception:
        return ""


def analyze_api(
    old_file,
    new_file,
    documentation_file: str | None = None,
):
    """
    Legacy-compatible API analysis entry point.

    Pipeline:
        load -> semantic diff -> deterministic rules -> optional RAG
        -> impact explanation

    Deterministic rules remain authoritative for classification/severity.
    """
    old_api = load_api_spec(old_file)
    new_api = load_api_spec(new_file)

    changes = compare_api_specs(
        old_api,
        new_api,
    )

    results = []

    for change in changes:
        rule_result = classify_change(change)

        documentation = _retrieve_documentation(
            documentation_file,
            change,
        )

        impact_report = analyze_impact(
            change,
            documentation,
            rule_result=rule_result,
        )

        results.append(
            {
                "change": change,
                "rule_result": rule_result,

                # Backward-compatible key.
                "llm_result": impact_report,

                # Preferred explicit name.
                "impact_report": impact_report,
            }
        )

    return results