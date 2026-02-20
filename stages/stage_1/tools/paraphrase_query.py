"""
Paraphrase Query tool — generates N semantically diverse reformulations of a
search query using a targeted LLM sub-call.

Why this matters:
  A single query finds one slice of results. Different phrasings surface different
  pages. For example, "death of Gwen Stacy" vs "Amazing Spider-Man 121 bridge scene"
  vs "Spider-Man girlfriend killed Green Goblin" all return different results
  even though they describe the same event.

How it works:
  1. The LLM calls this tool with the original query and desired count N.
  2. Python makes a small, targeted LLM call (same model, low max_tokens)
     asking it to produce N paraphrases focused on diverse angles.
  3. The paraphrases are returned to the LLM, which then uses each one
     as a separate web_search query to maximize search coverage.

The 'focus' parameter steers what dimension to vary:
  - 'specificity'  : broad → narrow (general topic to exact issue/creator)
  - 'perspective'  : event-focused, character-focused, impact-focused
  - 'terminology'  : technical comic jargon ↔ casual everyday language
  - ''             : free variation across all dimensions (default)
"""
import json
from ..ui import Colors

# ─── Schema ─────────────────────────────────────────────────────────────────

PARAPHRASE_QUERY_TOOL = {
    "name": "paraphrase_query",
    "description": (
        "Generate N semantically diverse reformulations of a search query to maximize "
        "search coverage. Use this BEFORE web_search when the topic is nuanced, ambiguous, "
        "or when a single phrasing might miss important results. "
        "The paraphrases vary in vocabulary, angle, and specificity so that each one "
        "finds DIFFERENT results. After receiving the paraphrases, call web_search "
        "separately for each one to gather the broadest set of information."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The original search query to paraphrase",
            },
            "n": {
                "type": "integer",
                "description": "Number of paraphrases to generate. Between 2 and 5.",
            },
            "focus": {
                "type": "string",
                "description": (
                    "Optional dimension to vary across paraphrases: "
                    "'specificity' (broad to narrow), "
                    "'perspective' (event vs character vs impact angle), "
                    "'terminology' (technical jargon vs casual language). "
                    "Leave empty for free variation."
                ),
            },
        },
        "required": ["query", "n"],
    },
}

# ─── Module-level LLM reference (set via init()) ─────────────────────────────

_client = None
_model = None


def init(client, model):
    """Provide the LLM client so paraphrase_query can make sub-calls."""
    global _client, _model
    _client = client
    _model = model


# ─── Focus instructions (injected into the sub-prompt) ──────────────────────

_FOCUS_INSTRUCTIONS = {
    "specificity": (
        "Vary from broad/general (topic overview) to narrow/specific "
        "(exact issue number, creator name, publication year)."
    ),
    "perspective": (
        "Vary the angle: one from the event's perspective, one from a character's "
        "perspective, one from the narrative impact/legacy perspective."
    ),
    "terminology": (
        "Vary from technical comic-book terminology (series title, issue arc, "
        "writer/artist credit) to casual everyday language a non-fan would use."
    ),
    "": (
        "Vary freely across vocabulary, angle, and specificity. "
        "Each paraphrase should feel like it was written by a different person "
        "approaching the same topic from a completely different starting point."
    ),
}


# ─── Implementation ──────────────────────────────────────────────────────────

def paraphrase_query(query: str, n: int = 3, focus: str = "") -> dict:
    """
    Generate N diverse paraphrases via a targeted LLM sub-call.

    Returns:
        {
            "original": str,
            "paraphrases": [str, ...],   # length N
            "count": int,
        }
    """
    n = max(2, min(n, 5))  # clamp to 2–5
    focus_instruction = _FOCUS_INSTRUCTIONS.get(focus, _FOCUS_INSTRUCTIONS[""])

    print(f"  {Colors.DIM}🔀 Generating {n} paraphrases for: \"{query}\"{Colors.END}")

    if _client is None:
        print(f"  {Colors.DIM}   Warning: tool not initialized, returning original query{Colors.END}")
        return {"original": query, "paraphrases": [query], "count": 1,
                "error": "Call init(client, model) before using paraphrase_query"}

    prompt = (
        f'Generate exactly {n} diverse search query paraphrases for this topic:\n'
        f'"{query}"\n\n'
        f'Variation instructions:\n'
        f'{focus_instruction}\n\n'
        f'Rules:\n'
        f'- Each paraphrase must find DIFFERENT search results than the others\n'
        f'- Keep each one concise: 4–10 words, optimized for web search\n'
        f'- Domain context: comic books, manga, graphic novels\n'
        f'- Do NOT number them or add explanations\n\n'
        f'Return ONLY a valid JSON array of {n} strings. Nothing else.\n'
        f'Example: ["query one here", "different angle query", "third variation"]'
    )

    try:
        response = _client.messages.create(
            model=_model,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()

        # Parse: try direct JSON first, then find array inside text
        try:
            paraphrases = json.loads(raw)
        except json.JSONDecodeError:
            bracket_start = raw.find("[")
            bracket_end = raw.rfind("]")
            if bracket_start != -1 and bracket_end != -1:
                paraphrases = json.loads(raw[bracket_start : bracket_end + 1])
            else:
                raise ValueError("No JSON array found in response")

        if not isinstance(paraphrases, list):
            raise ValueError("Response is not a list")

        # Sanitize: ensure strings, cap at N, strip whitespace
        paraphrases = [str(p).strip() for p in paraphrases[:n] if str(p).strip()]

        print(f"  {Colors.DIM}   ✓ {len(paraphrases)} paraphrases generated{Colors.END}")
        for i, p in enumerate(paraphrases, 1):
            print(f"  {Colors.DIM}     [{i}] {p}{Colors.END}")

        return {
            "original": query,
            "paraphrases": paraphrases,
            "count": len(paraphrases),
        }

    except Exception as e:
        # Graceful fallback: return original query so the caller isn't broken
        print(f"  {Colors.DIM}   Paraphrase error: {e} — falling back to original query{Colors.END}")
        return {
            "original": query,
            "paraphrases": [query],
            "count": 1,
            "error": str(e),
        }
