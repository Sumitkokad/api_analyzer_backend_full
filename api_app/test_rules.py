from schemas import APIChange
from breaking_change_rules import classify_change


removed_change = APIChange(
    change_type="endpoint_removed",
    endpoint="/users",
    old_value="GET /users",
    new_value=None
)

added_change = APIChange(
    change_type="endpoint_added",
    endpoint="/products",
    old_value=None,
    new_value="GET /products"
)


print("REMOVED ENDPOINT:")
print(classify_change(removed_change))

print("\nADDED ENDPOINT:")
print(classify_change(added_change))