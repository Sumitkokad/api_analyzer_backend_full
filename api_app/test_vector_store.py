import unittest

try:
    from .embeddings import LocalHashEmbeddings
    from .vector_store import _collection_name
except ImportError:
    from embeddings import LocalHashEmbeddings
    from vector_store import _collection_name


class VectorStoreTests(unittest.TestCase):
    def test_collection_name_is_stable(self):
        self.assertEqual(
            _collection_name("./project/data/old_api.yaml"),
            _collection_name("./project/data/old_api.yaml"),
        )

    def test_local_hash_embeddings_are_deterministic(self):
        embeddings = LocalHashEmbeddings()
        self.assertEqual(embeddings.embed_query("abc"), embeddings.embed_query("abc"))
        self.assertEqual(len(embeddings.embed_query("abc")), 32)


if __name__ == "__main__":
    unittest.main()
