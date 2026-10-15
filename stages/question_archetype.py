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
    r"why|how|when|what\s+(?:made|makes|drives|causes)|"
    r"what\s+(?:actually\s+|really\s+|exactly\s+)?(?:is|was|are|were|does)(?!\s+\d+\b)|"
    r"what\s+(?:has\s+)?happened\s+(?:to|when|in)\b|what\s+happens\s+(?:to|when|in)\b|"
    r"what\s+(?:rule|cost|price|consequence|limit|power|secret|truth|choice|event)\s+(?:governs|drives|defines|lies|comes|happens|is|causes)\b"
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
_LIST_Q_RE = re.compile(
    r"^\s*(?:how\s+many\b|what\s+are\s+some\b|"
    r"what\s+are\s+(?:the\s+)?(?:distinct|different|multiple|notable|two|three|four|five|six|seven|eight|nine|ten|\d+)\b[^?]{0,80}\bexamples\b|"
    r"what\s+are\s+(?:the\s+)?(?:distinct|different|multiple)\s+(?:ways|times|moments|instances|cases)\b|"
    r"what\s+are\s+(?:(?:some|the|distinct|unique|hidden|forgotten)\s+)*"
    r"(?:[\w'-]+'s\s+)?(?:abilities|powers|weapons|suits|feats|transformations)\b|"
    r"what\s+are\s+some\s+of\b[^?]{0,100}\b"
    r"(?:feats|transformations|abilities|powers)\b)",
    re.IGNORECASE,
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
_ISSUE_OR_YEAR_RE = re.compile(
    r"#\s*\d+\b|\b(?:vol(?:ume)?\.?\s*\d+|19\d{2}|20\d{2})\b", re.I,
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
    q = question or ""
    return QuestionArchetype(
        QA_FORMAT_DIRECT if _DIRECT_Q_RE.match(q) and not _LIST_Q_RE.match(q)
        else QA_FORMAT_LIST
    )


def is_narrow_direct_question(question: str) -> bool:
    """A broad direct discovery question should not name an issue or year."""
    return str(question_archetype(question)) == QA_FORMAT_DIRECT and bool(
        _ISSUE_OR_YEAR_RE.search(question)
    )


def is_statement_lead(question: str) -> bool:
    """True when a direct question is phrased as a STATEMENT."""
    return bool(_STATEMENT_LEAD_RE.match(question or ""))
