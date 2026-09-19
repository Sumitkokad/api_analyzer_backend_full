from __future__ import annotations

from pathlib import Path

from langchain_core.documents import Document


DEFAULT_MAX_DOCUMENT_BYTES = 2 * 1024 * 1024


class DocumentLoadError(ValueError):
    pass


def load_api_document(
    file_path: str | Path,
    *,
    max_bytes: int = DEFAULT_MAX_DOCUMENT_BYTES,
):
    """
    Safely load documentation for optional RAG enrichment.

    The file is treated only as data.
    """
    path = Path(file_path)

    if not path.exists():
        raise DocumentLoadError(
            f"Documentation file does not exist: {path}"
        )

    if not path.is_file():
        raise DocumentLoadError(
            f"Documentation path is not a file: {path}"
        )

    try:
        size = path.stat().st_size
    except OSError as exc:
        raise DocumentLoadError(
            f"Unable to inspect documentation file: {path}"
        ) from exc

    if size > max_bytes:
        raise DocumentLoadError(
            f"Documentation file is too large: "
            f"{size} bytes > {max_bytes} bytes"
        )

    try:
        text = path.read_text(
            encoding="utf-8"
        )
    except UnicodeDecodeError as exc:
        raise DocumentLoadError(
            "Documentation must be valid UTF-8 text."
        ) from exc
    except OSError as exc:
        raise DocumentLoadError(
            f"Unable to read documentation file: {path}"
        ) from exc

    return [
        Document(
            page_content=text,
            metadata={
                "source": str(path),
                "document_type": "api_documentation",
            },
        )
    ]


__all__ = [
    "DocumentLoadError",
    "load_api_document",
    "DEFAULT_MAX_DOCUMENT_BYTES",
]