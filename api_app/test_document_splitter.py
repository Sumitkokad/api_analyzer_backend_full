from document_loader import load_api_document
from document_splitter import split_documents


documents = load_api_document(
    "./project/data/old_api.yaml"
)

chunks = split_documents(documents)


print(f"\nNumber of chunks: {len(chunks)}")

for i, chunk in enumerate(chunks):
    print(f"\n--- CHUNK {i + 1} ---")
    print(chunk.page_content)