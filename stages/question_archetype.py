"""Q&A question archetype & format detection (explore_answer mode).

Two formats drive different research + writing contracts:

  "list"   — "Who has survived X?", "N characters who..." → independent items,
             countdown listicle across multiple comics.
  "direct" — "Why did X...", "How did Y...", "What is Z...", "This is how X..."
             → ONE grounded answer developed through story/evidence beats. The
             scenes build a causal or conceptual argument, and the final scene
             states the answer thesis plainly. (Previously also referred to as
             "explain"; "direct" and legacy "explain" are treated as equal).

Direct questions come in two registers:
- Interrogative: "Why...", "How...", "What made...", "What is/was/are...",
  "What happened to..."
- Statement lead: "This is how...", "The day...", "The tragic reason...",
  "The truth behind...", "The origin of..."
Both drive the identical direct research/writer/validator contract; only the
hook builder needs to tell them apart so it avoids appending "?" to statements.
"""
import re

QA_FORMAT_LIST = "list"
QA_FORMAT_DIRECT = "direct"

_DIRECT_INTERROGATIVE_LEAD = (
    r"why|how|what\s+made|"
    r"what\s+(?:actually\s+|really\s+|exactly\s+)?(?:is|was|are|were)(?!\s+\d+\b)|"
    r"what\s+happened\s+(?:to|when|in)\b"
)
_DIRECT_STATEMENT_LEAD = (
    r"the\s+(?:real\s+|tragic\s+|hidden\s+)?reason|"
    r"the\s+(?:true\s+|real\s+)?(?:origin|secret|truth)\s+(?:of|behind|about)|"
    r"the\s+day|the\s+time|the\s+moment|"
    r"this\s+is\s+how|this\s+is\s+why|here'?s\s+how|here'?s\s+why"
)
_DIRECT_Q_RE = re.compile(
    rf"^\s*(?:{_DIRECT_INTERROGATIVE_LEAD}|{_DIRECT_STATEMENT_LEAD})\b", re.IGNORECASE,
)
_STATEMENT_LEAD_RE = re.compile(rf"^\s*(?:{_DIRECT_STATEMENT_LEAD})\b", re.IGNORECASE)

# Capability-comparison shape ("X things Carnage can do that Venom can't",
# "...that make Carnage stronger than Venom"). Still routes through
# question_archetype() as "list" (unchanged) — this is a SEPARATE flag the
# stage_3 hook builder checks to skip the "who"/rank tease language.
_COMPARISON_RE = re.compile(
    r"can\s+do\s+that\b.*\bcan'?t\b"       # "...can do that... can't"
    r"|\bcan'?t\s+do\b"                     # "...can't do..."
    r"|\bthat\s+makes?\b.*\bstronger\s+than\b",  # "...that make(s) X stronger than Y"
    re.IGNORECASE,
)


class QuestionArchetype(str):
    """String subclass that treats 'direct' and legacy 'explain' as matching."""

    def __eq__(self, other: object) -> bool:
        if super().__eq__(other):
            return True
        val = str(self)
        other_val = str(other) if isinstance(other, (str, QuestionArchetype)) else other
        if val == "direct" and other_val == "explain":
            return True
        if val == "explain" and other_val == "direct":
            return True
        return False

    def __hash__(self) -> int:
        return hash("direct") if self in ("direct", "explain") else super().__hash__()


def is_direct_format(fmt: str | None) -> bool:
    """True when format or archetype represents a direct answer."""
    return str(fmt or "").strip().lower() in ("direct", "explain")


def is_comparison(question: str) -> bool:
    """True when `question` is a capability-comparison shape."""
    return bool(_COMPARISON_RE.search(question or ""))


def question_archetype(question: str) -> QuestionArchetype:
    """"direct" for Why/How/What-is/concept questions, else "list"."""
    return QuestionArchetype(QA_FORMAT_DIRECT if _DIRECT_Q_RE.match(question or "") else QA_FORMAT_LIST)


def is_statement_lead(question: str) -> bool:
    """True when a direct question is phrased as a STATEMENT."""
    return bool(_STATEMENT_LEAD_RE.match(question or ""))

