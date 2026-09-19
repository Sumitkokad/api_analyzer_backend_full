from document_loader import load_api_document


documents = load_api_document(
    "./project/data/old_api.yaml"
)


print("\nDOCUMENT:")
print(documents[0].page_content)

print("\nMETADATA:")
print(documents[0].metadata)