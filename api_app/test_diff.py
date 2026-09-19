from api_parser import load_api_spec

from api_diff import (
    compare_endpoints,
    compare_parameters,
    compare_request_bodies,
    compare_responses
)


old_api = load_api_spec(
    "./project/data/old_api.yaml"
)

new_api = load_api_spec(
    "./project/data/new_api.yaml"
)


print("\nENDPOINT CHANGES")
print("=" * 50)

endpoint_changes = compare_endpoints(
    old_api,
    new_api
)

print("Added:", endpoint_changes["added"])
print("Removed:", endpoint_changes["removed"])


print("\nPARAMETER CHANGES")
print("=" * 50)

parameter_changes = compare_parameters(
    old_api,
    new_api
)

for change in parameter_changes:
    print(change)


print("\nREQUEST BODY CHANGES")
print("=" * 50)

request_body_changes = compare_request_bodies(
    old_api,
    new_api
)

for change in request_body_changes:
    print(change)

print("\nRESPONSE CHANGES")
print("=" * 50)

response_changes = compare_responses(
    old_api,
    new_api
)

for change in response_changes:
    print(change)    