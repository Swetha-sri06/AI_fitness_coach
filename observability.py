"""
observability.py
=========================================================
Lightweight observability layer for FitMate AI.

What this gives you (no paid service required):

1. Structured JSON logs -- every request and every LangGraph node emits one
   JSON line with a request_id, node name, duration_ms, and outcome. This is
   the same "one JSON object per event" pattern used in production services
   and is easy to pipe into CloudWatch/Datadog/ELK later.

2. A `request_id` that is generated per HTTP request and threaded through
   the LangGraph state, so every log line for a single run (across all
   agents) can be correlated -- essential once more than one user is
   hitting the API at once.

3. Per-node timing + LLM/MCP call counters, so you can see exactly which
   agent is slow or which agent is failing, instead of one opaque
   "final_response" blob.

4. Optional LangSmith tracing: if LANGCHAIN_TRACING_V2=true and
   LANGCHAIN_API_KEY are set in the environment, LangGraph/LangChain will
   automatically send full traces (prompts, tool calls, latencies) to
   LangSmith. This module does not require LangSmith -- it just makes sure
   the env vars are read consistently and logs whether tracing is active.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import sys
import time
import uuid
from typing import Any, Callable

# =========================================================
# Structured (JSON) logging setup
# =========================================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Anything passed via logger.info("msg", extra={...}) shows up here.
        for key, value in record.__dict__.items():
            if key in (
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message",
                "taskName",
            ):
                continue
            payload[key] = value

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("fitmate")
    if logger.handlers:
        return logger

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(LOG_LEVEL)
    logger.propagate = False
    return logger


logger = _build_logger()


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


# =========================================================
# LangSmith tracing (optional, zero-config if unset)
# =========================================================
def tracing_enabled() -> bool:
    return os.getenv("LANGCHAIN_TRACING_V2", "").lower() == "true" and bool(
        os.getenv("LANGCHAIN_API_KEY")
    )


def log_tracing_status() -> None:
    if tracing_enabled():
        logger.info(
            "LangSmith tracing is ENABLED",
            extra={
                "event": "tracing_status",
                "enabled": True,
                "project": os.getenv("LANGCHAIN_PROJECT", "fitmate-ai"),
            },
        )
    else:
        logger.info(
            "LangSmith tracing is disabled (set LANGCHAIN_TRACING_V2=true "
            "and LANGCHAIN_API_KEY to enable)",
            extra={"event": "tracing_status", "enabled": False},
        )


# =========================================================
# Per-node timing decorator for LangGraph nodes
# =========================================================
def traced_node(node_name: str) -> Callable:
    """
    Wrap a LangGraph node function so every invocation logs:
      - node_name
      - request_id (pulled from state, if present)
      - duration_ms
      - success/failure
      - a few safe summary fields from the returned state delta
        (never full prompt/response text, to keep logs small + safe)
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(state: dict[str, Any]) -> dict[str, Any]:
            request_id = state.get("request_id", "unknown")
            started = time.perf_counter()

            logger.info(
                f"node started: {node_name}",
                extra={
                    "event": "node_start",
                    "node": node_name,
                    "request_id": request_id,
                },
            )

            try:
                result = fn(state)
                duration_ms = round((time.perf_counter() - started) * 1000, 1)

                logger.info(
                    f"node completed: {node_name}",
                    extra={
                        "event": "node_end",
                        "node": node_name,
                        "request_id": request_id,
                        "duration_ms": duration_ms,
                        "outcome": "success",
                    },
                )
                return result

            except Exception as exc:
                duration_ms = round((time.perf_counter() - started) * 1000, 1)
                logger.error(
                    f"node failed: {node_name}",
                    extra={
                        "event": "node_end",
                        "node": node_name,
                        "request_id": request_id,
                        "duration_ms": duration_ms,
                        "outcome": "error",
                        "error_type": type(exc).__name__,
                    },
                    exc_info=True,
                )
                raise

        return wrapper

    return decorator


# =========================================================
# Simple in-process usage counters (per-process, resets on restart)
# =========================================================
class UsageCounters:
    """
    Tracks coarse usage so /metrics (or logs) can answer "how much are we
    using the LLM / MCP servers" without needing an external metrics stack.
    Thread-safety isn't critical here since uvicorn workers each keep their
    own counters and this is best-effort visibility, not billing.
    """

    def __init__(self) -> None:
        self.total_requests = 0
        self.total_llm_calls = 0
        self.total_mcp_calls = 0
        self.total_errors = 0
        self.guardrail_blocked = 0

    def snapshot(self) -> dict[str, int]:
        return {
            "total_requests": self.total_requests,
            "total_llm_calls": self.total_llm_calls,
            "total_mcp_calls": self.total_mcp_calls,
            "total_errors": self.total_errors,
            "guardrail_blocked": self.guardrail_blocked,
        }


usage = UsageCounters()
