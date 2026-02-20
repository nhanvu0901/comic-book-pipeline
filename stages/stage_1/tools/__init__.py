"""
Tool registry for Stage 1.

Responsibilities:
  - Assemble the TOOLS list (schemas) that gets sent to the LLM API
  - Provide init() to wire up tools that need LLM access (paraphrase_query)
  - Provide dispatch_tool() to route LLM tool-use requests to implementations

Adding a new tool:
  1. Create tools/<name>.py with MY_TOOL schema dict + function
  2. Import both here
  3. Add schema to TOOLS list
  4. Add branch to dispatch_tool()
"""
from .web_search import WEB_SEARCH_TOOL, web_search
from .sequential_thinking import SEQUENTIAL_THINKING_TOOL, think, reset_session
from .paraphrase_query import (
    PARAPHRASE_QUERY_TOOL,
    paraphrase_query,
    init as _init_paraphrase,
)

# ─── TOOLS list — sent to the LLM API on every call ─────────────────────────

TOOLS = [
    WEB_SEARCH_TOOL,
    SEQUENTIAL_THINKING_TOOL,
    PARAPHRASE_QUERY_TOOL,
]


# ─── Initialization ──────────────────────────────────────────────────────────

def init(client, model):
    """
    Initialize tools that require LLM access.
    Also resets the sequential thinking session.
    Call once per ScriptAgent instantiation.
    """
    _init_paraphrase(client, model)
    reset_session()


# ─── Dispatcher ──────────────────────────────────────────────────────────────

def dispatch_tool(name: str, inputs: dict) -> dict:
    """
    Route a tool-use request from the LLM to the correct implementation.

    Args:
        name:   Tool name as declared in the schema (e.g. "web_search")
        inputs: The arguments dict the LLM passed to the tool

    Returns:
        A dict that gets JSON-serialized and sent back to the LLM as tool_result
    """
    if name == "web_search":
        return web_search(
            query=inputs["query"],
            max_results=inputs.get("max_results", 5),
        )

    if name == "sequential_thinking":
        return think(
            thought=inputs["thought"],
            step_number=inputs["step_number"],
            total_steps=inputs["total_steps"],
            branch=inputs.get("branch", ""),
            is_final=inputs.get("is_final", False),
        )

    if name == "paraphrase_query":
        return paraphrase_query(
            query=inputs["query"],
            n=inputs.get("n", 3),
            focus=inputs.get("focus", ""),
        )

    return {"error": f"Unknown tool: '{name}'"}
