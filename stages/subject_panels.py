"""Q&A subject-panel ranking (shared by Stage 2 preprocess and Stage 5 render).

A Q&A ("explore_answer") Short answers ONE question about ONE famous character —
"Who has stopped the unstoppable Juggernaut?" — but its answer ITEMS name DIFFERENT
characters (Captain Universe, Colossus, Nimrod…). The intro/outro must feature the
QUESTION'S SUBJECT (Juggernaut), not whichever answer-entity a free matcher liked.

Right after Stage 2 preprocess, `build_subject_panels` scans EVERY panel in the
project and ranks them by how strongly they feature that subject (name in the panel's
`characters`, `description`, or OCR dialog), writing `projects/<slug>/subject_panels.json`:

    {"subject": "Juggernaut", "panels": [{"page": 66, "panel": 2, "score": 5.0}, ...]}

Pure text matching — no VLM, no embeddings — so it's fast and idempotent. Stage 5's
locked-Q&A render reads this file directly to build a multi-panel subject intro
(see stages/stage_5/shots.py::_qa_subject_sequence). A HAND-WRITTEN file carrying
`"manual": true` is never overwritten, so Master can pick panels by hand.

A per-panel entry may also carry `"force_intro": true` (set by review_gate.py's money-shot
funnel via `_pin_money_intro` on the confirmed payoff panel) — `_qa_subject_sequence`
lets that panel bookend the video even when it's ALSO locked to a body beat, instead of
dropping it under the usual no-reuse rule. This file is loaded as plain JSON
(`load_subject_panels`), so any extra key written directly to it (like `force_intro`)
survives untouched — only a full `build_subject_panels` rebuild replaces the panel list.

`derive_subject`'s single best guess sometimes names a LOCATION or leftover phrase, not a
drawable character ("Who Has Actually Broken Into the Batcave?" → "Batcave" — no panel's
`characters`/`description` literally says "Batcave", so scoring draws a blank). Rather than
dead-end there, `build_subject_panels` tries a RANKED LIST of candidates — the question's
own phrase(s) first, then whichever character name the answer items mention most — and
keeps the first one `score_panels` actually finds panels for. General text heuristic, no
per-comic special-casing.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Callable

from config import PROJECTS_ROOT

# Capitalized question words / titles / pronouns that are never the subject on their own.
# "Doctor" is here so "Doctor Doom" reduces to the matchable name "Doom"; the interrogatives
# and pronouns keep "Who"/"What"/sentence-leading "He"/"They" out of the candidate set.
_SUBJ_STOP = frozenset((
    "The", "This", "That", "Who", "Whom", "Whose", "What", "When", "Where",
    "Why", "How", "Which", "A", "An", "And", "But", "Or", "Of", "In", "On",
    "Doctor", "Doc", "Mr", "Mister", "Ms", "Mrs", "Lord", "Lady", "King", "Queen",
    "He", "She", "It", "They", "We", "You",
))


def _name_phrases(text: str) -> list[str]:
    """Every run of Capitalized words in `text` as a candidate name-phrase, in the order
    they occur (a possessive `'s` terminates a run, so "Ghost Rider's Penance Stare" splits
    into "Ghost Rider" and "Penance Stare"); stop-words dropped, empty phrases skipped."""
    text2 = re.sub(r"[’'‘]s\b", " | ", text)  # possessive ends a name phrase
    out: list[str] = []
    for ph in re.findall(r"[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*", text2):
        toks = [t for t in ph.split() if t not in _SUBJ_STOP]
        if toks:
            out.append(" ".join(toks))
    return out


def _item_corpus(items: list[dict]) -> list[str]:
    """Lower-cased how_or_why/entity/drawable_moment blob per answer item."""
    return [
        (str(it.get("how_or_why", "")) + " " + str(it.get("entity", "")) + " "
         + str(it.get("drawable_moment", ""))).lower()
        for it in items
    ]


def _question_candidates(question: str, items: list[dict]) -> list[str]:
    """The question's Capitalized name-phrases, ranked by how many answer items mention
    each — ties break toward the phrase that comes FIRST in the question (the possessor /
    focal character leads: "Who survived [Ghost Rider]'s Penance Stare"). Empty when the
    question names no capitalized phrase. `derive_subject`'s ranking, factored out so
    `build_subject_panels` can also try candidates BEYOND just the winner."""
    phrases = re.findall(r"[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*", re.sub(r"[’'‘]s\b", " | ", question))
    cands: list[tuple[str, int]] = []            # (normalized phrase, question order)
    for order, ph in enumerate(phrases):
        toks = [t for t in ph.split() if t not in _SUBJ_STOP]
        if toks:
            cands.append((" ".join(toks), order))
    if not cands:
        return []
    corpus = _item_corpus(items)

    def item_hits(phrase: str) -> int:
        low = phrase.lower()
        return sum(1 for t in corpus if low in t)

    cands.sort(key=lambda c: (item_hits(c[0]), -c[1]), reverse=True)
    seen: set[str] = set()
    ranked: list[str] = []
    for phrase, _ in cands:
        if phrase not in seen:
            seen.add(phrase)
            ranked.append(phrase)
    return ranked


def _item_name_candidates(items: list[dict]) -> list[str]:
    """Capitalized name-phrases across answer items' how_or_why/entity/drawable_moment,
    ranked by how many DIFFERENT items mention each (counted once per item, so one wordy
    item can't out-vote several items agreeing) — ties favor the phrase seen FIRST. General
    fallback pool: when the question's own phrase is a location/thing with zero matching
    panels, the character every item keeps describing is very likely the real subject."""
    counts: Counter[str] = Counter()
    order: dict[str, int] = {}
    for it in items:
        text = (str(it.get("how_or_why", "")) + " " + str(it.get("entity", "")) + " "
                + str(it.get("drawable_moment", "")))
        for name in dict.fromkeys(_name_phrases(text)):    # de-dup within THIS item
            counts[name] += 1
            order.setdefault(name, len(order))
    return sorted(counts, key=lambda n: (-counts[n], order[n]))


def derive_subject(answer_context: dict) -> str:
    """The question's SUBJECT character — the recognizable A-tier name the whole video
    is about (Juggernaut, Ghost Rider, Doom), NOT the varied answer-entities.

    Heuristic (text-only, general): take every run of Capitalized words in the question
    as a candidate name-phrase (a possessive `'s` terminates a phrase, so "Ghost Rider's
    Penance Stare" splits into "Ghost Rider" and "Penance Stare"), drop stop-words, then
    pick the candidate that appears in the MOST answer items' how_or_why/entity/moment
    text — ties break toward the phrase that comes FIRST in the question (the possessor /
    focal character leads: "Who survived [Ghost Rider]'s Penance Stare"). "" if the
    question names no capitalized subject.
    """
    q = str(answer_context.get("question", "") or "")
    if not q:
        return ""
    ranked = _question_candidates(q, answer_context.get("items") or [])
    return ranked[0] if ranked else ""


def _panel_subject_score(subject: str, panel: dict) -> float:
    """How strongly `panel` features `subject`. `characters` is the cleanest signal
    (Magi's cluster names) so it weighs most; then the VLM description; then dialog
    text/OCR (counted once — a name repeated in bubbles isn't more "the subject").
    ponytail: plain case-insensitive substring — no alias/synonym table, add one if a
    subject renders under a nickname the panels use ("Cain Marko" for Juggernaut)."""
    subj = subject.lower().strip()
    if not subj:
        return 0.0
    score = 0.0
    for ch in panel.get("characters") or []:
        if subj in str(ch).lower():
            score += 3.0
    if subj in str(panel.get("description", "")).lower():
        score += 2.0
    for d in panel.get("dialog") or []:
        blob = (str(d.get("text", "")) + " " + str(d.get("ocr", ""))).lower()
        if subj in blob:
            score += 1.0
            break
    return score


def score_panels(subject: str, pages: list[dict]) -> list[dict]:
    """Rank every story-page panel by subject presence, score-desc then bigger-first
    (a larger panel is a stronger hook frame). Only panels with score > 0 are kept.
    Returns [{"page": <page_number>, "panel": <index>, "score": float}, ...]."""
    scored: list[dict] = []
    for page in pages:
        if not page.get("is_story_page", True):
            continue
        pn = int(page.get("page_number", 0) or 0)
        for idx, panel in enumerate(page.get("panels") or []):
            sc = _panel_subject_score(subject, panel)
            if sc > 0:
                bb = panel.get("bbox") or {}
                area = int(bb.get("w", 0) or 0) * int(bb.get("h", 0) or 0)
                scored.append({"page": pn, "panel": idx, "score": round(sc, 3), "_area": area})
    scored.sort(key=lambda r: (r["score"], r["_area"]), reverse=True)
    for r in scored:
        r.pop("_area", None)
    return scored


def _subject_path(project: str) -> Path:
    return PROJECTS_ROOT / project / "subject_panels.json"


def build_subject_panels(
    project: str, answer_context: dict, pages: list[dict],
    *, log: Callable[[str], None] = print,
) -> Path | None:
    """Write projects/<slug>/subject_panels.json ranking every panel by subject presence.

    Tries a RANKED LIST of subject candidates — the question's own capitalized phrase(s)
    first (same ranking `derive_subject` uses), then the character names the answer items
    mention most — and keeps the FIRST candidate `score_panels` finds >=1 panel for. If
    every candidate draws a blank, falls back to today's behavior: write the primary
    (`derive_subject`) candidate with an EMPTY panel list rather than silently skip.

    A hand-written file marked `"manual": true` is left untouched (Master's picks win).
    Returns the path (written or preserved), or None when no subject could be derived."""
    out_path = _subject_path(project)
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text())
        except Exception:
            existing = {}
        if existing.get("manual"):
            log(f"[subject-panels] manual override present — keeping {out_path.name}")
            return out_path

    items = answer_context.get("items") or []
    q = str(answer_context.get("question", "") or "")
    candidates: list[str] = []
    for name in _question_candidates(q, items) + _item_name_candidates(items):
        if name not in candidates:
            candidates.append(name)
    if not candidates:
        log("[subject-panels] no subject derived from question — skipping")
        return None

    primary = candidates[0]
    subject, ranked = primary, []
    for cand in candidates:
        hit = score_panels(cand, pages)
        if hit:
            subject, ranked = cand, hit
            break
    if subject != primary:
        log(f"[subject-panels] subject={primary!r} → 0 panels, fallback → {subject!r}")
    out_path.write_text(json.dumps({"subject": subject, "panels": ranked}, indent=2))
    log(f"[subject-panels] subject={subject!r} → {len(ranked)} panel(s) ranked → {out_path.name}")
    return out_path


def load_subject_panels(project: str) -> dict:
    """Parsed subject_panels.json, or {} when absent/unreadable (Stage 5 falls back)."""
    path = _subject_path(project)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


if __name__ == "__main__":
    # Self-check: subject derivation + scoring + manual override, no I/O deps.
    ac = {
        "question": "Who has survived Ghost Rider's Penance Stare?",
        "items": [
            {"entity": "The Punisher", "how_or_why": "Ghost Rider hit Frank with the Penance Stare."},
            {"entity": "Deadpool", "how_or_why": "Ghost Rider unleashed the Penance Stare on Deadpool."},
            {"entity": "Carnage", "how_or_why": "Danny Ketch Ghost Rider tried the Penance Stare."},
            {"entity": "King Thanos", "how_or_why": "Cosmic Ghost Rider served Thanos."},
        ],
    }
    assert derive_subject(ac) == "Ghost Rider", derive_subject(ac)
    assert derive_subject({"question": "Who beat the real Doctor Doom?",
                           "items": [{"how_or_why": "Doom lost."}]}) == "Doom"
    assert derive_subject({"question": "no caps here"}) == ""
    pages = [
        {"page_number": 5, "is_story_page": True, "panels": [
            {"bbox": {"w": 100, "h": 100}, "characters": ["Ghost Rider"],
             "description": "Ghost Rider on a bike", "dialog": [{"text": "Ghost Rider speaks"}]},
            {"bbox": {"w": 400, "h": 400}, "characters": ["a man"],
             "description": "Ghost Rider looms", "dialog": []},
            {"bbox": {"w": 50, "h": 50}, "characters": ["a woman"], "description": "empty street"},
        ]},
        {"page_number": 6, "is_story_page": False, "panels": [
            {"bbox": {"w": 999, "h": 999}, "characters": ["Ghost Rider"], "description": "cover"}]},
    ]
    ranked = score_panels("Ghost Rider", pages)
    assert [(r["page"], r["panel"]) for r in ranked] == [(5, 0), (5, 1)], ranked  # p6 skipped (not story)
    assert ranked[0]["score"] == 6.0 and ranked[1]["score"] == 2.0, ranked
    print("subject_panels self-check OK")
