from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from langchain_chroma import Chroma

from .document_loader import load_api_document
from .document_splitter import split_documents
from .embeddings import embeddings


DEFAULT_PERSIST_ROOT = Path(".chroma")


def _safe_namespace(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(value))
    return value[:80] or "default"


def _file_digest(file_path: str | Path) -> str:
    path = Path(file_path)

    try:
        data = path.read_bytes()
    except OSError:
        data = str(path.resolve()).encode("utf-8")

    return hashlib.sha256(data).hexdigest()[:16]


def _collection_name(
    file_path: str | Path,
    project_id: str | None = None,
) -> str:
    """
    Generate a deterministic, project-isolated collection name.

    Existing callers that only provide file_path continue to work.
    """
    digest = _file_digest(file_path)

    if project_id is None:
        namespace = "default"
    else:
        namespace = _safe_namespace(project_id)

    return f"api_documentation_{namespace}_{digest}"


def _persist_directory(
    collection_name: str,
    persist_root: str | Path = DEFAULT_PERSIST_ROOT,
) -> str:
    return str(Path(persist_root) / collection_name)


def create_vector_store(
    file_path: str | Path,
    *,
    project_id: str | None = None,
    persist_root: str | Path = DEFAULT_PERSIST_ROOT,
):
    """
    Create or load a Chroma store for API documentation.

    project_id isolates documentation between projects.

    Backward compatible:
        create_vector_store("docs.txt")
    """
    documents = load_api_document(file_path)
    chunks = split_documents(documents)

    collection_name = _collection_name(
        file_path,
        project_id=project_id,
    )

    persist_directory = _persist_directory(
        collection_name,
        persist_root=persist_root,
    )

    return Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        collection_name=collection_name,
        persist_directory=persist_directory,
    )


__all__ = [
    "create_vector_store",
    "_collection_name",
    "_file_digest",
    "_persist_directory",
]