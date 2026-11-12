"""Single source of truth for how a spoken script OPENS and where it names its source.

Measured 2026-11-12 (our newest scripts vs recent competitor transcripts): our first
concrete story event came 7-10 seconds in, behind a question or "what if" hook and a
sentence that cited the comic and issue. Competitors state a concrete event in their first
sentence, almost never open on a question for something that really happened, and the
first event lands around two seconds in.

Two blocks, kept separate because not every writer names its source in the opening (the
recap SDK credits the comic in its closing line):

* ``EVENT_FIRST_RULES`` binds every writer of spoken narration.
* ``CITATION_PLACEMENT_RULES`` binds every writer that names the comic and issue (or the
  film and year) in the opening of the script.

Inline system prompts get them through :func:`with_opening_rules`; the Gemini templates in
``prompts/`` carry the ``{{OPENING_RULES}}`` placeholder (or, for a template pasted by hand,
the literal text). ``tests/test_opening_rules.py`` keeps every copy in sync.
"""
from __future__ import annotations

EVENT_FIRST_RULES = """OPENING RULES (event first; these bind the first spoken lines):
- Sentence one states a concrete event or stake with the named character, as a statement: who does what, or what is at stake for whom.
- The first concrete story event is spoken within the first ten words, about three seconds. No title, issue number, definition or background comes before it.
- A question or a "what if" opening is allowed only when the source itself is a hypothetical, such as a What If title or a premise the source presents as imaginary or alternate. When the event really happens in the story, say it as a fact, never as a question and never as a hypothetical."""

CITATION_PLACEMENT_RULES = """SOURCE CITATION PLACEMENT (the comic and issue, or the film or series and its year):
- Still name the source so viewers can find it, but only after the first story event, never before it.
- Keep it to a short clause or a short sentence, such as "That's Ultimate Wolverine issue fifteen." for a comic or "That's the twenty nineteen film <Title>." for screen media, spoken within roughly the first third of the script.
- It does not have to be the sentence right after the hook. Let the next story event come first when that reads naturally, and never spend a long "This happens in ..., where ..." sentence on it.
- In a list whose items come from different sources, this placement governs the first item; each later item names its own source inside its own lines."""

OPENING_RULES = EVENT_FIRST_RULES + "\n\n" + CITATION_PLACEMENT_RULES

OPENING_RULES_PLACEHOLDER = "{{OPENING_RULES}}"


def with_opening_rules(prompt: str, marker: str = "Return ONLY JSON", *,
                       citation: bool = True) -> str:
    """Insert the opening rules into ``prompt`` just before the LAST ``marker`` (the
    output-format tail), like ``with_spoken_rules``. ``citation=False`` inserts only the
    event-first block, for a writer that does not name its source in the opening.
    Raises if the marker is missing, so a prompt edit can never silently drop the rules."""
    block = OPENING_RULES if citation else EVENT_FIRST_RULES
    idx = prompt.rfind(marker)
    if idx < 0:
        raise ValueError(f"opening rules marker {marker!r} not found in prompt")
    return prompt[:idx] + block + "\n\n" + prompt[idx:]


def fill_opening_rules(rendered: str) -> str:
    """Put OPENING_RULES into a rendered Gemini writer prompt at its {{OPENING_RULES}}
    placeholder, or append it when a (fallback) template has none."""
    if OPENING_RULES_PLACEHOLDER in rendered:
        return rendered.replace(OPENING_RULES_PLACEHOLDER, OPENING_RULES)
    if OPENING_RULES in rendered:
        return rendered
    return rendered.rstrip("\n") + "\n\n" + OPENING_RULES + "\n"
