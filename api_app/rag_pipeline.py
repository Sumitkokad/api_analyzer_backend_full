try:
    from .vector_store import create_vector_store
    from .impact_analysis import analyze_impact
except ImportError:
    from vector_store import create_vector_store
    from impact_analysis import analyze_impact


def analyze_api_change(change, documentation_file):
    vector_store = create_vector_store(documentation_file)

    retriever = vector_store.as_retriever(
        search_kwargs={"k": 2}
    )

    query = str(change)

    documents = retriever.invoke(query)

    documentation = "\n\n".join(
        document.page_content
        for document in documents
    )

    return analyze_impact(
        change,
        documentation
    )
