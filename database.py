"""
database.py
=========================================================
Data layer for FitMate AI.

Uses SQLite so the project runs instantly with ZERO external database
setup (no server, no credentials) -- important for a hackathon demo.

Tables
------
exercises          : curated exercise library (the "Exercise Database")
fitness_profiles   : one row per user, their goals/constraints
workout_plans      : generated plan history
workout_sessions   : logged workouts used for adaptive progress tracking

All functions here are plain, synchronous, dependency-free Python so they
can be imported directly by both MCP servers (exercise_mcp_server.py and
progress_mcp_server.py) as well as by the FastAPI layer if needed.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "fitmate.db"


# =========================================================
# Connection helpers
# =========================================================
@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# =========================================================
# Schema
# =========================================================
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS exercises (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    muscle_group    TEXT NOT NULL,
    equipment       TEXT NOT NULL,
    difficulty      TEXT NOT NULL,
    exercise_type   TEXT NOT NULL,
    default_sets    INTEGER NOT NULL,
    default_reps    TEXT NOT NULL,
    instructions    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fitness_profiles (
    user_id         TEXT PRIMARY KEY,
    age             TEXT,
    goal            TEXT,
    fitness_level   TEXT,
    days_per_week   INTEGER,
    session_duration TEXT,
    equipment       TEXT,   -- JSON list
    preferences     TEXT,   -- JSON list
    limitations     TEXT,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS workout_plans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    plan_text       TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workout_sessions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             TEXT NOT NULL,
    session_date        TEXT NOT NULL,
    workout_type        TEXT,
    duration_minutes    INTEGER,
    completed           INTEGER NOT NULL DEFAULT 0,
    difficulty_rating   INTEGER,
    energy_level        INTEGER,
    notes               TEXT,
    created_at          TEXT NOT NULL
);
"""


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(SCHEMA_SQL)


# =========================================================
# Exercise library seed data
# =========================================================
EXERCISE_SEED: list[dict[str, Any]] = [
    # name, muscle_group, equipment, difficulty, type, sets, reps, instructions
    dict(name="Bodyweight Squat", muscle_group="legs", equipment="none", difficulty="beginner", exercise_type="strength", default_sets=3, default_reps="12-15", instructions="Feet shoulder-width apart, lower hips back and down, keep chest up, drive through heels to stand."),
    dict(name="Push-Up", muscle_group="chest", equipment="none", difficulty="beginner", exercise_type="strength", default_sets=3, default_reps="8-12", instructions="Hands under shoulders, body in a straight line, lower chest to floor, press back up."),
    dict(name="Knee Push-Up", muscle_group="chest", equipment="none", difficulty="beginner", exercise_type="strength", default_sets=3, default_reps="10-15", instructions="Same as a push-up but with knees on the floor to reduce load. Good regression for beginners."),
    dict(name="Glute Bridge", muscle_group="glutes", equipment="none", difficulty="beginner", exercise_type="strength", default_sets=3, default_reps="12-15", instructions="Lie on back, knees bent, feet flat, drive hips up squeezing glutes at the top."),
    dict(name="Plank", muscle_group="core", equipment="none", difficulty="beginner", exercise_type="strength", default_sets=3, default_reps="20-40 sec", instructions="Forearms on floor, body in a straight line from head to heels, brace core."),
    dict(name="Mountain Climbers", muscle_group="core", equipment="none", difficulty="beginner", exercise_type="cardio", default_sets=3, default_reps="30 sec", instructions="Plank position, drive knees alternately toward chest at a quick pace."),
    dict(name="Jumping Jacks", muscle_group="full_body", equipment="none", difficulty="beginner", exercise_type="cardio", default_sets=3, default_reps="30-45 sec", instructions="Jump feet out while raising arms overhead, return to start, repeat rhythmically."),
    dict(name="Walking Lunge", muscle_group="legs", equipment="none", difficulty="beginner", exercise_type="strength", default_sets=3, default_reps="10 per leg", instructions="Step forward, lower back knee toward floor, push off front foot to step through."),
    dict(name="Superman Hold", muscle_group="back", equipment="none", difficulty="beginner", exercise_type="strength", default_sets=3, default_reps="10-12", instructions="Lie face down, lift arms and legs a few inches off the floor, hold briefly, lower."),
    dict(name="Burpee", muscle_group="full_body", equipment="none", difficulty="intermediate", exercise_type="cardio", default_sets=3, default_reps="8-12", instructions="Squat, kick feet back to plank, push-up, jump feet forward, jump up explosively."),
    dict(name="High Knees", muscle_group="full_body", equipment="none", difficulty="beginner", exercise_type="cardio", default_sets=3, default_reps="30 sec", instructions="Run in place driving knees up toward hip height at a fast pace."),
    dict(name="Wall Sit", muscle_group="legs", equipment="none", difficulty="beginner", exercise_type="strength", default_sets=3, default_reps="20-40 sec", instructions="Back flat against a wall, knees at 90 degrees, hold the position."),

    dict(name="Dumbbell Goblet Squat", muscle_group="legs", equipment="dumbbell", difficulty="beginner", exercise_type="strength", default_sets=3, default_reps="10-12", instructions="Hold one dumbbell vertically at chest, squat down keeping chest tall, drive up."),
    dict(name="Dumbbell Bench Press", muscle_group="chest", equipment="dumbbell", difficulty="beginner", exercise_type="strength", default_sets=3, default_reps="8-10", instructions="Lying on a bench or floor, press dumbbells up from chest level until arms extend."),
    dict(name="One-Arm Dumbbell Row", muscle_group="back", equipment="dumbbell", difficulty="beginner", exercise_type="strength", default_sets=3, default_reps="10-12", instructions="Support one hand/knee on a bench, row the dumbbell to the hip, squeeze the back."),
    dict(name="Dumbbell Shoulder Press", muscle_group="shoulders", equipment="dumbbell", difficulty="beginner", exercise_type="strength", default_sets=3, default_reps="8-10", instructions="Press dumbbells overhead from shoulder height until arms extend, lower under control."),
    dict(name="Dumbbell Bicep Curl", muscle_group="arms", equipment="dumbbell", difficulty="beginner", exercise_type="strength", default_sets=2, default_reps="12-15", instructions="Curl dumbbells up toward shoulders keeping elbows tucked, lower slowly."),
    dict(name="Dumbbell Triceps Extension", muscle_group="arms", equipment="dumbbell", difficulty="beginner", exercise_type="strength", default_sets=2, default_reps="12-15", instructions="Hold a dumbbell overhead with both hands, lower behind head by bending elbows, extend back up."),
    dict(name="Dumbbell Romanian Deadlift", muscle_group="hamstrings", equipment="dumbbell", difficulty="intermediate", exercise_type="strength", default_sets=3, default_reps="10-12", instructions="Hinge at hips keeping back flat, lower dumbbells along legs, feel hamstring stretch, stand tall."),
    dict(name="Dumbbell Lunge", muscle_group="legs", equipment="dumbbell", difficulty="beginner", exercise_type="strength", default_sets=3, default_reps="10 per leg", instructions="Hold dumbbells at sides, step forward into a lunge, push back to start."),
    dict(name="Dumbbell Lateral Raise", muscle_group="shoulders", equipment="dumbbell", difficulty="beginner", exercise_type="strength", default_sets=3, default_reps="12-15", instructions="Raise dumbbells out to the sides to shoulder height, lower with control."),
    dict(name="Renegade Row", muscle_group="core", equipment="dumbbell", difficulty="intermediate", exercise_type="strength", default_sets=3, default_reps="8 per side", instructions="Plank on dumbbells, row one dumbbell at a time while stabilizing the core."),

    dict(name="Resistance Band Squat", muscle_group="legs", equipment="resistance_band", difficulty="beginner", exercise_type="strength", default_sets=3, default_reps="12-15", instructions="Band around thighs above knees, squat while pressing knees outward against the band."),
    dict(name="Band Row", muscle_group="back", equipment="resistance_band", difficulty="beginner", exercise_type="strength", default_sets=3, default_reps="12-15", instructions="Anchor band at chest height, pull handles toward ribs, squeeze shoulder blades together."),
    dict(name="Band Chest Press", muscle_group="chest", equipment="resistance_band", difficulty="beginner", exercise_type="strength", default_sets=3, default_reps="12-15", instructions="Anchor band behind you, press handles forward at chest height until arms extend."),
    dict(name="Band Lateral Walk", muscle_group="glutes", equipment="resistance_band", difficulty="beginner", exercise_type="strength", default_sets=3, default_reps="10 steps each way", instructions="Band around ankles, slight squat, step sideways keeping tension on the band."),
    dict(name="Band Bicep Curl", muscle_group="arms", equipment="resistance_band", difficulty="beginner", exercise_type="strength", default_sets=2, default_reps="12-15", instructions="Stand on the band, curl handles up toward shoulders, lower slowly."),

    dict(name="Kettlebell Swing", muscle_group="full_body", equipment="kettlebell", difficulty="intermediate", exercise_type="cardio", default_sets=4, default_reps="15-20", instructions="Hinge at hips and drive kettlebell forward and up to chest height using hip power, not arms."),
    dict(name="Kettlebell Goblet Squat", muscle_group="legs", equipment="kettlebell", difficulty="beginner", exercise_type="strength", default_sets=3, default_reps="10-12", instructions="Hold kettlebell at chest, squat down keeping chest tall, drive back up."),
    dict(name="Kettlebell Deadlift", muscle_group="hamstrings", equipment="kettlebell", difficulty="beginner", exercise_type="strength", default_sets=3, default_reps="10-12", instructions="Hinge at hips to lower kettlebell toward floor keeping back flat, stand by driving hips forward."),

    dict(name="Barbell Back Squat", muscle_group="legs", equipment="barbell", difficulty="intermediate", exercise_type="strength", default_sets=4, default_reps="6-10", instructions="Bar on upper back, squat down until thighs are parallel to floor, drive up through heels."),
    dict(name="Barbell Bench Press", muscle_group="chest", equipment="barbell", difficulty="intermediate", exercise_type="strength", default_sets=4, default_reps="6-10", instructions="Lower bar to mid-chest with control, press back up to full arm extension."),
    dict(name="Barbell Deadlift", muscle_group="back", equipment="barbell", difficulty="advanced", exercise_type="strength", default_sets=4, default_reps="5-8", instructions="Hinge and grip the bar, keep back flat, drive through the floor to stand tall."),
    dict(name="Barbell Overhead Press", muscle_group="shoulders", equipment="barbell", difficulty="intermediate", exercise_type="strength", default_sets=3, default_reps="6-10", instructions="Press bar from shoulder height straight overhead, lower under control."),
    dict(name="Barbell Row", muscle_group="back", equipment="barbell", difficulty="intermediate", exercise_type="strength", default_sets=4, default_reps="8-10", instructions="Hinge forward, pull bar to lower ribs, squeeze shoulder blades, lower with control."),

    dict(name="Lat Pulldown Machine", muscle_group="back", equipment="gym_machine", difficulty="beginner", exercise_type="strength", default_sets=3, default_reps="10-12", instructions="Pull the bar down to upper chest, squeeze back muscles, control it back up."),
    dict(name="Leg Press Machine", muscle_group="legs", equipment="gym_machine", difficulty="beginner", exercise_type="strength", default_sets=3, default_reps="10-12", instructions="Press the platform away by extending knees and hips, don't lock knees, control the return."),
    dict(name="Chest Press Machine", muscle_group="chest", equipment="gym_machine", difficulty="beginner", exercise_type="strength", default_sets=3, default_reps="10-12", instructions="Press handles forward until arms extend, control the return to start."),
    dict(name="Seated Cable Row", muscle_group="back", equipment="gym_machine", difficulty="beginner", exercise_type="strength", default_sets=3, default_reps="10-12", instructions="Pull the handle toward the torso keeping back straight, squeeze shoulder blades."),
    dict(name="Treadmill Jog", muscle_group="full_body", equipment="gym_machine", difficulty="beginner", exercise_type="cardio", default_sets=1, default_reps="15-25 min", instructions="Maintain a steady, conversational-pace jog; adjust incline/speed for intensity."),

    dict(name="Brisk Walking", muscle_group="full_body", equipment="none", difficulty="beginner", exercise_type="cardio", default_sets=1, default_reps="20-30 min", instructions="Walk at a pace that raises your heart rate but still lets you talk in short sentences."),
    dict(name="Cat-Cow Stretch", muscle_group="mobility", equipment="none", difficulty="beginner", exercise_type="mobility", default_sets=1, default_reps="8-10 reps", instructions="On hands and knees, alternate arching and rounding the spine slowly with breath."),
    dict(name="Hip Flexor Stretch", muscle_group="mobility", equipment="none", difficulty="beginner", exercise_type="mobility", default_sets=1, default_reps="30 sec per side", instructions="Kneeling lunge position, gently press hips forward until a stretch is felt in the front hip."),
    dict(name="Child's Pose", muscle_group="mobility", equipment="none", difficulty="beginner", exercise_type="mobility", default_sets=1, default_reps="30-60 sec", instructions="Kneel and sit back onto heels, reach arms forward, relax the back and shoulders."),
    dict(name="Shoulder Circles", muscle_group="mobility", equipment="none", difficulty="beginner", exercise_type="mobility", default_sets=1, default_reps="10 each direction", instructions="Slowly rotate shoulders in large circles to loosen the joint before or after training."),
]


def seed_exercises() -> None:
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM exercises").fetchone()["c"]
        if count > 0:
            return
        conn.executemany(
            """
            INSERT INTO exercises
                (name, muscle_group, equipment, difficulty, exercise_type,
                 default_sets, default_reps, instructions)
            VALUES
                (:name, :muscle_group, :equipment, :difficulty, :exercise_type,
                 :default_sets, :default_reps, :instructions)
            """,
            EXERCISE_SEED,
        )


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


# =========================================================
# Exercise library queries
# =========================================================
def search_exercises(
    muscle_group: str | None = None,
    equipment: str | None = None,
    difficulty: str | None = None,
    exercise_type: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    query = "SELECT * FROM exercises WHERE 1=1"
    params: list[Any] = []

    if muscle_group:
        query += " AND muscle_group = ?"
        params.append(muscle_group.strip().lower())
    if equipment:
        query += " AND equipment = ?"
        params.append(equipment.strip().lower())
    if difficulty:
        query += " AND difficulty = ?"
        params.append(difficulty.strip().lower())
    if exercise_type:
        query += " AND exercise_type = ?"
        params.append(exercise_type.strip().lower())

    query += " LIMIT ?"
    params.append(max(1, min(limit, 100)))

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_exercise(name: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM exercises WHERE lower(name) = lower(?)", (name.strip(),)
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_muscle_groups() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT muscle_group FROM exercises ORDER BY muscle_group"
        ).fetchall()
    return [r["muscle_group"] for r in rows]


def list_equipment_types() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT equipment FROM exercises ORDER BY equipment"
        ).fetchall()
    return [r["equipment"] for r in rows]


# =========================================================
# Fitness profile CRUD
# =========================================================
def save_fitness_profile(user_id: str, profile: dict[str, Any]) -> dict[str, Any]:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO fitness_profiles
                (user_id, age, goal, fitness_level, days_per_week,
                 session_duration, equipment, preferences, limitations, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                age=excluded.age,
                goal=excluded.goal,
                fitness_level=excluded.fitness_level,
                days_per_week=excluded.days_per_week,
                session_duration=excluded.session_duration,
                equipment=excluded.equipment,
                preferences=excluded.preferences,
                limitations=excluded.limitations,
                updated_at=excluded.updated_at
            """,
            (
                user_id,
                str(profile.get("age", "")),
                str(profile.get("goal", "")),
                str(profile.get("fitness_level", "")),
                int(profile.get("days_per_week") or 0),
                str(profile.get("session_duration", "")),
                json.dumps(profile.get("equipment", [])),
                json.dumps(profile.get("preferences", [])),
                str(profile.get("limitations", "")),
                _now(),
            ),
        )
    return get_fitness_profile(user_id) or {}


def get_fitness_profile(user_id: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM fitness_profiles WHERE user_id = ?", (user_id,)
        ).fetchone()
    if not row:
        return None
    data = _row_to_dict(row)
    data["equipment"] = json.loads(data.get("equipment") or "[]")
    data["preferences"] = json.loads(data.get("preferences") or "[]")
    return data


# =========================================================
# Plan history
# =========================================================
def save_workout_plan(user_id: str, plan_text: str) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO workout_plans (user_id, plan_text, created_at) VALUES (?, ?, ?)",
            (user_id, plan_text, _now()),
        )
        return cursor.lastrowid


# =========================================================
# Workout session logging (adaptive progress feature)
# =========================================================
def log_workout_session(
    user_id: str,
    session_date: str,
    workout_type: str = "",
    duration_minutes: int | None = None,
    completed: bool = True,
    difficulty_rating: int | None = None,
    energy_level: int | None = None,
    notes: str = "",
) -> dict[str, Any]:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO workout_sessions
                (user_id, session_date, workout_type, duration_minutes,
                 completed, difficulty_rating, energy_level, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                session_date,
                workout_type,
                duration_minutes,
                1 if completed else 0,
                difficulty_rating,
                energy_level,
                notes,
                _now(),
            ),
        )
        session_id = cursor.lastrowid

    return {"id": session_id, "user_id": user_id, "session_date": session_date}


def get_workout_history(user_id: str, limit: int = 10) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM workout_sessions
            WHERE user_id = ?
            ORDER BY session_date DESC, id DESC
            LIMIT ?
            """,
            (user_id, max(1, min(limit, 100))),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_progress_summary(user_id: str) -> dict[str, Any]:
    history = get_workout_history(user_id, limit=50)

    if not history:
        return {
            "user_id": user_id,
            "total_sessions_logged": 0,
            "completed_sessions": 0,
            "completion_rate_percent": 0,
            "average_difficulty": None,
            "average_energy": None,
            "note": "No workout history logged yet.",
        }

    completed = [h for h in history if h["completed"]]
    difficulties = [h["difficulty_rating"] for h in history if h["difficulty_rating"] is not None]
    energies = [h["energy_level"] for h in history if h["energy_level"] is not None]

    return {
        "user_id": user_id,
        "total_sessions_logged": len(history),
        "completed_sessions": len(completed),
        "completion_rate_percent": round(100 * len(completed) / len(history), 1),
        "average_difficulty": round(sum(difficulties) / len(difficulties), 1) if difficulties else None,
        "average_energy": round(sum(energies) / len(energies), 1) if energies else None,
        "recent_sessions": history[:5],
    }


# Initialize on import so every consumer (MCP servers, FastAPI app) gets a ready DB.
init_db()
seed_exercises()
