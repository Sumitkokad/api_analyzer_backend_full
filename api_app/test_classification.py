from classification import classify_change


change = """
The username field was removed from the request body
of POST /users.
"""

result = classify_change(change)

print("\nRESULT:")
print(result)

print("\nCLASSIFICATION:")
print(result.classification)

print("\nSEVERITY:")
print(result.severity)

print("\nREASON:")
print(result.reason)