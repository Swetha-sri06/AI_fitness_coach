"""
backend.py
=========================================================
FitMate AI -- Multi-Agent Personal Fitness Coach

Architecture (LangGraph):

    START
      |
      v
   Supervisor  (input guardrail + dynamic agent routing)
      |
      +-- guardrail_blocked --------------------------> END
      |
      v  (dynamic order chosen by the supervisor)
   Workout Agent -> Equipment Agent -> Nutrition Agent -> Progress Agent
      |
      v
   Fitness Plan Agent  (always runs; integrates every selected result)
      |
      v
   Human Approval  (interrupt: approve / request changes)
      |
      v
   Final Agent  (produces the polished final plan, persists it)
      |
      v
     END

Design notes
------------
* The Supervisor + Guardrail step decides which specialist agents are
  actually needed for a given request (dynamic routing), exactly like a
  senior coach delegating to the right specialists.
* Every specialist agent that touches "real" data (exercises, user
  history) calls a local MCP tool first, then asks the LLM to reason
  over that grounded data -- this avoids the LLM hallucinating exercise
  names or fabricating progress history.
* Human-in-the-loop (HITL): the draft plan always pauses for human
  review before becoming final, using LangGraph's `interrupt()`.
* Checkpointing: uses Postgres if DATABASE_URL is set (durable,
  production-style persistence), otherwise falls back automatically to
  an in-memory checkpointer so the project runs with zero setup.
"""

import os
import asyncio
import concurrent.futures
import operator
import uuid
from typing import Annotated, Any, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

import database as db
from llm_client import json_from_llm, llm, llm_text, safe_llm_invoke
from mcp_client import exercise_mcp_call, progress_mcp_call
from observability import log_tracing_status, logger, new_request_id, traced_node, usage

load_dotenv()
log_tracing_status()


# =========================================================
# State
# =========================================================
class FitnessState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], operator.add]
    user_query: str
    user_id: str
    request_id: str

    # Supervisor + guardrail state
    guardrail_allowed: bool
    guardrail_reason: str
    selected_agents: list[str]
    fitness_profile: dict[str, Any]
    supervisor_reasoning: str

    # Specialist agent results
    workout_results: str
    equipment_results: str
    nutrition_results: str
    progress_results: str
    fitness_plan: str

    # HITL + final output
    approval_request: str
    approved: bool
    human_feedback: str
    final_response: str

    llm_calls: int


# =========================================================
# Shared config
# =========================================================
KNOWN_AGENTS = {
    "workout_agent",
    "equipment_agent",
    "nutrition_agent",
    "progress_agent",
    "fitness_plan_agent",
}

AGENT_ORDER = [
    "workout_agent",
    "equipment_agent",
    "nutrition_agent",
    "progress_agent",
    "fitness_plan_agent",
]


def _empty_profile() -> dict[str, Any]:
    return {
        "age": "",
        "goal": "",
        "fitness_level": "",
        "days_per_week": "",
        "session_duration": "",
        "equipment": [],
        "preferences": [],
        "limitations": "",
    }


def _run_async(coro):
    """
    Run an async MCP call from inside a sync LangGraph node.

    FastAPI/uvicorn's default event loop (uvloop) is already running when
    these sync node functions execute, and libraries like nest_asyncio do
    not patch uvloop. To stay reliable regardless of the server's event
    loop implementation, each MCP call is executed inside a brand-new
    thread with its own fresh asyncio event loop.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(asyncio.run, coro)
        return future.result()


# =========================================================
# Supervisor Agent + Input Guardrail
# =========================================================
@traced_node("supervisor")
def supervisor_agent(state: FitnessState):
    query = state["user_query"]
    llm_calls = state.get("llm_calls", 0)

    guardrail_prompt = f"""
Determine whether the following request belongs to personal fitness coaching.
Valid requests include: workout plans, exercise selection, training splits,
fitness goals (weight loss, muscle gain, endurance, general health), available
equipment/resources, time constraints, nutrition/hydration/recovery guidance
related to fitness, and workout progress or motivation.

Block requests that are clearly unrelated to fitness (e.g. coding help,
travel planning, unrelated trivia) and block requests asking for harmful or
medically unsafe instructions (e.g. ignoring a serious injury, extreme unsafe
dieting). Do not block a valid fitness request merely because some details
(like exact equipment) are missing -- assume reasonable defaults instead.

Return strict JSON only:
{{
  "allowed": true,
  "reason": ""
}}

User request:
{query}
"""

    try:
        guardrail_raw = llm_text(
            "You are the input guardrail for an AI personal fitness coach. "
            "Return strict JSON only.",
            guardrail_prompt,
        )
        guardrail_result = json_from_llm(guardrail_raw)
        allowed = bool(guardrail_result.get("allowed", True))
        guardrail_reason = str(guardrail_result.get("reason", "")).strip()
        llm_calls += 1
    except Exception as exc:
        print(f"Guardrail fallback used: {exc}")
        allowed = True
        guardrail_reason = "Guardrail validation fallback allowed the request."

    if not allowed:
        reason = guardrail_reason or (
            "FitMate AI can only help with fitness-coaching requests -- "
            "workouts, exercise selection, equipment, nutrition guidance, "
            "or training progress. Please rephrase your request."
        )
        return {
            "guardrail_allowed": False,
            "guardrail_reason": reason,
            "selected_agents": [],
            "fitness_profile": _empty_profile(),
            "supervisor_reasoning": reason,
            "final_response": reason,
            "messages": [AIMessage(content=f"Guardrail blocked request: {reason}")],
            "llm_calls": llm_calls,
        }

    supervisor_prompt = f"""
You are the supervisor of a multi-agent AI personal fitness coach.
Choose only the specialist agents needed for this request, and extract a
structured fitness profile from what the user shared (leave fields blank
if not mentioned; do not invent values).

Available specialist agents:
- workout_agent: designs the exercise plan (sets, reps, structure)
- equipment_agent: maps exercises to the user's available equipment/resources
- nutrition_agent: general nutrition, hydration, sleep, and recovery guidance
- progress_agent: analyzes workout history/adherence and adjusts intensity
- fitness_plan_agent: creates the final integrated plan and must always be included

Return strict JSON only using this schema:
{{
  "selected_agents": ["workout_agent", "equipment_agent", "nutrition_agent", "progress_agent", "fitness_plan_agent"],
  "fitness_profile": {{
    "age": "",
    "goal": "",
    "fitness_level": "",
    "days_per_week": "",
    "session_duration": "",
    "equipment": [],
    "preferences": [],
    "limitations": ""
  }},
  "reasoning": ""
}}

User request:
{query}
"""

    try:
        supervisor_raw = llm_text(
            "You route work to fitness-coaching specialist agents. Return strict JSON only.",
            supervisor_prompt,
        )
        parsed = json_from_llm(supervisor_raw)
        requested_agents = parsed.get("selected_agents", [])
        selected_agents = [
            name for name in AGENT_ORDER if name in requested_agents and name in KNOWN_AGENTS
        ]

        # The fitness plan agent integrates whichever specialist results exist.
        if "fitness_plan_agent" not in selected_agents:
            selected_agents.append("fitness_plan_agent")

        profile = _empty_profile()
        parsed_profile = parsed.get("fitness_profile", {})
        if isinstance(parsed_profile, dict):
            profile.update(parsed_profile)

        reasoning = str(parsed.get("reasoning", "")).strip()
        llm_calls += 1
    except Exception as exc:
        print(f"Supervisor fallback used: {exc}")
        selected_agents = AGENT_ORDER.copy()
        profile = _empty_profile()
        reasoning = (
            "Supervisor parsing failed, so the full fitness-coaching workflow "
            "was selected as a safe fallback."
        )

    return {
        "guardrail_allowed": True,
        "guardrail_reason": guardrail_reason,
        "selected_agents": selected_agents,
        "fitness_profile": profile,
        "supervisor_reasoning": reasoning,
        "messages": [AIMessage(content="Supervisor created the agent plan.")],
        "llm_calls": llm_calls,
    }


# =========================================================
# Guardrail blocked response
# =========================================================
@traced_node("guardrail_blocked")
def guardrail_blocked_agent(state: FitnessState):
    reason = (
        state.get("final_response")
        or state.get("guardrail_reason")
        or "This request was blocked by the fitness-coaching input guardrail."
    )
    return {
        "final_response": reason,
        "messages": [AIMessage(content=reason)],
    }


# =========================================================
# Workout Agent -- uses the Exercise Database MCP server
# =========================================================
@traced_node("workout_agent")
def workout_agent(state: FitnessState):
    profile = state.get("fitness_profile", {})

    try:
        candidate_exercises = _run_async(
            exercise_mcp_call(
                "search_exercises",
                {
                    "equipment": (profile.get("equipment") or [""])[0].lower().replace(" ", "_")
                    if profile.get("equipment")
                    else "",
                    "difficulty": str(profile.get("fitness_level", "")).lower(),
                    "limit": 20,
                },
            )
        )
    except Exception as exc:
        print(f"WORKOUT AGENT MCP ERROR: {type(exc).__name__}: {exc}", flush=True)
        candidate_exercises = []

    prompt = f"""
Design a workout plan using the exercise catalog provided below. Prefer
exercises from this catalog so the plan uses real, safe, well-described
movements. You may add a small number of well-known exercises only if the
catalog is insufficient for the goal.

User Request:
{state['user_query']}

Fitness Profile:
{profile}

Exercise Catalog (from the exercise database):
{candidate_exercises if candidate_exercises else "No catalog matches were returned; use general safe exercise knowledge."}

Produce a day-by-day weekly workout split appropriate for the days-per-week
and session duration. For each exercise include sets, reps, and rest time.
Keep total session time realistic for the stated duration.
"""

    try:
        response = safe_llm_invoke(
            [
                SystemMessage(content="You are an expert strength & conditioning coach."),
                HumanMessage(content=prompt),
            ]
        )
        workout_data = response.content
    except Exception as exc:
        workout_data = f"Workout plan generation encountered an issue: {exc}"

    return {
        "workout_results": workout_data,
        "messages": [AIMessage(content="Workout plan drafted.")],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


# =========================================================
# Equipment Agent -- uses the Exercise Database MCP server
# =========================================================
@traced_node("equipment_agent")
def equipment_agent(state: FitnessState):
    profile = state.get("fitness_profile", {})
    equipment_list = profile.get("equipment") or ["none"]

    equipment_matches: dict[str, Any] = {}
    for item in equipment_list[:4]:
        key = str(item).lower().strip().replace(" ", "_") or "none"
        try:
            equipment_matches[key] = _run_async(
                exercise_mcp_call("search_exercises", {"equipment": key, "limit": 8})
            )
        except Exception as exc:
            print(f"EQUIPMENT AGENT MCP ERROR: {type(exc).__name__}: {exc}", flush=True)
            equipment_matches[key] = []

    prompt = f"""
The user has access to this equipment: {equipment_list}

Matching exercises found in the exercise database:
{equipment_matches}

Write a short, clear "Equipment Guide" section that:
1. Lists exercises that work well with the available equipment (use the
   database matches above where possible).
2. Lists common exercises the user should AVOID because they require
   equipment they don't have.
3. Suggests simple at-home substitutes if useful equipment is missing.
"""

    try:
        response = safe_llm_invoke(
            [
                SystemMessage(content="You are a practical fitness equipment specialist."),
                HumanMessage(content=prompt),
            ]
        )
        equipment_data = response.content
    except Exception as exc:
        equipment_data = f"Equipment guidance encountered an issue: {exc}"

    return {
        "equipment_results": equipment_data,
        "messages": [AIMessage(content="Equipment guidance generated.")],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


# =========================================================
# Nutrition / Lifestyle Agent
# =========================================================
@traced_node("nutrition_agent")
def nutrition_agent(state: FitnessState):
    profile = state.get("fitness_profile", {})

    prompt = f"""
Provide general nutrition, hydration, sleep, and recovery guidance for this
fitness profile:
{profile}

User Request:
{state['user_query']}

Cover, briefly:
1. Protein / macronutrient guidance appropriate for the goal
2. Hydration
3. Sleep & recovery
4. Pre/post-workout nutrition suggestions

Important: this is general wellness guidance, NOT medical or dietetic advice.
End with a short, clear disclaimer recommending a doctor or registered
dietitian for personalized medical or nutrition needs.
"""

    try:
        response = safe_llm_invoke(
            [
                SystemMessage(content="You are a supportive fitness lifestyle & nutrition coach."),
                HumanMessage(content=prompt),
            ]
        )
        nutrition_data = response.content
    except Exception as exc:
        nutrition_data = f"Nutrition guidance encountered an issue: {exc}"

    return {
        "nutrition_results": nutrition_data,
        "messages": [AIMessage(content="Nutrition & recovery guidance generated.")],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


# =========================================================
# Progress Agent -- uses the Fitness Progress MCP server
# =========================================================
@traced_node("progress_agent")
def progress_agent(state: FitnessState):
    user_id = state.get("user_id", "").strip()

    history: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}

    if user_id:
        try:
            history = _run_async(progress_mcp_call("get_workout_history", {"user_id": user_id, "limit": 10}))
            summary = _run_async(progress_mcp_call("get_progress_summary", {"user_id": user_id}))
        except Exception as exc:
            print(f"PROGRESS AGENT MCP ERROR: {type(exc).__name__}: {exc}", flush=True)

    if not user_id or not history:
        progress_data = (
            "No prior workout history was found for this user yet. "
            "Once workouts are logged (see the Progress Tracker panel), "
            "future plans will automatically adapt intensity, volume, and "
            "exercise selection based on completion rate, reported difficulty, "
            "and energy levels."
        )
        return {
            "progress_results": progress_data,
            "messages": [AIMessage(content="No progress history available yet.")],
        }

    prompt = f"""
Analyze this user's real logged workout history and progress summary, then
recommend specific adjustments for their next training block.

User Request:
{state['user_query']}

Progress Summary:
{summary}

Recent Sessions:
{history}

Return:
1. A short adherence assessment (are they completing workouts consistently?)
2. Whether intensity/volume should increase, decrease, or stay the same, and why
3. Any specific exercise substitutions if difficulty/energy patterns suggest fatigue
4. A short motivational note that references their actual progress (not generic).
"""

    try:
        response = safe_llm_invoke(
            [
                SystemMessage(content="You are an encouraging, data-driven fitness progress coach."),
                HumanMessage(content=prompt),
            ]
        )
        progress_data = response.content
    except Exception as exc:
        progress_data = f"Progress analysis encountered an issue: {exc}"

    return {
        "progress_results": progress_data,
        "messages": [AIMessage(content="Progress analysis generated.")],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


# =========================================================
# Fitness Plan Agent -- always runs, integrates everything
# =========================================================
@traced_node("fitness_plan_agent")
def fitness_plan_agent(state: FitnessState):
    prompt = f"""
Create a complete, integrated personalized fitness plan.

User Request:
{state['user_query']}

Fitness Profile:
{state.get('fitness_profile', {})}

Workout Plan:
{state.get('workout_results', '')}

Equipment Guidance:
{state.get('equipment_results', '')}

Nutrition & Recovery Guidance:
{state.get('nutrition_results', '')}

Progress Analysis:
{state.get('progress_results', '')}

Format the plan clearly using these sections (use markdown headings):
## Fitness Profile Summary
## Weekly Workout Plan
## Equipment Guide
## Nutrition & Recovery
## Progress & Motivation
## Safety Notes

Keep it practical, encouraging, and easy to follow. Create a clear draft
that is ready for human review before being finalized.
"""

    try:
        response = safe_llm_invoke(
            [
                SystemMessage(content="You are an expert AI personal fitness coach."),
                HumanMessage(content=prompt),
            ]
        )
        plan_text = response.content
    except Exception as exc:
        plan_text = f"Fitness plan generation encountered an issue: {exc}"

    approval_request = (
        "Please review your generated fitness plan. Approve it to finalize, "
        "or request changes (e.g. \"I only have 30 minutes\" or \"replace push-ups\")."
    )

    return {
        "fitness_plan": plan_text,
        "approval_request": approval_request,
        "messages": [AIMessage(content="Draft fitness plan created for human review.")],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


# =========================================================
# Human-in-the-Loop approval
# =========================================================
@traced_node("human_approval")
def human_approval_agent(state: FitnessState):
    # Do not wrap interrupt() in try/except -- LangGraph uses it to pause execution.
    review = interrupt(
        {
            "question": "Do you approve this fitness plan?",
            "draft_plan": state.get("fitness_plan", ""),
            "approval_request": state.get("approval_request", ""),
            "selected_agents": state.get("selected_agents", []),
            "supervisor_reasoning": state.get("supervisor_reasoning", ""),
            "expected_response": {
                "approved": True,
                "feedback": "Optional revision feedback",
            },
        }
    )

    approved = bool(review.get("approved", False))
    human_feedback = str(review.get("feedback", "")).strip()

    return {
        "approved": approved,
        "human_feedback": human_feedback,
        "messages": [AIMessage(content="Human approval step completed.")],
    }


# =========================================================
# Final Agent -- polishes the plan and persists it
# =========================================================
@traced_node("final_agent")
def final_agent(state: FitnessState):
    if state.get("approved", False):
        review_instruction = "The user approved the draft. Preserve its decisions while polishing it."
    else:
        review_instruction = f"""
The user requested a revision. Apply this feedback carefully:
{state.get('human_feedback', '') or 'Improve the draft before finalizing it.'}
"""

    final_prompt = f"""
Generate the final fitness coaching response for the user.

Human Review:
{review_instruction}

User Request:
{state['user_query']}

Fitness Profile:
{state.get('fitness_profile', {})}

Workout Plan:
{state.get('workout_results', '')}

Equipment Guidance:
{state.get('equipment_results', '')}

Nutrition & Recovery Guidance:
{state.get('nutrition_results', '')}

Progress Analysis:
{state.get('progress_results', '')}

Draft Fitness Plan:
{state.get('fitness_plan', '')}

Format the final answer using these markdown sections:
## Fitness Profile Summary
## Weekly Workout Plan
## Equipment Guide
## Nutrition & Recovery
## Progress & Motivation
## Safety Notes

Important:
- Be clear and practical.
- Include a brief safety disclaimer that this is general guidance, not
  medical advice, and to consult a doctor for injuries or medical conditions.
- Incorporate the human feedback when a revision was requested.
"""

    try:
        response = safe_llm_invoke(
            [
                SystemMessage(content="You are a professional AI personal fitness coach."),
                HumanMessage(content=final_prompt),
            ]
        )
        final_text = response.content
    except Exception as exc:
        final_text = state.get("fitness_plan", "") or f"Final plan generation encountered an issue: {exc}"

    # Persist the profile + plan so the Progress Agent has data for next time.
    user_id = state.get("user_id", "").strip()
    if user_id:
        try:
            db.save_fitness_profile(user_id, state.get("fitness_profile", {}))
            db.save_workout_plan(user_id, str(final_text))
        except Exception as exc:
            print(f"PERSISTENCE WARNING: {type(exc).__name__}: {exc}", flush=True)

    return {
        "final_response": final_text,
        "messages": [response] if not isinstance(final_text, str) else [AIMessage(content=str(final_text))],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


# =========================================================
# Dynamic Supervisor Routing
# =========================================================
ROUTE_MAP = {
    "guardrail_blocked": "guardrail_blocked",
    "workout_agent": "workout_agent",
    "equipment_agent": "equipment_agent",
    "nutrition_agent": "nutrition_agent",
    "progress_agent": "progress_agent",
    "fitness_plan_agent": "fitness_plan_agent",
}


def _selected_agents(state: FitnessState) -> list[str]:
    selected = state.get("selected_agents", [])
    return [agent for agent in AGENT_ORDER if agent in selected]


def route_from_supervisor(state: FitnessState) -> str:
    if not state.get("guardrail_allowed", True):
        return "guardrail_blocked"

    selected = _selected_agents(state)
    return selected[0] if selected else "fitness_plan_agent"


def route_after_agent(current_agent: str):
    def route(state: FitnessState) -> str:
        selected = _selected_agents(state)
        current_index = AGENT_ORDER.index(current_agent)

        for next_agent in AGENT_ORDER[current_index + 1 :]:
            if next_agent in selected:
                return next_agent

        return "fitness_plan_agent"

    return route


# =========================================================
# Build Graph
# =========================================================
graph = StateGraph(FitnessState)

graph.add_node("supervisor", supervisor_agent)
graph.add_node("guardrail_blocked", guardrail_blocked_agent)
graph.add_node("workout_agent", workout_agent)
graph.add_node("equipment_agent", equipment_agent)
graph.add_node("nutrition_agent", nutrition_agent)
graph.add_node("progress_agent", progress_agent)
graph.add_node("fitness_plan_agent", fitness_plan_agent)
graph.add_node("human_approval", human_approval_agent)
graph.add_node("final_agent", final_agent)

graph.add_edge(START, "supervisor")
graph.add_conditional_edges("supervisor", route_from_supervisor, ROUTE_MAP)

graph.add_conditional_edges("workout_agent", route_after_agent("workout_agent"), ROUTE_MAP)
graph.add_conditional_edges("equipment_agent", route_after_agent("equipment_agent"), ROUTE_MAP)
graph.add_conditional_edges("nutrition_agent", route_after_agent("nutrition_agent"), ROUTE_MAP)
graph.add_conditional_edges("progress_agent", route_after_agent("progress_agent"), ROUTE_MAP)

graph.add_edge("fitness_plan_agent", "human_approval")
graph.add_edge("human_approval", "final_agent")
graph.add_edge("final_agent", END)
graph.add_edge("guardrail_blocked", END)


# =========================================================
# Checkpointer -- Postgres if configured, otherwise in-memory
# =========================================================
def _build_checkpointer():
    database_url = os.getenv("DATABASE_URL", "").strip()

    if database_url:
        try:
            import psycopg
            from psycopg.rows import dict_row
            from langgraph.checkpoint.postgres import PostgresSaver

            if "sslmode=" not in database_url:
                separator = "&" if "?" in database_url else "?"
                database_url = f"{database_url}{separator}sslmode=require"

            conn = psycopg.connect(database_url, autocommit=True, row_factory=dict_row)
            checkpointer = PostgresSaver(conn)
            checkpointer.setup()
            print("FitMate AI: using PostgreSQL checkpointing (durable).")
            return checkpointer
        except Exception as exc:
            print(
                f"FitMate AI: could not connect to DATABASE_URL ({exc}); "
                "falling back to in-memory checkpointing."
            )

    from langgraph.checkpoint.memory import MemorySaver

    print("FitMate AI: using in-memory checkpointing (zero-setup demo mode).")
    return MemorySaver()


checkpointer = _build_checkpointer()
fitness_graph = graph.compile(checkpointer=checkpointer)


# =========================================================
# FastAPI-facing helpers
# =========================================================
def _interrupt_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    interrupts = result.get("__interrupt__", [])
    if not interrupts:
        return None

    first_interrupt = interrupts[0]
    payload = getattr(first_interrupt, "value", first_interrupt)
    return payload if isinstance(payload, dict) else {"value": payload}


def _serialize_result(result: dict[str, Any], thread_id: str) -> dict[str, Any]:
    messages = result.get("messages", [])
    last_message = messages[-1].content if messages else ""
    answer = result.get("final_response") or last_message
    interrupt_payload = _interrupt_payload(result)

    if interrupt_payload:
        answer = interrupt_payload.get("draft_plan") or result.get("fitness_plan", "")

    return {
        "thread_id": thread_id,
        "answer": answer,
        "requires_approval": interrupt_payload is not None,
        "approval_request": (
            interrupt_payload.get("approval_request", "")
            if interrupt_payload
            else result.get("approval_request", "")
        ),
        "workout_results": result.get("workout_results", ""),
        "equipment_results": result.get("equipment_results", ""),
        "nutrition_results": result.get("nutrition_results", ""),
        "progress_results": result.get("progress_results", ""),
        "fitness_plan": (
            interrupt_payload.get("draft_plan", "")
            if interrupt_payload
            else result.get("fitness_plan", "")
        ),
        "selected_agents": result.get("selected_agents", []),
        "fitness_profile": result.get("fitness_profile", {}),
        "supervisor_reasoning": result.get("supervisor_reasoning", ""),
        "guardrail_allowed": result.get("guardrail_allowed", True),
        "guardrail_reason": result.get("guardrail_reason", ""),
        "approved": result.get("approved"),
        "human_feedback": result.get("human_feedback", ""),
        "llm_calls": result.get("llm_calls", 0),
    }


def run_fitness_agent(user_input: str, user_id: str = "", thread_id: str | None = None):
    """Start a new fitness-coaching run and pause at human approval."""
    if not thread_id:
        thread_id = f"user_{uuid.uuid4().hex}"

    request_id = new_request_id()
    usage.total_requests += 1
    logger.info(
        "fitness run started",
        extra={
            "event": "run_start",
            "request_id": request_id,
            "thread_id": thread_id,
            "has_user_id": bool(user_id),
        },
    )

    config = {"configurable": {"thread_id": thread_id}}

    result = fitness_graph.invoke(
        {
            "messages": [HumanMessage(content=user_input)],
            "user_query": user_input,
            "user_id": user_id or "",
            "request_id": request_id,
            "guardrail_allowed": True,
            "guardrail_reason": "",
            "selected_agents": [],
            "fitness_profile": _empty_profile(),
            "supervisor_reasoning": "",
            "workout_results": "",
            "equipment_results": "",
            "nutrition_results": "",
            "progress_results": "",
            "fitness_plan": "",
            "approval_request": "",
            "approved": False,
            "human_feedback": "",
            "final_response": "",
            "llm_calls": 0,
        },
        config=config,
    )

    if not result.get("guardrail_allowed", True):
        usage.guardrail_blocked += 1

    logger.info(
        "fitness run paused/completed",
        extra={
            "event": "run_end",
            "request_id": request_id,
            "thread_id": thread_id,
            "requires_approval": bool(result.get("__interrupt__")),
            "guardrail_allowed": result.get("guardrail_allowed", True),
            "llm_calls": result.get("llm_calls", 0),
        },
    )

    return _serialize_result(result, thread_id)


def resume_fitness_agent(thread_id: str, approved: bool, feedback: str = ""):
    """Resume the paused LangGraph thread after human review."""
    if not thread_id:
        raise ValueError("thread_id is required to resume a fitness plan.")

    logger.info(
        "fitness run resumed",
        extra={"event": "run_resume", "thread_id": thread_id, "approved": approved},
    )

    config = {"configurable": {"thread_id": thread_id}}
    result = fitness_graph.invoke(
        Command(resume={"approved": approved, "feedback": feedback.strip()}),
        config=config,
    )

    return _serialize_result(result, thread_id)
