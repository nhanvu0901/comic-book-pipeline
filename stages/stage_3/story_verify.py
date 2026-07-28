"""STORY_VERIFY critic for the explore_answer (Q&A) mode.

The Q&A writer builds scenes from Stage 1's WEB research, not from the comic's
own pages — so it can confidently state the opposite of what the issue actually
shows. Real miss: a scene wrote "an accident swaps their minds" while the page's
own dialogue reads "Telekinesis was a ruse... with one touch of a button, our
minds will switch bodies" (deliberate, not an accident), and "both men end up
trapped, unable to reclaim who they were" against an ending that has Doom
declare himself "an HONORABLE man" — the research-driven prose inverted the
story.

This module fact-checks each body scene against the comic's OWN preprocessed
evidence (OCR dialogue first, panel descriptions second), per chapter. A
CONTRADICTED claim triggers ONE grounded re-write + re-verify; if it still
contradicts, the original text ships with an unresolved issue string (same
pattern as the length-band guard — the pipeline never blocks on it). NOT_FOUND
never blocks (the evidence window is a partial slice of the issue, so most
whole-issue summary claims are simply absent, not wrong).

Knob: env STORY_VERIFY (default ON); STORY_VERIFY=0 turns it off.
Additive — only explore_answer imports this file.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from config import FIDELITY_LLM_MODELS, PROJECTS_ROOT
from ._llm import call_with_chain
from .beat_split import _verbatim_ok
from .schema import Narration
from .write_script import _extract_json
from .._arc import issue_index_of_page

_SUPPORTED, _NOT_FOUND, _CONTRADICTED = "SUPPORTED", "NOT_FOUND", "CONTRADICTED"


@dataclass
class Claim:
    text: str
    verdict: str  # SUPPORTED | NOT_FOUND | CONTRADICTED
    evidence: str = ""


def _story_verify_on() -> bool:
    return os.getenv("STORY_VERIFY", "1").lower() not in ("0", "false", "no")


def _max_chars() -> int:
    try:
        return max(500, int(os.getenv("STORY_VERIFY_MAX_CHARS", "8000")))
    except ValueError:
        return 8000


def _load_preprocessed(project: str) -> list[dict]:
    """Every preprocessed/page_*.json for `project`, sorted by page_number.
    Returns [] if the directory is missing or empty (never raises)."""
    import json
    prep = PROJECTS_ROOT / project / "preprocessed"
    if not prep.is_dir():
        return []
    pages: list[dict] = []
    for f in sorted(prep.glob("page_*.json")):
        try:
            pages.append(json.loads(f.read_text()))
        except (OSError, ValueError):
            continue
    pages.sort(key=lambda p: int(p.get("page_number", 0) or 0))
    return pages


def gather_evidence(project: str, chapter_pages: list[dict]) -> str:
    """Evidence text for ONE chapter — verbatim dialogue first, panel descriptions
    second — capped to STORY_VERIFY_MAX_CHARS. Dialogue leads because it is the
    ground-truth OCR the writer never sees; descriptions are softer VLM output.
    `chapter_pages` = the preprocessed page dicts of that chapter (caller filters).

    ponytail: caps by front-loading dialogue then truncating — a long chapter's
    late dialogue falls outside the window (accepted: NOT_FOUND, never a false
    CONTRADICTED). Sample across the chapter if coverage matters."""
    dialog_lines: list[str] = []
    desc_lines: list[str] = []
    for page in chapter_pages:
        pn = page.get("page_number", "?")
        for panel in page.get("panels") or []:
            desc = str(panel.get("description", "")).strip()
            if desc:
                desc_lines.append(f"p{pn}: {desc}")
            for d in panel.get("dialog") or []:
                text = " ".join(str(d.get("text", "")).split()).strip()
                if not text:
                    continue
                speaker = str(d.get("speaker", "") or "").strip()
                dialog_lines.append(f'p{pn} {speaker}: "{text}"' if speaker else f'p{pn}: "{text}"')
    parts = [f"=== EVIDENCE (comic: {project}) ==="]
    if dialog_lines:
        parts.append("DIALOGUE (verbatim OCR from the comic — strongest truth):\n" + "\n".join(dialog_lines))
    if desc_lines:
        parts.append("PANEL DESCRIPTIONS (softer, VLM-generated):\n" + "\n".join(desc_lines))
    return "\n\n".join(parts)[: _max_chars()]


_VERIFY_SYSTEM = """You are a FACT-CHECKER for a comic-trivia narration. You are given ONE spoken \
SCENE and the comic's OWN EVIDENCE (verbatim dialogue + panel descriptions from the actual pages). \
Split the scene into its ATOMIC factual claims (each: one who-did-what or what-happened), then judge \
EACH claim against the evidence ALONE.

VERDICT per claim (use these EXACT strings):
- "SUPPORTED": the evidence directly confirms it.
- "CONTRADICTED": the evidence directly states the OPPOSITE (a different actor, method, motive, or \
outcome). Use this ONLY for a real conflict, never for something merely absent.
- "NOT_FOUND": the evidence neither confirms nor denies it. This is the DEFAULT — the evidence is a \
PARTIAL slice of the issue, so most whole-issue summary claims are simply absent, NOT wrong. When \
unsure, return NOT_FOUND.

Rules:
- Dialogue is the strongest truth; a panel description is softer (VLM guesswork) — never CONTRADICT on \
a description alone.
- IGNORE the source-comic citation ("in Infamous Iron Man #1", issue numbers) — that is a credit, not \
a factual claim to check.
- Be conservative. CONTRADICTED is a strong call reserved for a plain conflict a reader would catch \
(e.g. scene says "an accident swapped their minds" but dialogue says the swap was a deliberate trick).
- Give a SHORT evidence quote (<=20 words) for each SUPPORTED/CONTRADICTED verdict; "" for NOT_FOUND.

Return JSON ONLY, no markdown: {"claims":[{"claim":"...","verdict":"SUPPORTED|NOT_FOUND|CONTRADICTED","evidence":"..."}]}."""


def verify_scene(
    scene_text: str, evidence: str,
    *, model: str | None = None, progress: Callable[[str], None] | None = None,
) -> list[Claim]:
    """One LLM call: split `scene_text` into atomic claims, each verdicted against
    `evidence`. Strict JSON parse; on any failure degrades to a single NOT_FOUND
    claim (the whole scene) — never raises, so it can only add a soft signal."""
    log = progress or (lambda _m: None)
    scene_text = str(scene_text or "").strip()
    if not scene_text or not evidence.strip():
        return [Claim(scene_text, _NOT_FOUND)]
    user = (
        f"{evidence}\n\n"
        f"=== SCENE TO CHECK ===\n{scene_text}\n\n"
        'Return JSON {"claims":[{"claim":"...","verdict":"...","evidence":"..."}]}.'
    )
    chain = [model] if model else list(FIDELITY_LLM_MODELS)
    try:
        raw, _mdl = call_with_chain(
            system=_VERIFY_SYSTEM, user=user, models=chain, max_tokens=1200,
            progress=progress, label="story_verify", validator=lambda c: '"claims"' in c)
    except RuntimeError as exc:
        log(f"[story_verify]   verify chain failed — degrading to NOT_FOUND: {exc}")
        return [Claim(scene_text, _NOT_FOUND)]
    parsed = _extract_json(raw)
    rows = parsed.get("claims") if isinstance(parsed, dict) else None
    if not isinstance(rows, list) or not rows:
        return [Claim(scene_text, _NOT_FOUND)]
    claims: list[Claim] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        text = str(r.get("claim", "")).strip()
        verdict = str(r.get("verdict", "")).strip().upper()
        if verdict not in (_SUPPORTED, _NOT_FOUND, _CONTRADICTED):
            verdict = _NOT_FOUND  # unknown label from the model = treat as absent, never a false block
        if text:
            claims.append(Claim(text, verdict, str(r.get("evidence", "")).strip()))
    return claims or [Claim(scene_text, _NOT_FOUND)]


_REWRITE_SYSTEM = """You are a narration FIXER for a comic-trivia YouTube Short. A scene contains a \
claim that CONTRADICTS the comic's own evidence. Rewrite the ONE scene so it is faithful to the \
EVIDENCE, fixing only what conflicts and keeping everything else.

HARD RULES:
- Correct the contradicted fact to what the EVIDENCE actually shows (dialogue is the truth).
- Keep the SAME source-comic mention and the same overall shape; length within +/-20% of the original.
- Plain B2 English, ONE event per sentence, name the entity. Do not narrate the artwork.
- "visual_beats" = the scene's OWN words split at punctuation/connectives into 2-3 drawable fragments; \
concatenated verbatim they must equal "text" exactly (drop only a comma/dash at a split). A short \
scene may be ONE fragment = the whole text.
- Return JSON ONLY, no markdown: {"text":"...","visual_beats":["...","..."]}."""


def _rewrite_scene(
    scene_text: str, evidence: str, contradicted: list[Claim],
    *, model: str | None = None, progress: Callable[[str], None] | None = None,
) -> tuple[str, list[str]] | None:
    """One grounded re-write of a contradicted scene. Returns (new_text, visual_beats)
    or None on failure. visual_beats fall back to [new_text] when the model's split
    is not verbatim (a valid single-fragment scene). Never raises."""
    log = progress or (lambda _m: None)
    fixes = "\n".join(f"- {c.text} (evidence: {c.evidence})" for c in contradicted) or "(see evidence)"
    user = (
        f"{evidence}\n\n"
        f"=== SCENE (has a contradicted claim) ===\n{scene_text}\n\n"
        f"CONTRADICTED CLAIMS TO FIX:\n{fixes}\n\n"
        'Return JSON {"text":"...","visual_beats":["...","..."]}.'
    )
    chain = [model] if model else list(FIDELITY_LLM_MODELS)
    try:
        raw, _mdl = call_with_chain(
            system=_REWRITE_SYSTEM, user=user, models=chain, max_tokens=800,
            progress=progress, label="story_rewrite", validator=lambda c: '"text"' in c)
    except RuntimeError as exc:
        log(f"[story_verify]   rewrite chain failed: {exc}")
        return None
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        return None
    new_text = str(parsed.get("text", "")).strip()
    if not new_text:
        return None
    beats = [str(b).strip() for b in (parsed.get("visual_beats") or []) if str(b).strip()]
    if not _verbatim_ok(new_text, beats):
        beats = [new_text]  # single-fragment fallback — Stage 5 holds one panel
    return new_text, beats


def run_story_verify(
    narration: Narration, project: str,
    progress: Callable[[str], None] | None = None,
) -> list[str]:
    """Fact-check every BODY scene of `narration` against the comic's evidence,
    per chapter. Mutates a scene in place when a grounded re-write resolves a
    contradiction; otherwise keeps the original text and records an unresolved
    issue string. Returns the list of unresolved-contradiction issues (empty when
    all scenes are clean or the knob is off). Never raises."""
    log = progress or (lambda _m: None)
    if not _story_verify_on():
        return []
    pages = _load_preprocessed(project)
    if not pages:
        log("[story_verify] no preprocessed pages — skipping")
        return []

    page_chapter = {int(p.get("page_number", 0) or 0): issue_index_of_page(p) for p in pages}
    chapters: dict[int, list[dict]] = {}
    for p in pages:
        chapters.setdefault(issue_index_of_page(p), []).append(p)

    evidence_cache: dict[int, str] = {}
    issues: list[str] = []
    for scene in narration.scenes:
        if scene.is_intro or scene.is_outro or not str(scene.text).strip():
            continue
        chapter = page_chapter.get(int(scene.page_ref or 0))
        if chapter is None:
            log(f"[story_verify]   scene S{scene.scene_id}: page {scene.page_ref} maps to no chapter — skipping")
            continue
        if chapter not in evidence_cache:
            evidence_cache[chapter] = gather_evidence(project, chapters.get(chapter, []))
        evidence = evidence_cache[chapter]

        claims = verify_scene(scene.text, evidence, progress=progress)
        contradicted = [c for c in claims if c.verdict == _CONTRADICTED]
        verdicts = ", ".join(f"{c.verdict[0]}" for c in claims)
        log(f"[story_verify]   scene S{scene.scene_id} (ch{chapter}): {len(claims)} claim(s) [{verdicts}]")
        if not contradicted:
            continue

        rewrite = _rewrite_scene(scene.text, evidence, contradicted, progress=progress)
        resolved = False
        if rewrite:
            new_text, new_beats = rewrite
            recheck = verify_scene(new_text, evidence, progress=progress)
            still = [c for c in recheck if c.verdict == _CONTRADICTED]
            if not still:
                scene.text = new_text
                scene.visual_beats = new_beats
                scene.word_count = len(new_text.split())
                log(f"[story_verify]   scene S{scene.scene_id} re-write resolved contradiction")
                resolved = True
                contradicted = []
        if not resolved:
            for c in contradicted:
                issues.append(f"STORY_VERIFY: scene {scene.scene_id} contradicted — {c.text}")
            log(f"[story_verify]   scene S{scene.scene_id} still contradicted — keeping original text")

    if issues:
        narration.total_word_count = sum(len(str(s.text).split()) for s in narration.scenes)
    return issues


# ─── CLI dry-run: read-only verdict audit of an existing narration.json ──────────
def _dry_run(project: str) -> int:
    import json
    root = PROJECTS_ROOT / project
    nar_path = root / "narration.json"
    if not nar_path.exists():
        print(f"[story_verify] no narration.json at {nar_path}", file=sys.stderr)
        return 1
    nar = json.loads(nar_path.read_text())
    if nar.get("mode") != "explore_answer":
        print(f"[story_verify] note: mode is {nar.get('mode')!r}, not explore_answer — checking anyway")
    pages = _load_preprocessed(project)
    if not pages:
        print(f"[story_verify] no preprocessed pages for {project}", file=sys.stderr)
        return 1
    page_chapter = {int(p.get("page_number", 0) or 0): issue_index_of_page(p) for p in pages}
    chapters: dict[int, list[dict]] = {}
    for p in pages:
        chapters.setdefault(issue_index_of_page(p), []).append(p)
    evidence_cache: dict[int, str] = {}

    print(f"\n=== STORY_VERIFY dry-run: {project} (READ-ONLY) ===\n")
    log = lambda m: print(m)
    totals = {_SUPPORTED: 0, _NOT_FOUND: 0, _CONTRADICTED: 0}
    for scene in nar.get("scenes") or []:
        if scene.get("is_intro") or scene.get("is_outro") or not str(scene.get("text", "")).strip():
            continue
        sid = scene.get("scene_id", "?")
        chapter = page_chapter.get(int(scene.get("page_ref", 0) or 0))
        if chapter is None:
            print(f"S{sid}: page {scene.get('page_ref')} -> no chapter, skipped\n")
            continue
        if chapter not in evidence_cache:
            evidence_cache[chapter] = gather_evidence(project, chapters.get(chapter, []))
        claims = verify_scene(scene["text"], evidence_cache[chapter], progress=log)
        print(f"\nS{sid} (ch{chapter}): {str(scene['text'])[:90]}")
        for c in claims:
            totals[c.verdict] = totals.get(c.verdict, 0) + 1
            quote = f"  <- {c.evidence}" if c.evidence else ""
            print(f"   [{c.verdict:12}] {c.text}{quote}")
    print(f"\n=== TOTAL: {totals[_SUPPORTED]} SUPPORTED, {totals[_NOT_FOUND]} NOT_FOUND, "
          f"{totals[_CONTRADICTED]} CONTRADICTED ===")
    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="STORY_VERIFY dry-run (read-only verdict audit).")
    ap.add_argument("--project", required=True, help="project slug under projects/")
    args = ap.parse_args()
    raise SystemExit(_dry_run(args.project))
