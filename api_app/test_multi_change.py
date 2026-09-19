from api_parser import load_api_spec
from multi_change_analysis import analyze_all_changes


old_api = load_api_spec(
    "./project/data/old_api.yaml"
)

new_api = load_api_spec(
    "./project/data/new_api.yaml"
)


results = analyze_all_changes(
    old_api,
    new_api,
    "./project/data/old_api.yaml"
)


print("\nAPI COMPATIBILITY REPORT")
print("=" * 60)


for index, result in enumerate(results, start=1):

    print(f"\nCHANGE {index}")
    print("-" * 60)

    print("\nAPI CHANGE:")
    print(result["change"])

    print("\nRULE CLASSIFICATION:")
    print(result["rule_result"])

    report = result["impact_report"]

    print("\nIMPACT REPORT:")
    print("Classification:", report.classification)
    print("Severity:", report.severity)

    print("\nAffected Components:")

    for component in report.affected_components:
        print("-", component)

    print("\nImpact:")
    print(report.impact)

    print("\nRecommendation:")
    print(report.recommendation)