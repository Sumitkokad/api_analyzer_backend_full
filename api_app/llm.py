from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Any

from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TRUE_VALUES = {"1", "true", "yes", "on"}

DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
DEFAULT_HF_MODEL = "openai/gpt-oss-120b"
DEFAULT_LLM_TIMEOUT = 30

# These fields are intentionally NOT allowed from the LLM.
# Classification/severity are owned by breaking_change_rules.py.
DISALLOWED_LLM_FIELDS = {
    "classification",
    "severity",
    "gate_status",
    "rules_version",
    "rule_id",
}

ALLOWED_LLM_FIELDS = {
    "status",
    "explanation",
    "impact",
    "recommendation",
    "evidence",
    "confidence",
    "provider",
}


# ---------------------------------------------------------------------------
# Lazy provider state
# ---------------------------------------------------------------------------

groq_model = None
hf_model = None


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in TRUE_VALUES


def _llm_enabled() -> bool:
    """
    LLM is opt-in.

    The deterministic analyzer must work when this is false.
    """
    if _env_enabled("API_ANALYZER_DISABLE_LLM"):
        return False

    return _env_enabled("API_ANALYZER_ENABLE_LLM")


def _get_timeout() -> int:
    try:
        value = int(
            os.getenv(
                "API_ANALYZER_LLM_TIMEOUT_SECONDS",
                str(DEFAULT_LLM_TIMEOUT),
            )
        )
        return max(1, min(value, 300))
    except (TypeError, ValueError):
        return DEFAULT_LLM_TIMEOUT


# ---------------------------------------------------------------------------
# Provider creation
# ---------------------------------------------------------------------------

def get_groq_model():
    """
    Lazily create the Groq model.

    Importing this module must not require LangChain/Groq to be available
    when the deterministic analyzer is running with LLM disabled.
    """
    global groq_model

    if groq_model is not None:
        return groq_model

    try:
        from langchain_groq import ChatGroq
    except ImportError as exc:
        raise RuntimeError(
            "Groq provider is unavailable. Install langchain-groq."
        ) from exc

    groq_model = ChatGroq(
        model=os.getenv("API_ANALYZER_GROQ_MODEL", DEFAULT_GROQ_MODEL),
        temperature=0,
        timeout=_get_timeout(),
    )

    return groq_model


def get_hf_model():
    """
    Lazily create the Hugging Face model.
    """
    global hf_model

    if hf_model is not None:
        return hf_model

    try:
        from langchain_huggingface import (
            ChatHuggingFace,
            HuggingFaceEndpoint,
        )
    except ImportError as exc:
        raise RuntimeError(
            "Hugging Face provider is unavailable. "
            "Install langchain-huggingface."
        ) from exc

    hf_llm = HuggingFaceEndpoint(
        repo_id=os.getenv("API_ANALYZER_HF_MODEL", DEFAULT_HF_MODEL),
        task="text-generation",
        temperature=0,
        timeout=_get_timeout(),
    )

    hf_model = ChatHuggingFace(llm=hf_llm)

    return hf_model


# ---------------------------------------------------------------------------
# Rule-result helpers
# ---------------------------------------------------------------------------

def _get_value(obj: Any, key: str, default: Any = None) -> Any:
    """
    Read values from dicts, Pydantic models, dataclasses, or simple objects.
    """
    if obj is None:
        return default

    if isinstance(obj, dict):
        return obj.get(key, default)

    return getattr(obj, key, default)


def build_rule_based_explanation(rule_result: Any) -> str:
    """
    Build deterministic fallback explanation.

    This function does not ask an LLM anything.
    """
    classification = _get_value(
        rule_result,
        "classification",
        "potentially-breaking",
    )

    severity = _get_value(
        rule_result,
        "severity",
        "medium",
    )

    reason = _get_value(
        rule_result,
        "reason",
        "The change requires compatibility review.",
    )

    remediation_hint = _get_value(
        rule_result,
        "remediation_hint",
        None,
    )

    parts = [
        f"Deterministic compatibility result: "
        f"{classification} ({severity}).",
        str(reason),
    ]

    if remediation_hint:
        parts.append(f"Recommended action: {remediation_hint}")

    return " ".join(parts)


def _skipped_payload(rule_result: Any = None) -> dict[str, Any]:
    """
    Response used when the LLM is unavailable or disabled.

    IMPORTANT:
    classification/severity are deliberately absent from the structured
    LLM payload. They belong to the deterministic rule engine.
    """
    explanation = (
        build_rule_based_explanation(rule_result)
        if rule_result is not None
        else (
            "LLM explanation was skipped. "
            "Use the deterministic compatibility result as the source of truth."
        )
    )

    return {
        "status": "skipped",
        "explanation": explanation,
        "impact": "",
        "recommendation": "",
        "evidence": [],
        "confidence": None,
        "provider": None,
    }


# ---------------------------------------------------------------------------
# LLM response sanitization
# ---------------------------------------------------------------------------

def sanitize_llm_payload(payload: Any) -> dict[str, Any]:
    """
    Sanitize a model-generated JSON object.

    The LLM is allowed to explain a deterministic result, but it is never
    allowed to override classification/severity/gate decisions.
    """
    if not isinstance(payload, dict):
        return {
            "status": "completed",
            "explanation": str(payload),
            "impact": "",
            "recommendation": "",
            "evidence": [],
            "confidence": None,
            "provider": "unknown",
        }

    sanitized: dict[str, Any] = {}

    for key in ALLOWED_LLM_FIELDS:
        if key in payload:
            sanitized[key] = payload[key]

    # Explicitly discard authoritative fields.
    for key in DISALLOWED_LLM_FIELDS:
        sanitized.pop(key, None)

    sanitized.setdefault("status", "completed")
    sanitized.setdefault("explanation", "")
    sanitized.setdefault("impact", "")
    sanitized.setdefault("recommendation", "")
    sanitized.setdefault("evidence", [])
    sanitized.setdefault("confidence", None)
    sanitized.setdefault("provider", "unknown")

    return sanitized


def _response_to_text(response: Any) -> str:
    """
    Extract model text from common LangChain response shapes.
    """
    content = getattr(response, "content", response)

    if isinstance(content, list):
        parts: list[str] = []

        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))
            else:
                parts.append(str(item))

        return "\n".join(parts)

    return str(content)


def parse_llm_response(
    response: Any,
    *,
    provider: str | None = None,
) -> dict[str, Any]:
    """
    Convert an LLM response into the safe explanation-only schema.

    Invalid/non-JSON responses are retained as plain explanation text.
    """
    text = _response_to_text(response).strip()

    if not text:
        payload = {
            "status": "completed",
            "explanation": "",
            "impact": "",
            "recommendation": "",
            "evidence": [],
            "confidence": None,
            "provider": provider or "unknown",
        }
        return payload

    try:
        parsed = json.loads(text)

        if isinstance(parsed, dict):
            payload = sanitize_llm_payload(parsed)
        else:
            payload = sanitize_llm_payload({"explanation": text})

    except (TypeError, ValueError, json.JSONDecodeError):
        payload = sanitize_llm_payload(
            {
                "explanation": text,
            }
        )

    if provider:
        payload["provider"] = provider

    return payload


# ---------------------------------------------------------------------------
# Main invocation
# ---------------------------------------------------------------------------

def invoke_llm(
    prompt: str,
    rule_result: Any = None,
):
    """
    Invoke an explanation-only LLM.

    Backward compatible:
        invoke_llm(prompt)

    Enhanced:
        invoke_llm(prompt, rule_result)

    The deterministic rule result is never replaced by model output.
    """
    if not _llm_enabled():
        return SimpleNamespace(
            content=json.dumps(
                _skipped_payload(rule_result),
                ensure_ascii=False,
            )
        )

    groq_error: Exception | None = None

    # -----------------------------------------------------------------------
    # Provider 1: Groq
    # -----------------------------------------------------------------------
    try:
        print("Trying Groq...")

        response = get_groq_model().invoke(prompt)

        payload = parse_llm_response(
            response,
            provider="groq",
        )

        return SimpleNamespace(
            content=json.dumps(
                payload,
                ensure_ascii=False,
            )
        )

    except Exception as exc:
        groq_error = exc
        print("Groq failed. Switching to Hugging Face...")

    # -----------------------------------------------------------------------
    # Provider 2: Hugging Face
    # -----------------------------------------------------------------------
    try:
        print("Trying Hugging Face...")

        response = get_hf_model().invoke(prompt)

        payload = parse_llm_response(
            response,
            provider="huggingface",
        )

        return SimpleNamespace(
            content=json.dumps(
                payload,
                ensure_ascii=False,
            )
        )

    except Exception as hf_error:
        raise RuntimeError(
            "Both LLM providers failed. "
            "Deterministic compatibility analysis remains authoritative. "
            f"Groq error: {groq_error}; "
            f"Hugging Face error: {hf_error}"
        ) from hf_error


__all__ = [
    "get_groq_model",
    "get_hf_model",
    "invoke_llm",
    "parse_llm_response",
    "sanitize_llm_payload",
    "build_rule_based_explanation",
]