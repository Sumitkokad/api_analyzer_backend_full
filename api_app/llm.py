import json
import os
from types import SimpleNamespace

from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_huggingface import ChatHuggingFace, HuggingFaceEndpoint

load_dotenv()

groq_model = None
hf_model = None


def get_groq_model():
    global groq_model
    if groq_model is None:
        groq_model = ChatGroq(
            model="openai/gpt-oss-120b",
            temperature=0
        )
    return groq_model


def get_hf_model():
    global hf_model
    if hf_model is None:
        hf_llm = HuggingFaceEndpoint(
            repo_id="openai/gpt-oss-120b",
            task="text-generation",
            temperature=0
        )
        hf_model = ChatHuggingFace(llm=hf_llm)
    return hf_model


def invoke_llm(prompt):
    if os.getenv("API_ANALYZER_ENABLE_LLM", "").lower() not in {"1", "true", "yes"}:
        return SimpleNamespace(content=json.dumps({
            "classification": "non-breaking",
            "severity": "low",
            "reason": "LLM execution is disabled for local/offline execution.",
            "affected_components": [],
            "impact": "No live model was called. Enable API_ANALYZER_ENABLE_LLM=true to use the configured providers.",
            "recommendation": "Run deterministic comparison locally, then enable LLM providers when credentials and network access are available.",
            "evidence": [],
            "confidence": 0.5,
        }))

    try:
        print("Trying Groq...")
        return get_groq_model().invoke(prompt)

    except Exception as groq_error:
        print("Groq failed. Switching to Hugging Face...")

        try:
            print("Trying Hugging Face...")
            return get_hf_model().invoke(prompt)

        except Exception as hf_error:
            raise RuntimeError(
                f"Both LLM providers failed.\n"
                f"Groq error: {groq_error}\n"
                f"Hugging Face error: {hf_error}"
            )
