"""
app.py
=========================================================
FitMate AI -- FastAPI application layer.

Endpoints
---------
GET  /                          -> UI
POST /api/fitness               -> start a new coaching run (pauses for HITL)
POST /api/fitness/approve       -> resume after human review (approve/revise)
POST /api/progress/log          -> log a completed workout session
GET  /api/progress/{user_id}    -> fetch progress summary + history
GET  /health                    -> health check
"""

import traceback
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

import database as db
from backend import resume_fitness_agent, run_fitness_agent
from observability import logger, usage

# =========================================================
# Rate limiting
# =========================================================
# Every /api/fitness call triggers several LLM calls (guardrail + supervisor
# + specialist agents), so this endpoint is the one worth protecting from
# accidental client-side loops or abuse. Limits are generous defaults for a
# single-user/demo deployment; tune via env vars for production.
limiter = Limiter(key_func=get_remote_address)

# Note: LangGraph agent nodes are synchronous but need to call async MCP
# tools. Rather than relying on nest_asyncio (which cannot patch uvloop,
# uvicorn's default event loop), backend.py runs each MCP call in its own
# isolated thread with a fresh event loop -- see backend._run_async().

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="FitMate AI",
    description=(
        "Multi-Agent Personal Fitness Coach built with LangGraph, MCP, "
        "a Supervisor + Guardrail architecture, and Human-in-the-Loop review."
    ),
    version="1.0.0",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.middleware("http")
async def log_requests(request: Request, call_next):
    import time as _time

    started = _time.perf_counter()
    response = await call_next(request)
    duration_ms = round((_time.perf_counter() - started) * 1000, 1)

    logger.info(
        f"{request.method} {request.url.path} -> {response.status_code}",
        extra={
            "event": "http_request",
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    return response


# =========================================================
# Request/response models
# =========================================================
class FitnessRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    user_id: str = Field(default="", max_length=100)
    thread_id: str | None = None


class ApprovalRequest(BaseModel):
    thread_id: str = Field(min_length=1)
    approved: bool
    feedback: str = Field(default="", max_length=2000)


class WorkoutLogRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=100)
    session_date: str = Field(min_length=1, max_length=40)
    workout_type: str = Field(default="", max_length=100)
    duration_minutes: int | None = None
    completed: bool = True
    difficulty_rating: int | None = Field(default=None, ge=1, le=10)
    energy_level: int | None = Field(default=None, ge=1, le=10)
    notes: str = Field(default="", max_length=1000)


# =========================================================
# UI
# =========================================================
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={})


# =========================================================
# Multi-agent fitness coaching
# =========================================================
@app.post("/api/fitness")
@limiter.limit("10/minute")
async def start_fitness_plan(request: Request, request_data: FitnessRequest):
    try:
        user_message = request_data.message.strip()

        if not user_message:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "Message cannot be empty."},
            )

        result = run_fitness_agent(
            user_input=user_message,
            user_id=request_data.user_id.strip(),
            thread_id=request_data.thread_id,
        )

        return JSONResponse(content={"success": True, **result})

    except Exception as exc:
        print("FITNESS AGENT ERROR:", exc)
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})


@app.post("/api/fitness/approve")
@limiter.limit("20/minute")
async def approve_fitness_plan(request: Request, request_data: ApprovalRequest):
    try:
        if not request_data.approved and not request_data.feedback.strip():
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": "Please provide revision feedback when requesting changes.",
                },
            )

        result = resume_fitness_agent(
            thread_id=request_data.thread_id,
            approved=request_data.approved,
            feedback=request_data.feedback,
        )

        return JSONResponse(content={"success": True, **result})

    except Exception as exc:
        print("APPROVAL ERROR:", exc)
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})


# =========================================================
# Progress tracking (bonus "adaptive" feature)
# =========================================================
@app.post("/api/progress/log")
async def log_workout(request_data: WorkoutLogRequest):
    try:
        result = db.log_workout_session(
            user_id=request_data.user_id.strip(),
            session_date=request_data.session_date.strip(),
            workout_type=request_data.workout_type.strip(),
            duration_minutes=request_data.duration_minutes,
            completed=request_data.completed,
            difficulty_rating=request_data.difficulty_rating,
            energy_level=request_data.energy_level,
            notes=request_data.notes.strip(),
        )
        summary = db.get_progress_summary(request_data.user_id.strip())
        return JSONResponse(content={"success": True, "logged": result, "summary": summary})

    except Exception as exc:
        print("LOG WORKOUT ERROR:", exc)
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})


@app.get("/api/progress/{user_id}")
async def get_progress(user_id: str):
    try:
        summary = db.get_progress_summary(user_id.strip())
        profile = db.get_fitness_profile(user_id.strip())
        return JSONResponse(content={"success": True, "summary": summary, "profile": profile})

    except Exception as exc:
        print("GET PROGRESS ERROR:", exc)
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})


# =========================================================
# Health, metrics & misc
# =========================================================
@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "message": "FitMate AI API is running",
        "features": [
            "supervisor_agent",
            "input_guardrail",
            "human_in_the_loop",
            "exercise_mcp_server",
            "progress_mcp_server",
            "structured_json_logging",
            "per_node_tracing",
            "llm_mcp_retry_backoff",
            "rate_limiting",
        ],
    }


@app.get("/metrics")
async def metrics():
    """
    Coarse, per-process usage counters (requests, LLM calls, MCP calls,
    errors, guardrail blocks). Not a replacement for LangSmith/Prometheus,
    but gives immediate visibility with zero extra infrastructure.
    """
    return {"success": True, "usage": usage.snapshot()}


@app.get("/favicon.ico")
async def favicon():
    return JSONResponse(content={})


if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
