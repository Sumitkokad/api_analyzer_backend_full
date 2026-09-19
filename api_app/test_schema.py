import unittest

try:
    from .schemas import APIChange
except ImportError:
    from schemas import APIChange


class SchemaTests(unittest.TestCase):
    def test_api_change_generates_stable_id(self):
        first = APIChange(
            change_type="endpoint_added",
            endpoint="/products",
            old_value=None,
            new_value="GET /products",
        )
        second = APIChange(
            change_type="endpoint_added",
            endpoint="/products",
            old_value=None,
            new_value="GET /products",
        )

        self.assertEqual(first.change_id, second.change_id)
        self.assertEqual(first.change_type, "endpoint_added")


if __name__ == "__main__":
    unittest.main()
