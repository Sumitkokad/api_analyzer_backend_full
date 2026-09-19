from django.test import SimpleTestCase

from api_app.contracts.quality import evaluate_contract_quality


class ContractQualityTests(SimpleTestCase):

    def test_good_contract_has_high_quality_score(self):
        spec = {
            "openapi": "3.0.3",
            "paths": {
                "/users": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "integer"},
                                                "name": {"type": "string"},
                                            },
                                        }
                                    }
                                },
                            }
                        }
                    }
                }
            },
        }

        report = evaluate_contract_quality(spec)

        self.assertEqual(report.operations_count, 1)
        self.assertEqual(report.operations_missing_responses, 0)
        self.assertGreaterEqual(report.quality_score, 80)

        codes = {finding.code for finding in report.findings}
        self.assertNotIn("LOW_CONTRACT_COVERAGE", codes)

    def test_missing_responses_are_detected(self):
        spec = {
            "openapi": "3.0.3",
            "paths": {
                "/users": {
                    "get": {}
                }
            },
        }

        report = evaluate_contract_quality(spec)

        self.assertEqual(report.operations_count, 1)
        self.assertEqual(report.operations_missing_responses, 1)

        codes = {finding.code for finding in report.findings}
        self.assertIn("MISSING_RESPONSES", codes)

    def test_empty_schema_is_detected(self):
        spec = {
            "openapi": "3.0.3",
            "paths": {
                "/users": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {
                                        "schema": {}
                                    }
                                },
                            }
                        }
                    }
                }
            },
        }

        report = evaluate_contract_quality(spec)

        self.assertGreaterEqual(report.empty_schema_count, 1)

        codes = {finding.code for finding in report.findings}
        self.assertIn("EMPTY_SCHEMAS", codes)

    def test_normalization_loss_is_reported(self):
        spec = {
            "openapi": "3.0.3",
            "paths": {
                "/users": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "OK"
                            }
                        }
                    }
                }
            },
        }

        report = evaluate_contract_quality(
            spec,
            normalization_quality={
                "dropped_keywords": 2,
                "ignored_links": 1,
                "unresolved_refs": 1,
                "recursive_refs": 1,
            },
        )

        self.assertEqual(report.ignored_surface_count, 5)
        self.assertEqual(report.unresolved_ref_count, 1)
        self.assertEqual(report.recursive_ref_count, 1)

        codes = {finding.code for finding in report.findings}

        self.assertIn("IGNORED_NORMALIZATION_KEYWORDS", codes)
        self.assertIn("IGNORED_RESPONSE_LINKS", codes)
        self.assertIn("UNRESOLVED_REFS", codes)
        self.assertIn("RECURSIVE_REFS", codes)

    def test_route_coverage_is_calculated(self):
        spec = {
            "openapi": "3.0.3",
            "paths": {
                "/users": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "OK"
                            }
                        }
                    }
                }
            },
        }

        report = evaluate_contract_quality(
            spec,
            registered_routes=[
                "/users",
                "/orders",
            ],
        )

        self.assertEqual(report.registered_routes, 2)
        self.assertEqual(report.covered_routes, 1)
        self.assertEqual(report.route_coverage_percent, 50.0)

        codes = {finding.code for finding in report.findings}
        self.assertIn("LOW_ROUTE_COVERAGE", codes)

    def test_generator_warnings_are_reported(self):
        spec = {
            "openapi": "3.0.3",
            "paths": {
                "/users": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "OK"
                            }
                        }
                    }
                }
            },
        }

        report = evaluate_contract_quality(
            spec,
            generator_warnings=[
                "Unable to infer response schema",
                "Skipped unsupported annotation",
            ],
        )

        self.assertEqual(report.generator_warning_count, 2)

        codes = {finding.code for finding in report.findings}
        self.assertIn("GENERATOR_WARNINGS", codes)

    def test_quality_threshold_creates_low_quality_finding(self):
        spec = {
            "openapi": "3.0.3",
            "paths": {
                "/users": {
                    "get": {
                        "responses": {}
                    }
                }
            },
        }

        report = evaluate_contract_quality(
            spec,
            min_quality_score=95,
        )

        self.assertLess(report.quality_score, 95)

        codes = {finding.code for finding in report.findings}
        self.assertIn("LOW_CONTRACT_COVERAGE", codes)

    def test_strict_mode_marks_low_quality_as_error(self):
        spec = {
            "openapi": "3.0.3",
            "paths": {},
        }

        report = evaluate_contract_quality(
            spec,
            min_quality_score=70,
            strict=True,
        )

        low_quality_findings = [
            finding
            for finding in report.findings
            if finding.code == "LOW_CONTRACT_COVERAGE"
        ]

        self.assertTrue(low_quality_findings)
        self.assertTrue(
            any(
                finding.severity == "error"
                for finding in low_quality_findings
            )
        )

    def test_invalid_spec_returns_parse_error(self):
        report = evaluate_contract_quality(None)

        self.assertEqual(report.quality_score, 0)
        self.assertEqual(
            report.findings[0].code,
            "SCHEMA_PARSE_ERROR",
        )