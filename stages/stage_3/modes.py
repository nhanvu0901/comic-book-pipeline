"""
Catalog of narration modes the LLM can propose.

Each mode is an angle for the 60-second narration. The LLM picks the 3 that
best fit a given comic, and the user selects one.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class NarrationMode:
    key: str
    label: str
    description: str


MODES: list[NarrationMode] = [
    NarrationMode(
        "lesson",
        "Lesson",
        "Teach the audience something — a principle the story illustrates. Conclusion-oriented.",
    ),
    NarrationMode(
        "moral",
        "Moral",
        "Explicit moral of the story, in the vein of 'With great power comes great responsibility.'",
    ),
    NarrationMode(
        "feat",
        "Feat / Power Moment",
        "Celebrate the character's greatest feat or power peak — this is the proof they're top tier.",
    ),
    NarrationMode(
        "fun_fact",
        "Fun Fact / Trivia",
        "Behind-the-scenes trivia, creator intent, or little-known detail about the issue.",
    ),
    NarrationMode(
        "recap_summary",
        "Straight Recap",
        "Clean chronological retelling — no gimmick, just tell the story well.",
    ),
    NarrationMode(
        "character_spotlight",
        "Character Spotlight",
        "Focus on one character's arc, fear, choice, or transformation across this issue.",
    ),
    NarrationMode(
        "hot_take",
        "Hot Take",
        "A controversial or opinionated angle — debate-bait. Useful when the story is divisive.",
    ),
    NarrationMode(
        "twist_reveal",
        "Twist-First Reveal",
        "Open on the biggest twist/spoiler, then rewind to show how we got there. High retention.",
    ),
    NarrationMode(
        "theme_analysis",
        "Theme Deep-Dive",
        "Unpack the story's core theme (legacy, sacrifice, identity, etc.) with examples from the pages.",
    ),
    NarrationMode(
        "what_if",
        "What If",
        "Counterfactual — re-examine a decision point and speculate on the alternate outcome.",
    ),
    NarrationMode(
        "tragedy",
        "Tragedy",
        "Frame it as a tragedy — the flaw, the fall, the irreversible loss. Emotional weight.",
    ),
    NarrationMode(
        "power_ranking",
        "Power Ranking",
        "Analyze the feats shown to justify where the character ranks among their peers.",
    ),
]


MODES_BY_KEY = {m.key: m for m in MODES}


def describe_catalog() -> str:
    """One-line-per-mode catalog for inclusion in LLM prompts."""
    return "\n".join(f"- {m.key}: {m.description}" for m in MODES)
