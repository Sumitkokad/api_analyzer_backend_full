from api_parser import load_api_spec
from api_diff import compare_endpoints
from schemas import APIChange
from breaking_change_rules import classify_change as classify_rule
from classification import classify_change as classify_llm


def analyze_api(old_file, new_file):
    # 1. Load API specifications
    old_api = load_api_spec(old_file)
    new_api = load_api_spec(new_file)

    # 2. Compare APIs
    diff = compare_endpoints(old_api, new_api)

    results = []

    # 3. Process removed endpoints
    for endpoint in diff["removed"]:
        change = APIChange(
            change_type="endpoint_removed",
            endpoint=endpoint,
            old_value=endpoint,
            new_value=None
        )

        rule_result = classify_rule(change)

        llm_result = classify_llm(
            f"""
API change:
{change}

Deterministic classification:
{rule_result}
"""
        )

        results.append({
            "change": change,
            "rule_result": rule_result,
            "llm_result": llm_result
        })

    # 4. Process added endpoints
    for endpoint in diff["added"]:
        change = APIChange(
            change_type="endpoint_added",
            endpoint=endpoint,
            old_value=None,
            new_value=endpoint
        )

        rule_result = classify_rule(change)

        llm_result = classify_llm(
            f"""
API change:
{change}

Deterministic classification:
{rule_result}
"""
        )

        results.append({
            "change": change,
            "rule_result": rule_result,
            "llm_result": llm_result
        })

    return results