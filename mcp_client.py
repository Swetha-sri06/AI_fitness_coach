"""
mcp_client.py
=========================================================
Wires up the two local MCP servers used by FitMate AI:

  1. exercise_mcp_server.py   -> the exercise database
  2. progress_mcp_server.py   -> user fitness profiles + workout logs

Both servers are launched as local stdio subprocesses -- no external
API keys, no network calls, no paid services. This keeps the hackathon
demo 100% self-contained and reliable.
"""

import sys
from pathlib import Path
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient
from tenacity import retry, stop_after_attempt, wait_exponential

from observability import logger, usage

BASE_DIR = Path(__file__).resolve().parent
EXERCISE_SERVER_PATH = BASE_DIR / "exercise_mcp_server.py"
PROGRESS_SERVER_PATH = BASE_DIR / "progress_mcp_server.py"

client = MultiServerMCPClient(
    {
        "exercise": {
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(EXERCISE_SERVER_PATH)],
        },
        "progress": {
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(PROGRESS_SERVER_PATH)],
        },
    }
)


@retry(
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
    stop=stop_after_attempt(3),
    reraise=True,
)
async def _call_tool(server_name: str, tool_name: str, tool_args: dict[str, Any] | None = None):
    """
    Load one tool from one MCP server and invoke it.

    Loading only the requested server keeps a problem in one server from
    ever affecting the other (mirrors the isolation pattern used for the
    original travel-agent MCP integrations). Retries with backoff cover
    transient stdio-subprocess hiccups instead of failing the whole run.
    """
    usage.total_mcp_calls += 1
    try:
        tools = await client.get_tools(server_name=server_name)
        tool = next((t for t in tools if t.name == tool_name), None)

        if tool is None:
            available = ", ".join(sorted(t.name for t in tools)) or "none"
            raise RuntimeError(
                f"MCP tool '{tool_name}' was not found on server '{server_name}'. "
                f"Available tools: {available}"
            )

        return await tool.ainvoke(tool_args or {})
    except Exception as exc:
        usage.total_errors += 1
        logger.warning(
            f"MCP call failed: {server_name}.{tool_name}",
            extra={
                "event": "mcp_error",
                "server": server_name,
                "tool": tool_name,
                "error_type": type(exc).__name__,
            },
        )
        raise


# =========================================================
# Exercise Database MCP
# =========================================================
async def exercise_mcp_call(tool_name: str, tool_args: dict[str, Any] | None = None):
    return await _call_tool("exercise", tool_name, tool_args)


# =========================================================
# Fitness Progress MCP
# =========================================================
async def progress_mcp_call(tool_name: str, tool_args: dict[str, Any] | None = None):
    return await _call_tool("progress", tool_name, tool_args)


async def get_all_tools() -> None:
    """Quick manual connectivity check for both MCP servers."""
    for server_name in ("exercise", "progress"):
        try:
            tools = await client.get_tools(server_name=server_name)
            names = ", ".join(t.name for t in tools) or "no tools"
            print(f"{server_name}: OK -> {names}")
        except Exception as exc:
            print(f"{server_name}: FAILED -> {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(get_all_tools())
