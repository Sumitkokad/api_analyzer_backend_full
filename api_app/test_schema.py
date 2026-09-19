from schemas import APIChange


change = APIChange(
    change_type="endpoint_added",
    endpoint="/products",
    old_value=None,
    new_value="GET /products"
)

print(change)
print(change.change_type)
print(change.endpoint)