"""The rules the QA discovery prompts share, written once.

The gate (qa_discovery_gate.py) rejects a candidate whose question does not name its hero, whose issue
label carries the wrong year, or whose quote is not one verbatim passage. The live runs lost most of
their yield to exactly those model slips, so every prompt that asks for candidates states the rules in
the same words. ``research_prompts/discover_qa.v2.md`` is a plain text file and carries copies of
these sentences; tests/test_qa_gate_r4_upstream.py fails if the copies drift.
"""

from __future__ import annotations

NAME_THE_HERO = (
    "The question MUST name the hero or team itself (the canonical_anchor, or the name fans use for "
    "them); a question that only says \"he\", a first name, a villain, a supporting character or "
    "\"this power\" is rejected before it is read."
)

REAL_RELEASE_YEAR = (
    "The (YYYY) in every issue label is that issue's REAL original release year, 2010 or later, as "
    "the cited page states it: never the series launch year, a reprint's year, an article date or a "
    "guess. If the page does not show it, drop the candidate."
)

EXACT_QUOTE = (
    "Copy each quote EXACTLY as ONE contiguous passage of the synopsis section of the cited page: no "
    "edits, no added names or words, no ellipsis, no joining of separate sentences."
)

HOOK_RULES = (
    "viewer_hook is a short curiosity question about the SAME answer, or the empty string. The "
    "viewer_hook must name the same hero or team as the question, in words (never a pronoun such as "
    "\"this power\" or \"him\"), reuse at least one key noun of the question, and be the same kind of "
    "question: a Why/How/What-is question gets a Why/How/What/Can question, a list question "
    "(What are some ...) gets a list-shaped one. It ends with ? and never copies or merely shortens "
    "the question. If no distinct hook fits, return the empty string."
)
