from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import PydanticOutputParser

try:
    from .llm import invoke_llm
    from .schemas import ImpactReport
except ImportError:
    from llm import invoke_llm
    from schemas import ImpactReport


parser = PydanticOutputParser(
    pydantic_object=ImpactReport
)


prompt = PromptTemplate(
    template="""
You are an API compatibility analyst.

Analyze the API change using the API documentation.

API CHANGE:
{change}

API DOCUMENTATION:
{documentation}

Determine:

1. Whether the change is breaking or non-breaking.
2. The severity.
3. Which API components or consumers may be affected.
4. The impact on existing clients.
5. What developers should do to handle the change.
6. A concise reason explaining why this classification applies.
7. Confidence from 0.0 to 1.0.

Return ONLY the structured JSON required by the format instructions.

{format_instructions}
""",
    input_variables=["change", "documentation"],
    partial_variables={
        "format_instructions": parser.get_format_instructions()
    }
)


def analyze_impact(change, documentation):
    formatted_prompt = prompt.invoke({
        "change": change,
        "documentation": documentation
    })

    response = invoke_llm(formatted_prompt)

    return parser.parse(getattr(response, "content", response))
