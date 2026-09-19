from typing import Literal

from pydantic import BaseModel

from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import PydanticOutputParser

try:
    from .llm import invoke_llm
except ImportError:
    from llm import invoke_llm


class ChangeClassification(BaseModel):
    classification: Literal["breaking", "non-breaking"]
    severity: Literal["low", "medium", "high", "critical"]
    reason: str


parser = PydanticOutputParser(
    pydantic_object=ChangeClassification
)


prompt = PromptTemplate(
    template="""
You are an API compatibility analyst.

Analyze the following API change:

{change}

Determine whether this change is breaking or non-breaking.

Assign an appropriate severity.

Return ONLY the structured JSON required by the format instructions.

{format_instructions}
""",
    input_variables=["change"],
    partial_variables={
        "format_instructions": parser.get_format_instructions()
    }
)


def classify_change(change):

    formatted_prompt = prompt.invoke({
        "change": change
    })

    response = invoke_llm(formatted_prompt)

    return parser.parse(getattr(response, "content", response))
