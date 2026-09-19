from document_loader import load_api_document
from document_splitter import split_documents
from embeddings import embeddings


documents = load_api_document(
    "./project/data/old_api.yaml"
)

chunks = split_documents(documents)

vectors = embeddings.embed_documents(
    [chunk.page_content for chunk in chunks]
)


print("\nNumber of chunks:", len(chunks))
print("Number of vectors:", len(vectors))
print("Vector dimension:", len(vectors[0]))