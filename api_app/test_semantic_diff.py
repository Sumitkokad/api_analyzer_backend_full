import os
import unittest

try:
    from api_diff import compare_api_specs
except ImportError:
    from .api_diff import compare_api_specs


class SemanticDiffTests(unittest.TestCase):
    def base_spec(self, schema):
        return {
            "openapi": "3.0.0",
            "info": {"title": "T", "version": "1"},
            "paths": {
                "/users": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {
                                    "application/json": {
                                        "schema": schema,
                                    },
                                },
                            },
                        },
                    },
                },
            },
        }

    def change_types(self, old, new):
        return {change.change_type for change in compare_api_specs(old, new)}

    def test_ref_resolution_and_nested_schema_diff(self):
        old = self.base_spec({"$ref": "#/components/schemas/User"})
        old["components"] = {
            "schemas": {
                "User": {
                    "type": "object",
                    "properties": {
                        "profile": {
                            "type": "object",
                            "properties": {"age": {"type": "integer"}},
                        },
                    },
                },
            },
        }
        new = self.base_spec({"$ref": "#/components/schemas/User"})
        new["components"] = {
            "schemas": {
                "User": {
                    "type": "object",
                    "properties": {
                        "profile": {
                            "type": "object",
                            "properties": {"age": {"type": "string"}},
                        },
                    },
                },
            },
        }

        changes = compare_api_specs(old, new)
        self.assertIn("schema_type_changed", {change.change_type for change in changes})
        self.assertIn("$.profile.age", {change.schema_path for change in changes})

    def test_enum_reorder_is_ignored_but_value_changes_are_detected(self):
        old = self.base_spec({"type": "string", "enum": ["a", "b", "c"]})
        reordered = self.base_spec({"type": "string", "enum": ["c", "b", "a"]})
        changed = self.base_spec({"type": "string", "enum": ["a", "b", "d"]})

        self.assertEqual(compare_api_specs(old, reordered), [])
        self.assertIn("enum_values_removed", self.change_types(old, changed))
        self.assertIn("enum_values_added", self.change_types(old, changed))

    def test_media_type_status_and_security_changes_are_detected(self):
        old = self.base_spec({"type": "object"})
        new = self.base_spec({"type": "object"})
        old["security"] = []
        new["security"] = [{"bearerAuth": []}]
        new["components"] = {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer"},
            },
        }
        del new["paths"]["/users"]["get"]["responses"]["200"]["content"]["application/json"]
        new["paths"]["/users"]["get"]["responses"]["200"]["content"]["application/xml"] = {
            "schema": {"type": "object"},
        }
        new["paths"]["/users"]["get"]["responses"]["404"] = {"description": "not found"}

        types = self.change_types(old, new)
        self.assertIn("response_media_type_removed", types)
        self.assertIn("response_media_type_added", types)
        self.assertIn("response_status_added", types)
        self.assertIn("global_security_changed", types)
        self.assertIn("security_scheme_added", types)


if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "project.settings")
    unittest.main()
