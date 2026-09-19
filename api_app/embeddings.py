from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv

load_dotenv()


TRUE_VALUES = {"1", "true", "yes", "on"}

DEFAULT_LOCAL_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def _enabled(name: str) -> bool:
    return (
        os.getenv(name, "")
        .strip()
        .lower()
        in TRUE_VALUES
    )


class LocalSemanticEmbeddings:
    """
    Semantic local embeddings.

    Uses sentence-transformers instead of SHA/hash vectors.
    The model is loaded lazily so importing this module does not
    immediately allocate model memory.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_LOCAL_MODEL,
    ):
        self.model_name = model_name
        self._model = None

    def _get_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    "Local semantic embeddings require "
                    "sentence-transformers. "
                    "Install it with: pip install sentence-transformers"
                ) from exc

            self._model = SentenceTransformer(
                self.model_name
            )

        return self._model

    def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        model = self._get_model()

        vectors = model.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )

        return vectors.tolist()

    def embed_query(
        self,
        text: str,
    ) -> list[float]:
        return self.embed_documents([text])[0]


class HuggingFaceSemanticEmbeddings:
    """
    Remote semantic embeddings through Hugging Face.

    Used only when API_ANALYZER_ENABLE_EMBEDDINGS=true.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_LOCAL_MODEL,
    ):
        self.model_name = model_name
        self._embeddings = None

    def _get_embeddings(self):
        if self._embeddings is None:
            try:
                from langchain_huggingface import (
                    HuggingFaceEndpointEmbeddings,
                )
            except ImportError as exc:
                raise RuntimeError(
                    "Hugging Face embeddings require "
                    "langchain-huggingface."
                ) from exc

            token = os.getenv(
                "HUGGINGFACEHUB_API_TOKEN"
            )

            if not token:
                raise RuntimeError(
                    "HUGGINGFACEHUB_API_TOKEN is required "
                    "when API_ANALYZER_ENABLE_EMBEDDINGS=true."
                )

            self._embeddings = (
                HuggingFaceEndpointEmbeddings(
                    model=self.model_name,
                    huggingfacehub_api_token=token,
                )
            )

        return self._embeddings

    def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        return self._get_embeddings().embed_documents(texts)

    def embed_query(
        self,
        text: str,
    ) -> list[float]:
        return self._get_embeddings().embed_query(text)


def get_embeddings():
    """
    Select semantic embedding backend.

    Default:
        local sentence-transformers

    Optional:
        Hugging Face endpoint when explicitly enabled.
    """
    model_name = os.getenv(
        "API_ANALYZER_EMBEDDING_MODEL",
        DEFAULT_LOCAL_MODEL,
    )

    if _enabled("API_ANALYZER_ENABLE_EMBEDDINGS"):
        return HuggingFaceSemanticEmbeddings(
            model_name=model_name,
        )

    return LocalSemanticEmbeddings(
        model_name=model_name,
    )


# Backward-compatible public variable used by vector_store.py.
embeddings = get_embeddings()


__all__ = [
    "LocalSemanticEmbeddings",
    "HuggingFaceSemanticEmbeddings",
    "get_embeddings",
    "embeddings",
]