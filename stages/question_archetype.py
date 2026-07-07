"""Q&A question archetype detection (explore_answer mode only).

Two archetypes drive different research + writing contracts:

  "list"    — "Who has survived X?", "N characters who..." → independent items,
              countdown listicle (the original explore_answer format).
  "explain" — "Why did X...", "How did Y...", "The day Z..." → ONE story told as
              an ARGUMENT; the scenes build a causal chain and the final body
              scene must state the answer plainly (a countdown of events never
              answers a "why" — measured failure on the first explain question).

Deterministic + data-driven (the question's own shape), so no comic/story is
special-cased. Shared by stage_1 answer_research (research contract) and
stage_3 explore_answer (writer/validator/hook contract).
"""
import re

_EXPLAIN_Q_RE = re.compile(
    r"^\s*(?:why|how|what\s+made|the\s+(?:real\s+|tragic\s+|hidden\s+)?reason|"
    r"the\s+day|the\s+time|the\s+moment)\b",
    re.IGNORECASE,
)


def question_archetype(question: str) -> str:
    """"explain" for Why/How/The-reason/The-day questions, else "list"."""
    return "explain" if _EXPLAIN_Q_RE.match(question or "") else "list"
