from api_parser import load_api_spec

old_api = load_api_spec("./project/data/old_api.yaml")
new_api = load_api_spec("./project/data/new_api.yaml")

print("\nOLD API PATHS:")
print(old_api["paths"])

print("\nNEW API PATHS:")
print(new_api["paths"])