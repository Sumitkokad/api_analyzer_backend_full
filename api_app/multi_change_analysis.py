try:
    from schemas import APIChange
    from breaking_change_rules import classify_change
    from impact_analysis import analyze_impact
    from vector_store import create_vector_store
    from api_diff import compare_api_specs
except ImportError:
    from .schemas import APIChange
    from .breaking_change_rules import classify_change
    from .impact_analysis import analyze_impact
    from .vector_store import create_vector_store
    from .api_diff import compare_api_specs


def analyze_all_changes(old_api, new_api, documentation_file):
    vector_store = create_vector_store(
        documentation_file
    )

    retriever = vector_store.as_retriever(
        search_kwargs={"k": 2}
    )

    changes = compare_api_specs(old_api, new_api)

    results = []

    # Analyze every change
    for change in changes:

        rule_result = classify_change(change)

        documents = retriever.invoke(
            str(change)
        )

        documentation = "\n\n".join(
            document.page_content
            for document in documents
        )

        impact_result = analyze_impact(
            change,
            documentation
        )

        results.append({
            "change": change,
            "rule_result": rule_result,
            "impact_report": impact_result
        })

    return results
