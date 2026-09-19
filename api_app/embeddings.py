import hashlib
import os

from dotenv import load_dotenv

load_dotenv()


class LocalHashEmbeddings:
    def _embed(self, text):
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [byte / 255 for byte in digest[:32]]

    def embed_documents(self, texts):
        return [self._embed(text) for text in texts]

    def embed_query(self, text):
        return self._embed(text)


if os.getenv("API_ANALYZER_ENABLE_EMBEDDINGS", "").lower() in {"1", "true", "yes"}:
    from langchain_huggingface import HuggingFaceEndpointEmbeddings

    embeddings = HuggingFaceEndpointEmbeddings(
        model="sentence-transformers/all-MiniLM-L6-v2",
        huggingfacehub_api_token=os.getenv("HUGGINGFACEHUB_API_TOKEN")
    )
else:
    embeddings = LocalHashEmbeddings()
