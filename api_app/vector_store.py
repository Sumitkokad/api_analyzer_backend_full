import hashlib
from pathlib import Path

from langchain_chroma import Chroma

try:
    from .document_loader import load_api_document
    from .document_splitter import split_documents
    from .embeddings import embeddings
except ImportError:
    from document_loader import load_api_document
    from document_splitter import split_documents
    from embeddings import embeddings


def _collection_name(file_path):
    path = Path(file_path)
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
    return f"api_documentation_{digest}"


def create_vector_store(file_path):
    documents = load_api_document(file_path)
    chunks = split_documents(documents)
    collection_name = _collection_name(file_path)
    persist_directory = str(Path(".chroma") / collection_name)

    vector_store = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        collection_name=collection_name,
        persist_directory=persist_directory
    )

    return vector_store
