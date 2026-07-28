"""Q&A question archetype detection (explore_answer mode only).

Two archetypes drive different research + writing contracts:

  "list"    — "Who has survived X?", "N characters who..." → independent items,
              countdown listicle (the original explore_answer format).
  "explain" — "Why did X...", "How did Y...", "The day Z...", "This is how
              X..." → ONE story told as an ARGUMENT; the scenes build a causal
              chain and the final body scene must state the answer plainly (a
              countdown of events never answers a "why" — measured failure on
              the first explain question).

An "explain" question comes in two REGISTERS — a real interrogative ("Why...",
"How...", "What made...") or a statement lead ("This is how...", "The day...",
"The tragic reason...") that reads as a promise, not a question. Both drive the
identical research/writer/validator contract; only the deterministic hook
(stage_3/explore_answer.py::_build_hook) needs to tell them apart, since
forcing a "?" onto a statement lead ("This is how Batman trains himself?")
reads wrong — see `is_statement_lead` below.

Deterministic + data-driven (the question's own shape), so no comic/story is
special-cased. Shared by stage_1 answer_research (research contract) and
stage_3 explore_answer (writer/validator/hook contract).
"""
import re

_INTERROGATIVE_LEAD = r"why|how|what\s+made"
_STATEMENT_LEAD = (
    r"the\s+(?:real\s+|tragic\s+|hidden\s+)?reason|the\s+day|the\s+time|the\s+moment|"
    r"this\s+is\s+how|this\s+is\s+why|here'?s\s+how|here'?s\s+why"
)
_EXPLAIN_Q_RE = re.compile(
    rf"^\s*(?:{_INTERROGATIVE_LEAD}|{_STATEMENT_LEAD})\b", re.IGNORECASE,
)
_STATEMENT_LEAD_RE = re.compile(rf"^\s*(?:{_STATEMENT_LEAD})\b", re.IGNORECASE)

# Capability-comparison shape ("X things Carnage can do that Venom can't",
# "...that make Carnage stronger than Venom"). Still routes through
# question_archetype() as "list" (unchanged) — this is a SEPARATE flag the
# stage_3 hook builder checks to skip the "who"/rank tease language, since a
# comparison has no ranked person, just an escalating capability. CONSERVATIVE:
# only clear can/can't or stronger-than shapes match; ambiguous phrasing (e.g.
# "things Carnage has that Venom doesn't") stays plain "list" — precision over
# recall per the spec.
_COMPARISON_RE = re.compile(
    r"can\s+do\s+that\b.*\bcan'?t\b"       # "...can do that... can't"
    r"|\bcan'?t\s+do\b"                     # "...can't do..."
    r"|\bthat\s+makes?\b.*\bstronger\s+than\b",  # "...that make(s) X stronger than Y"
    re.IGNORECASE,
)


def is_comparison(question: str) -> bool:
    """True when `question` is a capability-comparison shape (see _COMPARISON_RE
    above). Independent of question_archetype() — comparison questions still
    return "list" there; this is an additive flag, not a third archetype."""
    return bool(_COMPARISON_RE.search(question or ""))


def question_archetype(question: str) -> str:
    """"explain" for Why/How/The-reason/The-day/This-is-how questions, else "list"."""
    return "explain" if _EXPLAIN_Q_RE.match(question or "") else "list"


def is_statement_lead(question: str) -> bool:
    """True when an "explain" question is phrased as a STATEMENT ("This is how...",
    "The day...") rather than a real interrogative ("Why...", "How..."). Used only
    by the hook builder to decide whether a "?" belongs at the end."""
    return bool(_STATEMENT_LEAD_RE.match(question or ""))
