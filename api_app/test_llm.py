from llm import invoke_llm


response = invoke_llm(
    "Explain in one sentence why removing an API endpoint can be a breaking change."
)

print("\nFINAL RESPONSE:")
print(response.content)