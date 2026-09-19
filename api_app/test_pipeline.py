from api_pipeline import analyze_api


results = analyze_api(
    "./project/data/old_api.yaml",
    "./project/data/new_api.yaml"
)


print("\nAPI COMPATIBILITY REPORT")
print("=" * 50)

for result in results:
    print("\nCHANGE:")
    print(result["change"])

    print("\nRULE RESULT:")
    print(result["rule_result"])

    print("\nLLM RESULT:")
    print(result["llm_result"])

    print("-" * 50)