"""Panel-walk narrator — long-form recap that reads the panels in order.

Every other mode works backwards: an LLM plans beats from a wiki plot, writes prose, and a
matcher then hunts for panels that fit the prose. This mode inverts it. The panels ARE the
script: walk them in reading order, write one sentence per panel, and the (page, panel) a
sentence belongs to is known by construction — no cosine, no DTW, no anchor drift, because
there is nothing to match.

Consequences of that inversion, all deliberate:
  * no Stage 1 research and no story architect — the comic is the outline
  * no embedding of any kind (nothing to rank)
  * one LLM call per PAGE, with the last few sentences passed forward so the prose stays
    continuous across page boundaries
  * output is long: ~1 sentence x ~90 panels for a single issue, so minutes, not seconds

Needs VLM_EXTRACT=1. Under the manual-first default (VLM_EXTRACT=0) a panel's `description`
is concatenated dialog OCR, not a visual description, and silent panels come back as the
literal string "Wordless transition/SFX panel" — a writer fed that produces garbage. This
module refuses to run rather than discover it in the render.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from config import PROJECTS_ROOT
from ..stage_3._llm import call_with_chain
from ..stage_3.schema import Narration, Scene
from ..stage_3.write_script import _wps_for

# One LLM call covers one page. Pages carry 1-7 panels, so this keeps every call small
# enough to stay reliable while giving the model a whole page of visual context at once.
_CONTEXT_SENTENCES = 3      # how many previous sentences the next page's call sees
_MAX_PANELS_PER_PAGE = 12   # sanity bound; Magi has never returned more on a real page

_SYSTEM = """You are PanelWalker. You narrate a comic book by reading its panels in order.

You will be given the panels of ONE page, numbered in reading order, each with what the art
shows and any dialogue printed in it. You return EXACTLY ONE sentence per panel.

RULES
1) ONE sentence per panel, in the given order. Never merge two panels into one sentence and
   never split one panel across two sentences. The count must match exactly.
2) Narrate ONLY what that panel shows. No speculation about what happens next, no summary of
   what the issue is "about", no off-panel facts.
3) Plain past tense, everyday words, 8-20 words per sentence. A viewer hears this once.
4) Dialogue is evidence, not script. Use it to say who wants what ("he told her to run"),
   or quote at most a few words when the exact line matters. Never transcribe a whole balloon.
5) Name a character only when the panel data names them. If it does not, use a plain
   description ("the masked man", "a soldier") — never guess a famous name from a costume.
6) Silent panels still get a sentence: describe the image ("smoke drifted over the wreckage").
7) Continuity: you are given the last sentences you wrote. Do not repeat them, do not
   re-introduce a character already introduced, and open naturally from where they left off.

Return ONLY JSON: {"sentences": ["...", "..."]} with one entry per panel, in panel order."""


def _load_pages(project: str) -> list[dict]:
    prep = PROJECTS_ROOT / project / "preprocessed"
    if not prep.exists():
        raise FileNotFoundError(f"preprocessed/ missing: {prep}. Run Stage 2 first.")
    pages = []
    for f in sorted(prep.glob("page_*.json")):
        try:
            pages.append(json.loads(f.read_text()))
        except json.JSONDecodeError:
            continue
    if not pages:
        raise RuntimeError(f"no parseable pages in {prep}")
    pages = [p for p in pages if p.get("is_story_page")]
    pages.sort(key=lambda p: int(p.get("page_number", 0)))
    return pages


_OCR_SOUP = re.compile(r"Wordless transition/SFX panel", re.I)


def assert_vlm_descriptions(pages: list[dict]) -> None:
    """Fail loudly when Stage 2 ran Magi-only, instead of narrating OCR soup.

    With VLM_EXTRACT=0 the `description` field holds concatenated dialogue and silent panels
    get a fixed placeholder string. That is indistinguishable from a real description to a
    prompt but useless to narrate, so check for the placeholder — it is the reliable tell."""
    panels = [p for pg in pages for p in (pg.get("panels") or [])]
    if not panels:
        raise RuntimeError("no panels found — Stage 2 produced no panel boxes")
    placeholders = sum(1 for p in panels if _OCR_SOUP.search(str(p.get("description", ""))))
    if placeholders:
        raise RuntimeError(
            f"{placeholders}/{len(panels)} panels carry the Magi-only placeholder description, "
            f"so Stage 2 ran with VLM_EXTRACT=0 and there are no visual descriptions to "
            f"narrate. Re-run: VLM_EXTRACT=1 python -m stages.stage_2 --project <slug> --force"
        )


def _panel_block(panels: list[dict]) -> str:
    lines = []
    for i, p in enumerate(panels, start=1):
        desc = " ".join(str(p.get("description", "")).split()).strip() or "(no description)"
        chars = [c for c in (p.get("characters") or []) if str(c).strip()]
        dialog = [" ".join(str(d.get("text") or d.get("ocr") or "").split()).strip()
                  for d in (p.get("dialog") or [])]
        dialog = [d for d in dialog if d]
        lines.append(f"PANEL {i}\n  shows: {desc}")
        if chars:
            lines.append(f"  characters: {', '.join(str(c) for c in chars)}")
        if dialog:
            lines.append("  dialogue: " + " | ".join(f'"{d}"' for d in dialog[:6]))
    return "\n".join(lines)


def _valid_for(n_panels: int) -> Callable[[str], bool]:
    def _v(out: str) -> bool:
        try:
            data = json.loads(_strip_fence(out))
            sents = data.get("sentences")
        except Exception:
            return False
        return (isinstance(sents, list) and len(sents) == n_panels
                and all(isinstance(s, str) and s.strip() for s in sents))
    return _v


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t


def narrate_page(panels: list[dict], page_number: int, context: list[str], *,
                 progress: Callable[[str], None] | None = None) -> tuple[list[str], str]:
    """One LLM call → one sentence per panel. Returns (sentences, model_used)."""
    prior = ("\nTHE LAST SENTENCES YOU WROTE (continue from these, do not repeat them):\n"
             + "\n".join(f"- {s}" for s in context) if context else "")
    user = (f"PAGE {page_number} — {len(panels)} panels.\n\n{_panel_block(panels)}\n{prior}\n\n"
            f"Return JSON with exactly {len(panels)} sentences, one per panel, in order.")
    content, used = call_with_chain(
        system=_SYSTEM, user=user, max_tokens=1200, progress=progress,
        label=f"panel-walk p{page_number}", validator=_valid_for(len(panels)),
    )
    return [" ".join(s.split()).strip()
            for s in json.loads(_strip_fence(content))["sentences"]], used


def build_narration(project: str, *, title: str = "", hook: str = "",
                    progress: Callable[[str], None] | None = None) -> Narration:
    """Walk every story panel in order and return a Narration ready for Stage 4."""
    log = progress or (lambda _m: None)
    pages = _load_pages(project)
    assert_vlm_descriptions(pages)

    ctx_path = PROJECTS_ROOT / project / "comic_context.json"
    ctx = json.loads(ctx_path.read_text()) if ctx_path.exists() else {}
    subject = str(ctx.get("title") or project).strip()

    total_panels = sum(len(p.get("panels") or []) for p in pages)
    log(f"[panel-walk] {len(pages)} story page(s), {total_panels} panel(s) — "
        f"one sentence each, one LLM call per page")

    scenes: list[Scene] = []
    context: list[str] = []
    model_used = ""

    # Scene 1 is the spoken hook over the cover. panel_ref -1 = whole page (Stage 5 resolves
    # it); no outro at all — this mode stops when the comic stops.
    opener = hook.strip() or f"This is the whole story of {subject}."
    first_page = pages[0]
    scenes.append(Scene(
        scene_id=1, text=opener, page_ref=int(first_page["page_number"]), panel_ref=-1,
        word_count=len(opener.split()), is_intro=True,
    ))

    for page in pages:
        panels = (page.get("panels") or [])[:_MAX_PANELS_PER_PAGE]
        if not panels:
            continue
        pn = int(page["page_number"])
        try:
            sentences, model_used = narrate_page(panels, pn, context, progress=progress)
        except Exception as exc:                     # noqa: BLE001 - one bad page must not
            log(f"[panel-walk]   p{pn:03d}: SKIPPED ({type(exc).__name__}: {exc})")
            continue                                 # kill a 90-page walk
        for panel, sentence in zip(panels, sentences):
            scenes.append(Scene(
                scene_id=len(scenes) + 1, text=sentence, page_ref=pn,
                panel_ref=int(panel.get("index", 0)),
                word_count=len(sentence.split()),
            ))
        context = [s.text for s in scenes[-_CONTEXT_SENTENCES:]]
        log(f"[panel-walk]   p{pn:03d}: {len(sentences)} sentence(s) "
            f"({sum(len(s.split()) for s in sentences)}w)")

    if len(scenes) <= 1:
        raise RuntimeError("panel-walk produced no narration — every page call failed")

    mode = "panel_walk"
    wps = _wps_for(mode)
    total_words = sum(s.word_count for s in scenes)
    for s in scenes:
        s.target_seconds = round(s.word_count / wps, 2)

    log(f"[panel-walk] done — {len(scenes)} scene(s), {total_words} words, "
        f"~{round(total_words / wps / 60, 1)} min")
    return Narration(
        mode=mode,
        title=title.strip() or subject,
        hook=opener,
        banner_title=title.strip() or subject,
        scenes=scenes,
        total_word_count=total_words,
        estimated_duration_seconds=round(total_words / wps, 2),
        words_per_second=wps,
        source_project=project,
        llm_model=model_used,
    )
