"""
llm_client.py
=========================================================
Free-tier LLM setup for FitMate AI.

Uses Groq (https://groq.com) which offers a generous FREE API tier with
extremely fast inference on open-weight models such as Llama 3.3 70B.
No paid subscription is required -- only a free Groq API key.
"""

import json
import os
from typing import Any

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from observability import logger, usage

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

if not GROQ_API_KEY:
    raise ValueError(
        "GROQ_API_KEY is missing. Get a FREE key at https://console.groq.com/keys "
        "and add it to your .env file (see .env.example)."
    )

llm = ChatGroq(
    model=GROQ_MODEL,
    api_key=GROQ_API_KEY,
    temperature=0.4,
)

# Groq (and most hosted LLM APIs) occasionally return transient errors --
# rate limits (429), timeouts, or brief 5xx blips. Retrying with exponential
# backoff turns most of these into a 1-2 second delay instead of a hard
# failure surfaced to the end user. This mirrors the resilience pattern the
# original travel-agent project relied on try/except fallbacks for, but adds
# an actual retry instead of immediately giving up.
_RETRYABLE_ERRORS = (Exception,)


@retry(
    retry=retry_if_exception_type(_RETRYABLE_ERRORS),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    stop=stop_after_attempt(3),
    reraise=True,
)
def _invoke_with_retry(messages: list) -> Any:
    return llm.invoke(messages)


def llm_text(system_prompt: str, user_prompt: str) -> str:
    """Call the LLM (with retry/backoff) and return plain text content."""
    usage.total_llm_calls += 1
    try:
        response = _invoke_with_retry(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ]
        )
        return str(response.content)
    except Exception as exc:
        usage.total_errors += 1
        logger.error(
            "LLM call failed after retries",
            extra={"event": "llm_error", "error_type": type(exc).__name__},
        )
        raise


def safe_llm_invoke(messages: list) -> Any:
    """
    Same retry/backoff + usage-counting behavior as llm_text(), but returns
    the raw LangChain message object (used by nodes that need response
    metadata, not just .content).
    """
    usage.total_llm_calls += 1
    try:
        return _invoke_with_retry(messages)
    except Exception as exc:
        usage.total_errors += 1
        logger.error(
            "LLM call failed after retries",
            extra={"event": "llm_error", "error_type": type(exc).__name__},
        )
        raise


def json_from_llm(text: str) -> dict[str, Any]:
    """Extract the first complete JSON object from a model response."""
    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1 or end < start:
        raise ValueError("The model did not return a JSON object.")

    return json.loads(text[start : end + 1])
