from __future__ import annotations

from typing import Any


DEFAULT_CHUNK_SIZE = 500
DEFAULT_CHUNK_OVERLAP = 50


def split_documents(
    documents,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
):
    """
    Split documentation into deterministic chunks.

    Metadata from the original Document objects is preserved by
    RecursiveCharacterTextSplitter.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than 0.")

    if chunk_overlap < 0:
        raise ValueError("chunk_overlap cannot be negative.")

    if chunk_overlap >= chunk_size:
        raise ValueError(
            "chunk_overlap must be smaller than chunk_size."
        )

    try:
        from langchain_text_splitters import (
            RecursiveCharacterTextSplitter,
        )
    except ImportError as exc:
        raise RuntimeError(
            "langchain-text-splitters is required."
        ) from exc

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=[
            "\n\n",
            "\n",
            " ",
            "",
        ],
    )

    return splitter.split_documents(documents)


__all__ = [
    "split_documents",
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_CHUNK_OVERLAP",
]