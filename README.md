## Parity update (this build)

This build was diffed against the original travel-planner project
(Multi-Agent LangGraph / MCP / Supervisor / Guardrails / HITL) to close the
gaps in **observability**, **resilience**, and **usage control** that the
travel project didn't have either, on top of the supervisor + guardrail +
HITL + MCP architecture FitMate AI already had:

| Capability | Before | Now |
|---|---|---|
| Logging | `print()` statements | Structured JSON logs (`observability.py`) with `request_id`, per-node `duration_ms`, and error type |
| Tracing | none | Optional LangSmith tracing via `LANGCHAIN_TRACING_V2` / `LANGCHAIN_API_KEY` (zero-config if unset) |
| LLM/MCP resilience | fail-open try/except only | `tenacity` retry with exponential backoff (3 attempts) on every Groq call and every MCP tool call |
| Usage visibility | none | `GET /metrics` — per-process counters for requests, LLM calls, MCP calls, errors, guardrail blocks |
| Rate limiting | none | `slowapi`: `POST /api/fitness` limited to 10/min, `/api/fitness/approve` to 20/min per client IP |
| Request correlation | none | Every HTTP request and every LangGraph node run carries the same `request_id` through the logs |

**⚠️ Security note:** the uploaded project's `.env` contained a live Groq
API key. It was removed from this build (only `.env.example` is included).
Since that key was shared in a zip, treat it as compromised — rotate/revoke
it at https://console.groq.com/keys and put the new one in your own local
`.env` (never commit `.env`).

---

# FitMate AI — Multi-Agent Personal Fitness Coach

**Group 8 — AI Personal Fitness Coach**

> People often find it difficult to maintain a consistent fitness routine.
> FitMate AI is a multi-agent AI fitness coach that creates personalized
> workout plans based on user goals, fitness levels, available resources,
> and time constraints — and adapts over time using real logged progress.

Built with **LangGraph, MCP (Model Context Protocol), a Supervisor +
Guardrail architecture, Human-in-the-Loop (HITL) review, FastAPI, and a
free-tier Groq LLM (Llama 3.3 70B).**

---

## 1. Quick start (zero external database required)

```bash
# 1. Clone / unzip the project, then:
cd fitmate-ai
python -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt

# 2. Add your FREE Groq API key
cp .env.example .env
# edit .env and paste your key from https://console.groq.com/keys

# 3. Run
python app.py
# or: uvicorn app:app --reload

# 4. Open
http://127.0.0.1:8000
```

That's it. No Postgres, no external MCP APIs, no paid services. The
exercise database and progress tracker run on local SQLite, and LangGraph
checkpointing runs in memory by default. Everything works out of the box.

**Optional — durable checkpointing:** set `DATABASE_URL` in `.env` to a
Postgres connection string and FitMate AI will automatically use
`PostgresSaver` instead of the in-memory checkpointer, so plan threads
survive a server restart. This is fully optional for the demo.

---

## 2. Why this is a strong fit for the problem statement

The brief asks for personalization across **four axes**: goals, fitness
level, available resources, and time constraints — plus motivation and
guidance. FitMate AI is architected so those four axes are explicit,
structured fields (`fitness_profile`) that flow through every agent,
rather than being buried in a single free-text prompt to one LLM call.

Instead of `User → LLM → workout`, the system is:

```
                         USER
                           |
                           v
                 ┌───────────────────┐
                 │   Guardrail +      │   <- blocks non-fitness / unsafe requests
                 │   Supervisor Agent │   <- extracts fitness_profile, picks agents
                 └─────────┬──────────┘
                            │
        ┌──────────┬────────┼────────┬───────────┐
        v          v        v        v           │
   Workout      Equipment Nutrition Progress      │  (dynamically selected —
   Agent        Agent     Agent     Agent         │   not every request needs
        │          │        │        │            │   every specialist)
        └──────────┴────────┴────────┴────────────┘
                            │
                            v
                  Fitness Plan Agent   <- always runs, integrates everything
                            │
                            v
                    Human Approval (HITL)
                     /              \
                Approve          Request changes
                     \              /
                            v
                      Final Agent  <- polishes + persists to SQLite
                            │
                            v
                           END
```

---

## 3. Architecture & code layout

| File | Responsibility |
|---|---|
| `app.py` | FastAPI routes, request validation, HTML templating |
| `backend.py` | LangGraph `StateGraph`: state, supervisor, guardrail, 5 specialist agents, HITL, checkpointing |
| `llm_client.py` | Groq LLM client (free tier) + JSON-extraction helpers |
| `mcp_client.py` | Wires up both local MCP servers, exposes `exercise_mcp_call` / `progress_mcp_call` |
| `exercise_mcp_server.py` | **MCP Server #1** — exercise database tools (`search_exercises`, `get_exercise`, ...) |
| `progress_mcp_server.py` | **MCP Server #2** — profile + workout-log tools (`log_workout_session`, `get_progress_summary`, ...) |
| `database.py` | SQLite schema, seed data (45+ exercises), all CRUD used by both MCP servers |
| `templates/index.html`, `static/style.css`, `static/script.js` | Plain HTML/CSS/JS frontend (no build step) |

### Why two separate MCP servers?

This mirrors the original travel-agent project's pattern of isolating
each external capability behind its own MCP server/process, so a problem
in one tool never breaks another:

1. **Exercise Database MCP** — the Workout and Equipment agents call
   `search_exercises(...)` to ground their output in a real, curated
   library instead of letting the LLM hallucinate exercise names, muscle
   groups, or rep schemes.
2. **Fitness Progress MCP** — the Progress agent calls
   `get_workout_history(...)` / `get_progress_summary(...)` on **real
   logged data** so its recommendations ("increase volume", "reduce
   Friday's intensity") are based on actual adherence, not guesses.

Both run as local stdio subprocesses — no network calls, no external API
keys, which keeps the hackathon demo 100% reliable.

### Dynamic Supervisor routing

The Supervisor doesn't always run all 5 specialists. For example:

- *"Beginner workout, no equipment"* → `workout_agent, equipment_agent, fitness_plan_agent`
- *"I've been training 3 weeks, workouts feel too easy"* → `progress_agent, workout_agent, fitness_plan_agent`
- *"Help me gain muscle and eat better"* → `workout_agent, nutrition_agent, equipment_agent, fitness_plan_agent`

`fitness_plan_agent` always runs because it is the integration step.

### Guardrail

An LLM-based input guardrail checks that the request is fitness-related
before any specialist runs, and can flag unsafe requests (e.g. "give me
an intense plan despite my torn ACL") to respond with a safety message
rather than blindly generating a plan. It fails **open** on a parsing
error so a temporary format hiccup never blocks a legitimate user.

### Human-in-the-Loop (HITL)

`fitness_plan_agent` always produces a **draft**. Execution pauses via
LangGraph's `interrupt()` and the UI shows **Approve** / **Request
changes**. Feedback (e.g. *"I only have 30 minutes on weekdays"*) is fed
back into the graph, which resumes from the checkpoint and regenerates
the final plan — a real, working human-approval loop, not a cosmetic one.

### The "adapt my next workout" loop

The **Progress Log** tab lets a user log completed/skipped sessions with
a difficulty and energy rating. That data is stored in SQLite and served
back to the Progress Agent through the Fitness Progress MCP server the
next time that same tracking ID requests a plan — closing the feedback
loop the problem statement asks for ("providing motivation and
guidance").

---

## 4. Data modeling

SQLite is used deliberately for the hackathon: zero setup, file-based,
and still a real relational schema you can explain to judges.

```
exercises                  fitness_profiles            workout_sessions
--------------------------  --------------------------  --------------------------
id (PK)                     user_id (PK)                 id (PK)
name (unique)                age                          user_id (FK, logical)
muscle_group                 goal                          session_date
equipment                    fitness_level                 workout_type
difficulty                   days_per_week                 duration_minutes
exercise_type                session_duration               completed
default_sets                 equipment (JSON)               difficulty_rating
default_reps                 preferences (JSON)             energy_level
instructions                 limitations                    notes
                              updated_at                     created_at

workout_plans
--------------------------
id (PK)
user_id (FK, logical)
plan_text
created_at
```

`user_id` here is a **self-chosen tracking ID**, not an authenticated
account — appropriate for a hackathon demo (see Security section below
for the production note).

For durable multi-turn conversation state (the HITL pause/resume), the
LangGraph checkpointer stores its own thread state — in Postgres if
`DATABASE_URL` is set, otherwise in memory.

---

## 5. Technology stack & reasoning

| Layer | Choice | Why |
|---|---|---|
| LLM | **Groq — Llama 3.3 70B** | Free API tier, very low latency (important for a multi-call agent pipeline), strong instruction-following for structured JSON output |
| Orchestration | **LangGraph** | Native support for stateful graphs, conditional routing, and `interrupt()`-based HITL — exactly what a supervisor + specialists + approval flow needs |
| Tool access | **MCP (Model Context Protocol)** | Standardizes how agents call external tools (exercise DB, progress tracker); swappable/extensible without touching agent logic |
| Backend | **FastAPI** | Async-friendly, automatic request validation via Pydantic, minimal boilerplate |
| Database | **SQLite** (demo) / **Postgres** (optional, via `DATABASE_URL`) | Zero-setup reliability for judging, with a clear upgrade path to a real server for production |
| Frontend | **Plain HTML / CSS / JS** | No build step, loads instantly, easy for judges to inspect the exact code running |
| Containerization | **Docker** | One-command reproducible deployment |

---

## 6. Scalability, reliability & testing notes

- **Graceful degradation everywhere**: every LLM/JSON parse and every MCP
  call is wrapped in `try/except` with a sensible fallback (e.g. the
  Supervisor falls back to running all agents if JSON parsing fails; the
  Progress agent falls back to generic guidance if no history exists).
- **Fail-open guardrail**: a malformed guardrail response never blocks a
  legitimate user — it defaults to allowing the request.
- **Stateless HTTP + threaded checkpoints**: each conversation is
  addressed by a `thread_id`, so the API is horizontally scalable behind
  a load balancer as long as the checkpointer (Postgres) is shared.
- **Input validation**: Pydantic models enforce field lengths and value
  ranges (e.g. `difficulty_rating` 1–10) at the API boundary.
- **Isolation between MCP servers**: a failure in one tool server cannot
  crash the other or the main agent loop.
- **Manual test path**: `python mcp_client.py` independently verifies
  both MCP servers boot and expose the expected tools before you even
  start the API — useful as a pre-flight smoke test.

---

## 7. Security & data privacy

- No third-party data leaves the machine except calls to the Groq LLM
  API (industry-standard practice for any LLM-backed app).
- The exercise database and progress tracker are local SQLite files —
  no data broker, no analytics SDK, no ads.
- Only minimal, user-volunteered fields are stored (goal, level,
  equipment, session logs) — no names, emails, or health diagnoses are
  requested or required.
- All nutrition/lifestyle output carries an explicit non-medical-advice
  disclaimer, and the guardrail is instructed to flag unsafe requests
  (e.g. training through a serious injury) instead of complying blindly.
- **Production note (documented, not implemented for the hackathon):**
  the `user_id` field is a self-chosen tracking string with no
  authentication. A production version would replace it with an
  authenticated account system, encrypt data at rest, and add per-user
  access control before storing any real health information.

---

## 8. Business value

- **User impact**: turns "I don't know where to start / I keep quitting"
  into a concrete, reviewable plan plus a lightweight feedback loop that
  keeps adjusting to the user instead of staying static.
- **Cost to run**: the entire LLM cost is $0 on Groq's free tier at
  hackathon/demo scale; the only paid dependency in a production
  deployment would be hosting.
- **Extensibility**: because tools are exposed via MCP, adding a new
  capability (e.g. a wearable-data MCP server, a calendar-scheduling MCP
  server) does not require touching agent prompts or the graph
  structure — just registering a new server.

---

## 9. Demo script (suggested)

1. Open the **Coach** tab, click a preset (e.g. *Beginner Home Workout*),
   add a Progress tracking ID, submit.
2. Point out the guardrail + supervisor step, then the animated agent
   pipeline — explain that the highlighted agents are the *actual* ones
   the Supervisor selected for this request (not scripted).
3. Show the tabbed results (Workout / Equipment / Nutrition / Progress).
4. Trigger the **Request changes** path with something like *"I only
   have 20 minutes"* and show the plan regenerate from the same
   checkpoint.
5. Switch to **Progress Log**, log a workout with a high difficulty /
   low energy rating for the same tracking ID, then go back to **Coach**
   and submit a new request — show the Progress Agent's response now
   referencing that real logged session.
