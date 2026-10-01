"""The micro-scout rules every research prompt must carry, as pinned phrases.

The same three rules live in four places that cannot share one string: the
planner's assembled prompt, the fixed general_micro template (str.format, so
braces differ), the verify template, and the youcom_scout CLI. Each test file
asserts the phrases below against its own copy so one cannot drift from the
others unnoticed.
"""

DETAIL_FIELDS = ("aftermath", "context_behind", "unrevealed", "detail_citations")

# In the order of meaning the rules are written in: the ask, the reaction/teaser
# rule, the request-wording rule. Backticks are stripped before comparing, so a
# plain-text prompt (the CLI) and a markdown one match the same phrases.
ASK_PHRASES = (
    "Also return what happens next (aftermath) and what set the moment up (context_behind)",
    "detail_citations",
    "verbatim quote",
    "list those URLs in evidence_urls too",
    'Use "" when no source states it; never infer or invent an outcome or backstory.',
)
REACTION_PHRASES = (
    "A reviewer's reaction or a teaser",
    "a shocking twist I never saw coming",
    "is not an event",
    "never build what_visibly_happens, turning_point or aftermath from it",
    "keep aftermath to what is actually stated and put the withheld point in unrevealed",
)
REQUEST_WORDING_PHRASES = (
    "The request's own wording",
    "final twist",
    "it is never evidence",
    "Do not echo it into a candidate unless a source states it.",
)
THREE_RULES = ASK_PHRASES + REACTION_PHRASES + REQUEST_WORDING_PHRASES


def normalized(text: str) -> str:
    """Whitespace-collapsed, backtick-free text, so a wrapped markdown line matches."""
    return " ".join(text.replace("`", "").split())


def missing_rules(text: str) -> list[str]:
    flat = normalized(text)
    return [phrase for phrase in THREE_RULES if phrase not in flat]


def in_order(text: str) -> bool:
    """The ask, then the reaction rule, then the request-wording rule."""
    flat = normalized(text)
    positions = [flat.find(group[0]) for group in (ASK_PHRASES, REACTION_PHRASES, REQUEST_WORDING_PHRASES)]
    return all(p >= 0 for p in positions) and positions == sorted(positions)


# Added after an end-to-end run (2026-10-01) in which the code's own screens threw two scouted answers
# away: a claim_citation url rebuilt from a page title (it was not among the sources returned), and a
# series_issue_year that carried a volume, a cover date and an on-sale date (several years: not one exact
# issue). Plus the aftermath rule: with no stated outcome the field is "", not a sentence about the
# withheld ending. Micro-only, in the planner's assembled prompt and in the fixed general_micro template.
FORMAT_PHRASES = (
    "In series_issue_year write only Series Title #N (YYYY)",
    "ONE four-digit year",
    "A volume number, cover date or on-sale date goes in summary, never in that field.",
    "Copy every URL",
    "character for character from the address of a page you retrieved in this run",
    "rebuild a URL from a page title or guess a slug",
    'When no source states what happens, aftermath is ""',
    "never cite a reaction line as aftermath support",
)


def missing_format_rules(text: str) -> list[str]:
    flat = normalized(text)
    return [phrase for phrase in FORMAT_PHRASES if phrase not in flat]
