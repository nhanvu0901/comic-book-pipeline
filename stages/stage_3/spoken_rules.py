"""Single source of truth for how SPOKEN narration must be written.

The narration is read by local Chatterbox TTS, which does no number/symbol
normalisation and treats only commas and periods as pauses. Every writer prompt
(inline system prompts in stage_3 and the prompts/*.md templates) carries this
exact block. ``tests/test_spoken_rules.py`` asserts presence and that the .md
copies never drift from this constant.
"""
from __future__ import annotations

SPOKEN_TEXT_RULES = """SPOKEN TEXT RULES (the narration is read aloud by a text-to-speech voice; these bind every spoken line):
- Write for the ear: every word exactly as the narrator says it.
- Numbers in words, never digits. Issue numbers as "issue fifteen" or "issue one twenty-five", never "#15". Years as spoken: "twenty nineteen", "nineteen eighty-four".
- No symbols (# % & $ / + @ * =) and no abbreviations (Mr., Dr., vs., No., Vol.): write the full words.
- No ALL-CAPS words, no emoji, no parentheses or brackets, no colons, semicolons, ellipses or em dashes. A real initialism is spaced out ("F. B. I."). Pace ONLY with commas and periods.
- Sentences are mostly 8 to 22 words, one clear idea each. Never a standalone fragment under 4 words ("Silence." / "He's gone."): fold it into the neighbouring sentence with a comma.
- Flow: link sentences with natural spoken connectives (then, but, so, until, when, which means) instead of stacked fragments. Vary the rhythm, a longer sentence then a shorter one. Each sentence picks up from the last.
- Commas where a narrator would breathe; no run-on chains of "and".
- Do not stack several unusual proper names in one sentence. Name a character the same way each time (respect the glossary).
- A quoted line of dialogue is fine when introduced naturally ("he says, ..."); keep it short.
- Read every sentence silently aloud; if you would stumble, rewrite it.
- Where this rules block conflicts with an older word-budget, hook or punchiness rule, the shape here wins; the word budgets still hold."""


def with_spoken_rules(prompt: str, marker: str = "Return ONLY JSON") -> str:
    """Insert SPOKEN_TEXT_RULES into ``prompt`` just before the LAST ``marker``
    (the output-format tail), so the rules sit with the other writer rules.
    Raises if the marker is missing, so a prompt edit can never silently drop it."""
    idx = prompt.rfind(marker)
    if idx < 0:
        raise ValueError(f"spoken rules marker {marker!r} not found in prompt")
    return prompt[:idx] + SPOKEN_TEXT_RULES + "\n\n" + prompt[idx:]


_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
         "fourteen fifteen sixteen seventeen eighteen nineteen").split()
_TENS = {2: "twenty", 3: "thirty", 4: "forty", 5: "fifty", 6: "sixty", 7: "seventy",
         8: "eighty", 9: "ninety"}


def _below_hundred(n: int) -> str:
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens] + (f"-{_ONES[ones]}" if ones else "")


def year_words(year) -> str:
    """A four-digit year as a narrator says it: 2019 -> "twenty nineteen",
    1984 -> "nineteen eighty-four", 2005 -> "two thousand five", 1900 -> "nineteen
    hundred". Anything that is not a four-digit year comes back unchanged (as text)."""
    s = str(year).strip()
    if not (s.isdigit() and len(s) == 4):
        return s
    n = int(s)
    hi, lo = divmod(n, 100)
    if n % 1000 == 0:
        return f"{_ONES[n // 1000]} thousand"
    if 2000 < n < 2010:
        return f"two thousand {_ONES[lo]}"
    if lo == 0:
        return f"{_below_hundred(hi)} hundred"
    if lo < 10:
        return f"{_below_hundred(hi)} oh {_ONES[lo]}"
    return f"{_below_hundred(hi)} {_below_hundred(lo)}"
