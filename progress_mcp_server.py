"""
progress_mcp_server.py
=========================================================
MCP Server #2 : Fitness Progress Tracker

Exposes tools to save a user's fitness profile, log completed workouts,
and retrieve history/progress summaries. This lets the Progress Agent
reason over REAL logged data instead of guessing, and lets the app
demonstrate an "adapt my next workout" style feedback loop.
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

import database as db

mcp = FastMCP("Fitness Progress MCP Server")


@mcp.tool()
def save_fitness_profile(
    user_id: str,
    age: str = "",
    goal: str = "",
    fitness_level: str = "",
    days_per_week: int = 0,
    session_duration: str = "",
    equipment: list[str] | None = None,
    preferences: list[str] | None = None,
    limitations: str = "",
) -> dict[str, Any]:
    """Create or update a user's fitness profile."""
    profile = {
        "age": age,
        "goal": goal,
        "fitness_level": fitness_level,
        "days_per_week": days_per_week,
        "session_duration": session_duration,
        "equipment": equipment or [],
        "preferences": preferences or [],
        "limitations": limitations,
    }
    return db.save_fitness_profile(user_id, profile)


@mcp.tool()
def get_fitness_profile(user_id: str) -> dict[str, Any]:
    """Fetch a user's saved fitness profile, if one exists."""
    profile = db.get_fitness_profile(user_id)
    return profile or {"note": f"No saved profile found for user '{user_id}'."}


@mcp.tool()
def log_workout_session(
    user_id: str,
    session_date: str,
    workout_type: str = "",
    duration_minutes: int = 0,
    completed: bool = True,
    difficulty_rating: int = 0,
    energy_level: int = 0,
    notes: str = "",
) -> dict[str, Any]:
    """
    Log a completed (or skipped) workout session for a user.

    Args:
        session_date: ISO date string, e.g. "2026-08-22".
        difficulty_rating: 1-10 subjective difficulty.
        energy_level: 1-10 subjective energy during the workout.
    """
    return db.log_workout_session(
        user_id=user_id,
        session_date=session_date,
        workout_type=workout_type,
        duration_minutes=duration_minutes or None,
        completed=completed,
        difficulty_rating=difficulty_rating or None,
        energy_level=energy_level or None,
        notes=notes,
    )


@mcp.tool()
def get_workout_history(user_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return the most recent logged workout sessions for a user."""
    return db.get_workout_history(user_id, limit=limit)


@mcp.tool()
def get_progress_summary(user_id: str) -> dict[str, Any]:
    """Return adherence rate, average difficulty, and average energy for a user."""
    return db.get_progress_summary(user_id)


if __name__ == "__main__":
    mcp.run(transport="stdio")
