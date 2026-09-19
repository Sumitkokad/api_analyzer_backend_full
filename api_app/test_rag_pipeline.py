from schemas import APIChange
from rag_pipeline import analyze_api_change


change = APIChange(
    change_type="endpoint_removed",
    endpoint="GET /users",
    old_value="GET /users",
    new_value=None
)


result = analyze_api_change(
    change,
    "./project/data/old_api.yaml"
)


print("\nRAG IMPACT ANALYSIS")
print("=" * 50)

print("\nCLASSIFICATION:")
print(result.classification)

print("\nSEVERITY:")
print(result.severity)

print("\nAFFECTED COMPONENTS:")
for component in result.affected_components:
    print("-", component)

print("\nIMPACT:")
print(result.impact)

print("\nRECOMMENDATION:")
print(result.recommendation)