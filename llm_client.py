"""
exercise_mcp_server.py
=========================================================
MCP Server #1 : Exercise Database

Exposes a curated exercise library as MCP tools so the Workout and
Equipment agents can look up REAL exercises instead of asking the LLM
to hallucinate exercise names, muscle groups, or set/rep schemes.

Run standalone for a quick manual check:
    python exercise_mcp_server.py
(it will idle waiting for stdio input -- that's expected; it is meant
to be launched as a subprocess by mcp_client.py)
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

import database as db

mcp = FastMCP("Exercise Database MCP Server")


@mcp.tool()
def search_exercises(
    muscle_group: str = "",
    equipment: str = "",
    difficulty: str = "",
    exercise_type: str = "",
    limit: int = 12,
) -> list[dict[str, Any]]:
    """
    Search the exercise library.

    Args:
        muscle_group: e.g. chest, back, legs, core, shoulders, arms,
            glutes, hamstrings, full_body, mobility. Leave blank for any.
        equipment: none, dumbbell, resistance_band, kettlebell, barbell,
            gym_machine. Leave blank for any.
        difficulty: beginner, intermediate, advanced. Leave blank for any.
        exercise_type: strength, cardio, mobility. Leave blank for any.
        limit: max number of exercises to return.
    """
    return db.search_exercises(
        muscle_group=muscle_group or None,
        equipment=equipment or None,
        difficulty=difficulty or None,
        exercise_type=exercise_type or None,
        limit=limit,
    )


@mcp.tool()
def get_exercise(name: str) -> dict[str, Any]:
    """Return full details (instructions, default sets/reps) for one exercise by name."""
    exercise = db.get_exercise(name)
    if not exercise:
        return {"error": f"No exercise found matching '{name}'."}
    return exercise


@mcp.tool()
def list_muscle_groups() -> list[str]:
    """List every muscle group available in the exercise library."""
    return db.list_muscle_groups()


@mcp.tool()
def list_equipment_types() -> list[str]:
    """List every equipment category available in the exercise library."""
    return db.list_equipment_types()


if __name__ == "__main__":
    # mcp_client.py launches this file as a stdio subprocess.
    mcp.run(transport="stdio")
