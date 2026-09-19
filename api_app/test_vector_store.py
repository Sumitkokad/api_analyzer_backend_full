from vector_store import create_vector_store


vector_store = create_vector_store(
    "./project/data/old_api.yaml"
)

retriever = vector_store.as_retriever(
    search_kwargs={"k": 2}
)


query = "How can I get users?"

documents = retriever.invoke(query)


print("\nRETRIEVED DOCUMENTS")
print("=" * 50)

for i, document in enumerate(documents):
    print(f"\n--- DOCUMENT {i + 1} ---")
    print(document.page_content)