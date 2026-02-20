"""
Sequential Thinking tool — guides the LLM to reason through a topic
across multiple structured aspects before forming its final response.

How it works:
  - The LLM calls this tool once per thinking step.
  - Each call records the thought and returns guidance for the next aspect to explore.
  - On the final step (is_final=True), it returns the full thinking chain as a
    structured summary for the LLM to synthesize into its JSON response.
  - The session state (accumulated steps) lives at module level and is reset
    at the start of each new conversation via reset_session().

Why this matters:
  - Without structured thinking, LLMs jump to the most common/obvious answer.
  - This forces exploration of: narrative context, character motivation,
    emotional impact, visual storytelling, historical significance, and
    cultural legacy — all of which produce richer script narration.
"""
from ..ui import Colors

# ─── Schema ─────────────────────────────────────────────────────────────────

SEQUENTIAL_THINKING_TOOL = {
    "name": "sequential_thinking",
    "description": (
        "Think deeply and systematically through a topic before forming your response. "
        "Call this tool once per thinking step — each step should explore a DIFFERENT "
        "aspect or angle of the topic. Do NOT repeat the same angle twice. "
        "Aspects to cycle through: narrative context, character motivations, "
        "emotional core, visual storytelling potential, historical significance, "
        "cultural impact, pacing and scene structure. "
        "Use 3–6 steps for most topics. Set is_final=true on your last step. "
        "After the final step, synthesize all insights into your structured JSON response."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "thought": {
                "type": "string",
                "description": (
                    "Your detailed reasoning for this step. Be specific — "
                    "what does this aspect reveal about the story or characters?"
                ),
            },
            "step_number": {
                "type": "integer",
                "description": "Current step number, starting at 1",
            },
            "total_steps": {
                "type": "integer",
                "description": "Total number of thinking steps you plan to take (3–6 recommended)",
            },
            "branch": {
                "type": "string",
                "description": (
                    "The aspect/angle being explored in this step. "
                    "Examples: 'narrative context', 'character motivation', "
                    "'emotional impact', 'visual storytelling', 'historical significance', "
                    "'cultural legacy', 'pacing and scene structure'"
                ),
            },
            "is_final": {
                "type": "boolean",
                "description": "Set true on the last step to close the thinking session",
            },
        },
        "required": ["thought", "step_number", "total_steps"],
    },
}

# ─── Aspect rotation (used to suggest next angle when branch is omitted) ────

_ASPECTS = [
    "narrative context and story setup",
    "character motivations and relationships",
    "emotional core and central themes",
    "visual storytelling and iconic moments",
    "historical significance and era context",
    "cultural impact and lasting legacy",
    "pacing and scene structure",
]

# ─── Session state ───────────────────────────────────────────────────────────

_steps: list[dict] = []


def reset_session():
    """Clear accumulated steps. Call at the start of each new agent run."""
    global _steps
    _steps = []


# ─── Implementation ──────────────────────────────────────────────────────────

def think(
    thought: str,
    step_number: int,
    total_steps: int,
    branch: str = "",
    is_final: bool = False,
) -> dict:
    """
    Record one thinking step and return guidance for the next.

    The LLM calls this multiple times, each time exploring a different angle.
    On the final call, returns the full thinking chain as a structured summary.
    """
    display_branch = branch if branch else _ASPECTS[(step_number - 1) % len(_ASPECTS)]

    _steps.append({
        "step": step_number,
        "aspect": display_branch,
        "thought": thought,
    })

    # Show the thinking in the terminal so the user can follow along
    short = thought[:90] + "..." if len(thought) > 90 else thought
    print(
        f"  {Colors.DIM}💭 Thinking [{step_number}/{total_steps}] "
        f"— {display_branch}{Colors.END}\n"
        f"  {Colors.DIM}   {short}{Colors.END}"
    )

    if is_final:
        chain = list(_steps)
        reset_session()
        return {
            "status": "thinking_complete",
            "steps_taken": len(chain),
            "thinking_chain": chain,
            "instruction": (
                "Your deep analysis is complete. Now synthesize ALL of the above insights "
                "into your structured JSON response. Let the richness of your thinking show "
                "in the narration choices, scene mood, emotional beats, and image queries."
            ),
        }

    # Suggest the next aspect to explore
    next_aspect = _ASPECTS[step_number % len(_ASPECTS)]
    remaining = total_steps - step_number

    return {
        "status": "thought_recorded",
        "step_recorded": step_number,
        "steps_remaining": remaining,
        "total_steps_so_far": len(_steps),
        "suggested_next_aspect": next_aspect,
        "instruction": (
            f"Step {step_number} recorded. "
            f"Continue to step {step_number + 1} — suggested focus: '{next_aspect}'. "
            f"{remaining} step(s) remaining before is_final=true."
        ),
    }
