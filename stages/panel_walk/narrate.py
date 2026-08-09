"""Panel-walk narrator — long-form video essay that reads the comic in page order.

Every Short mode works backwards: an LLM plans beats from a wiki plot, writes prose, and a
matcher then hunts for panels that fit the prose. This mode inverts it. We walk the pages in
reading order, so the (page, panel) a line belongs to is known by construction — no cosine,
no DTW, no anchor drift, because there is nothing to match.

WHAT CHANGED 2026-07-29, and why. The first cut of this module took the inversion too far: it
told the writer "the panels ARE the script — one sentence per panel, narrate ONLY what that
panel shows". We then measured the reference channel Master picked (@griefspeaking, 5 videos,
~60k words of transcript, 246 hand-classified sentences) and that premise does not survive:

  * on the closest material (a Western comic, n=48) their sentences are 62.5% PLOT-TELLING and
    only 10.4% image-description — and 10.4% is the generous upper bound; counted strictly it
    is ~2-3%
  * the word "panel" occurs 5 times in 60,454 words; "panels" zero times. In a 29-minute comic
    narration it is literally absent
  * 14.6% of their sentences are the narrator's own opinion, which the old rules banned outright
  * 14.1% of their sentences run >=25 words, packing several events into one breath — impossible
    when every sentence is pinned to one drawing

So panels are demoted from "the content" to "the constraint": they are the evidence for what
happens and the guard against inventing things, not a checklist to read out. The writer tells
the story of the page; how many sentences that takes is up to the page.

ISOLATION (Master 2026-07-29: "seperated longform with other mode, dont make it update affect
other mode"). This module shares no style constant with recap / micro_moment / explore_answer.
Every number below was derived from the reference measurement and belongs to longform alone —
in particular we no longer import write_script._wps_for, which was silently handing longform the
recap words-per-second measured on a 60-second Short.

Needs VLM_EXTRACT=1. Under the manual-first default (VLM_EXTRACT=0) a panel's `description` is
concatenated dialog OCR, and silent panels come back as the literal string "Wordless
transition/SFX panel" — evidence a writer cannot use. This module refuses to run rather than
discover it in the render.
"""
from __future__ import annotations

import json
import re
from typing import Callable

from config import PROJECTS_ROOT
from ..stage_3._llm import call_with_chain
from ..stage_3.schema import Narration, Scene

# ── Longform's own constants. Source: @griefspeaking measurement, 2026-07-29. ────────────────
# NOTHING here is shared with the Short modes; see the ISOLATION note above.

_WORDS_PER_SEC = 3.05    # OUR render pace, not the reference's. The reference reads 219 WPM
                         # (3.65) and that number is sound — but Master set atempo 1.10 by ear
                         # (2026-08-01), which lands us at 3.05. target_seconds has to describe
                         # the audio we actually ship, or every downstream estimate is 20% off.
                         # ESTIMATE only — Stage 4 overwrites it with real TTS alignment.

_SENT_MIN_WORDS = 6          # their p10 is 7 words; allow 6 so punch lines are not blocked.
_SENT_MED_WORDS = 15         # median 15-16 across 2405 sentences.
_SENT_MAX_WORDS = 30         # p90 is 25 and the max seen is 56, but a generation cap of 30
                             # keeps the tail without licensing a rambling model.

# 16.5% of their sentences are <=8 words and 11.7% are >=25. The spread IS the rhythm, so the
# prompt asks for it explicitly rather than trusting a mid-range target to produce variance.
_SHORT_SENT_PCT = 16
_LONG_SENT_PCT = 12

_OPINION_EVERY_SENTENCES = 5   # 14.6% of sentences are narrator opinion ≈ one per 5-6 lines.
                               # 78.6% of those are a STANDALONE short sentence, not a trailing
                               # ", which is interesting" clause — a model's default failure.
_NAME_REFRESH_WORDS = 40       # median gap between two mentions of the lead's name.

# Narration budget for one page. The CEILING scales with panel count — a dense page holds more
# story — and comes from ~1.7-2.1 seconds of screen time per panel at our 3.05 w/s
# (≈5.8 words/panel), rounded up to 8 so a busy page is not truncated. The FLOOR is flat and deliberately NOT per-panel: a
# quiet page must be allowed one line and a walk-on, because the reference skips material rather
# than paying every drawing the same rent. A per-panel floor would forbid exactly that.
_PAGE_WORDS_MIN = 12          # about one median sentence — stops an empty page, nothing more
_WORDS_PER_PANEL_MAX = 8   # THE pacing constant. The reference's one invariant across both
                           # materials is ~1.7-2.1 SECONDS OF SCREEN TIME PER PANEL; at our
                           # 3.05 wps that is ~5.8 words per panel, so 8 is the cap and the
                           # average lands near it. 16 was a guess and it doubled every hold:
                           # measured on the-autumnal's first pass, 4.20s/panel against the
                           # reference's 1.9 — the video reads as a slideshow, not a story.
                           # Cross-check: at 8, three issues run 10.3 min, so all eight run
                           # ~27 min. The reference ships 29-minute videos.

_CONTEXT_SENTENCES = 3       # verbatim tail handed to the next page so prose flows across pages
_CONTEXT_GISTS = 12          # plus a running one-line-per-page summary. 3 sentences of memory
                             # is enough for a 50-second Short and useless across 40 minutes;
                             # the gists are what let page 60 know what happened on page 8.
_MAX_PANELS_PER_PAGE = 12    # sanity bound; Magi has never returned more on a real page


_SYSTEM = """You are narrating a graphic novel the way a long-form video essayist does. You TELL
THE STORY. A listener with their eyes closed should follow the plot and forget that a comic is
what you are reading from.

You are given the panels of ONE page: what the art shows, who is in it, and the dialogue printed
in it. That is your EVIDENCE for what happens on this page. It is NOT a checklist to describe.

HOW TO WRITE
1) Tell events, not pictures. Say what the characters DO, WANT and DECIDE. Never mention panels,
   pages, frames, the art, the artist, or "we see" — those words must not appear.
2) PRESENT TENSE for the main story. Switch to past tense ONLY to mark a flashback or backstory,
   then switch back. Tense is how the listener knows which timeline they are in.
3) Vary sentence length hard. Median about {med} words, floor {lo}, ceiling {hi}. Aim for roughly
   {short_pct}% of your sentences at 8 words or fewer, and {long_pct}% at 25 or more. A long
   sentence packs two or three events into one breath; a short one lands a turn. Do not write
   every sentence the same size — that flatness is the single most common failure here.
4) About every {op_every} sentences, add ONE short sentence of YOUR OWN reaction to what just
   happened. It must be its OWN sentence. Do NOT tack an opinion onto a story sentence as a
   trailing clause like ", which is unsettling" — that is the wrong shape.
5) Use characters' names. Re-name the lead roughly every {name_every} words so the listener never
   loses track, and use pronouns in between — pronouns should outnumber names about 2 to 1.
   {roster}
   If a character has no name in the data, keep them unnamed ("a young girl", "the older
   brother"). NEVER invent a name and never guess one from a costume.
6) Only claim what the evidence supports. You may state motive and feeling when the art or
   dialogue shows it. You may not invent events that are not on this page.
7) Dialogue is evidence, not a script. Use it to say who wants what. Quote at most a few words,
   and only when the exact words matter. Never transcribe a balloon.
8) Continuity: you are given the story so far and the last lines you wrote. Continue from them.
   Do not re-introduce a character already introduced and do not repeat what was already said.
9) No rhetorical questions on this page unless the page truly turns on one — across their whole
   channel these appear about once every three minutes.

LENGTH: write between {wmin} and {wmax} words for this page. Use as many or as few sentences as
that takes.

Return ONLY JSON:
{{"sentences": ["...", "..."], "gist": "one plain line saying what changed on this page"}}"""


_COLD_OPEN_SYSTEM = """You write the cold open of a long-form comic-story video.

You get the whole story as a list of one-line page summaries, in order. Write the spoken opening
that plays before the story starts.

Follow this shape — it is the shape the reference channel uses in every single video:
1) Name the version of this story or these characters that everyone already knows. One sentence.
2) Turn it over. In quick successive clauses, say what those familiar figures are in THIS version
   instead. Three to five of them, each short, stacked.
3) One sentence stating the premise of this world.
4) A title-drop sentence: this is <TITLE> like you have not seen it.
5) Say plainly that you are about to tell it from start to finish, then stop.

RULES
- PRESENT TENSE throughout.
- {wmin}-{wmax} words total.
- Do NOT spoil the ending, the twist, or who dies. Step 2 may use only what is established early.
- Do NOT ask the viewer to like, subscribe, or comment. The reference channel never does — the
  word "subscribe" appears zero times in 60,000 words of their narration.
- Do not mention panels, pages, or the artwork.

Return ONLY JSON: {{"lines": ["...", "..."]}} — one entry per spoken sentence."""


_OUTRO_SYSTEM = """You write the closing of a long-form comic-story video.

You get the whole story as one-line page summaries, in order. Write the spoken ending.

Follow this shape:
1) Close the story: that is the end of <TITLE>.
2) Step out of the story with a short pivot line.
3) One or two sentences of your own honest reflection on what the story was really about, then
   invite the viewer to say how THEY read it — ask a specific question, not "leave a comment".

RULES
- {wmin}-{wmax} words total.
- Do NOT ask for likes or subscribes. Not once.
- Do not mention panels, pages, or the artwork.
- Do not summarise the plot again. It just finished.

Return ONLY JSON: {{"lines": ["...", "..."]}} — one entry per spoken sentence."""

# Cold open 124s/396 words on their single-work video (the format closest to ours); the
# anthology videos run 53-73s. We target the shorter half so the story starts sooner.
_COLD_OPEN_WORDS_MIN, _COLD_OPEN_WORDS_MAX = 120, 260
# Outro measured 28-48s / 102-142 words on 4 of 5 videos (the fifth added a fan-art appeal).
_OUTRO_WORDS_MIN, _OUTRO_WORDS_MAX = 90, 150


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

    With VLM_EXTRACT=0 the `description` field holds concatenated dialogue OCR, which reads
    like a description to a prompt but is useless to narrate. The tell is Stage 2's OWN
    record of what it ran (`preprocessing_method`), NOT the "Wordless transition/SFX panel"
    string: that string is a BACKFILL (stages/stage_2/pipeline.py) for any panel the VLM
    returned nothing for, so it fires in BOTH modes. A clean VLM run still leaves a few
    (8/325 on the-autumnal), and keying the guard on it made a good run unnarratable.

    Those few are narrated as wordless beats — a silent panel is a real thing in a comic,
    not a data defect — but the count is printed so a HALF-failed VLM run stays visible."""
    panels = [p for pg in pages for p in (pg.get("panels") or [])]
    if not panels:
        raise RuntimeError("no panels found — Stage 2 produced no panel boxes")
    methods = [str(pg.get("preprocessing_method") or "").lower() for pg in pages]
    methods = [m for m in methods if m]
    # Only block on a POSITIVE Magi-only reading. An export that records no method at all
    # (older projects, hand-built fixtures) is unknown, not guilty — the count below still
    # surfaces it. Note a cover page reads 'heuristic_skip', so this is any(), not all().
    if methods and not any("vlm" in m for m in methods):
        raise RuntimeError(
            "Stage 2 ran Magi-only (no page has preprocessing_method='magi+vlm'), so there "
            "are no visual descriptions to narrate — only dialogue OCR. Re-run: "
            "VLM_EXTRACT=1 python -m stages.stage_2 --project <slug> --force  "
            "(--force is REQUIRED: VLM_EXTRACT is not part of the page cache key, so "
            "without it every page cache-hits and the flag silently does nothing.)"
        )
    missing = sum(1 for p in panels if _OCR_SOUP.search(str(p.get("description", ""))))
    if missing:
        print(f"[panel-walk] {missing}/{len(panels)} panels have no VLM description "
              f"({100 * missing / len(panels):.1f}%) — narrated as wordless beats")


def tiers_of(panels: list[dict]) -> list[list[dict]]:
    """Group a page's panels into reading-order TIERS (rows).

    Two panels share a tier when their vertical ranges overlap by more than half the shorter
    one's height. Measured over 371 tiers across two real projects, a tier's bounding box has a
    median aspect of 1.80 — against a 16:9 frame of 1.78. A single panel (median aspect ~1.0)
    and a whole page (~0.65) both fight a landscape frame; a tier does not. That is why the tier,
    not the panel, is longform's visual unit."""
    boxed = [p for p in panels if (p.get("bbox") or {}).get("h")]
    boxed.sort(key=lambda p: (p["bbox"]["y"], p["bbox"]["x"]))
    rows: list[list[dict]] = []
    for p in boxed:
        b = p["bbox"]
        for row in rows:
            rb = row[-1]["bbox"]
            overlap = min(b["y"] + b["h"], rb["y"] + rb["h"]) - max(b["y"], rb["y"])
            if overlap > 0.5 * min(b["h"], rb["h"]):
                row.append(p)
                break
        else:
            rows.append([p])
    return rows


def _roster_line(project: str) -> str:
    """Closed list of names the writer may use, from comic_context.

    Rule 5 lets the writer name characters constantly (their proper-noun rate is 4.5%), which is
    what makes 40 minutes listenable. Handing over a closed roster is how we get that WITHOUT
    the failure mode in project_concealed_identity_pipeline_bugs, where a model reads a costume
    and volunteers a famous name the comic is deliberately withholding."""
    ctx_path = PROJECTS_ROOT / project / "comic_context.json"
    if not ctx_path.exists():
        return ""
    try:
        ctx = json.loads(ctx_path.read_text())
    except json.JSONDecodeError:
        return ""
    names: list[str] = []

    def _collect(seq) -> None:
        for c in seq or []:
            n = c.get("name") if isinstance(c, dict) else c
            n = str(n or "").strip()
            if n and n not in names:
                names.append(n)

    # comic_context has three shapes in the wild and a project may use any of them:
    #   Stage 1        → {"summary": {"characters": [...]}}
    #   anthology/saga → {"issues": [{"characters": [...]}, ...]}
    #   URL-direct     → {"characters": [...], "issues": "#1-3"}   <- `issues` is a RANGE STRING
    # Iterating that range string yielded one CHARACTER per loop and crashed on .get.
    _collect(ctx.get("characters"))
    summary = ctx.get("summary")
    if isinstance(summary, dict):
        _collect(summary.get("characters"))
    issues = ctx.get("issues")
    if isinstance(issues, list):
        for issue in issues:
            if isinstance(issue, dict):
                _collect(issue.get("characters"))
    if not names:
        return ""
    return ("The named characters in this comic are: " + ", ".join(names[:24])
            + ". Use these spellings; do not use a name outside this list.")


def _page_block(panels: list[dict]) -> str:
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


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t


# Words that give away that the narrator is looking at a drawing. Their transcripts contain
# "panel" 5 times in 60k words and "panels" not at all, so a response using them has missed the
# brief and is worth one more sample rather than shipping.
_META_WORDS = re.compile(r"\b(panel|panels|this page|the page|the artwork|the art style|"
                         r"we see|we can see|we then see|the reader|the illustration)\b", re.I)


def _valid_page(word_min: int, word_max: int) -> Callable[[str], bool]:
    """Shape + style only — length is handled by narrate_page, NOT here.

    Length was a validator check until it was measured on the-autumnal: the writer returns
    good prose at ~28 words/panel against a 16-word cap, so every page failed all three
    attempts and got SKIPPED. The three attempts were identical prompts, so they failed
    identically — a blind retry cannot fix a length miss. narrate_page now re-asks with the
    actual overage quoted, and trims as a last resort, because losing a page's story
    entirely is far worse than losing its last sentence."""
    def _v(out: str) -> bool:
        try:
            data = json.loads(_strip_fence(out))
            sents = data.get("sentences")
        except Exception:
            return False
        if not isinstance(sents, list) or not sents:
            return False
        if not all(isinstance(s, str) and s.strip() for s in sents):
            return False
        if not str(data.get("gist") or "").strip():
            return False
        if any(_META_WORDS.search(s) for s in sents):
            return False
        return sum(len(s.split()) for s in sents) >= word_min
    return _v


def _trim_to_budget(sents: list[str], word_max: int) -> list[str]:
    """Keep whole leading sentences up to `word_max`; always keep at least one."""
    kept, used = [], 0
    for s in sents:
        n = len(s.split())
        if kept and used + n > word_max:
            break
        kept.append(s)
        used += n
    return kept or sents[:1]


def _valid_block(word_min: int, word_max: int) -> Callable[[str], bool]:
    def _v(out: str) -> bool:
        try:
            lines = json.loads(_strip_fence(out)).get("lines")
        except Exception:
            return False
        if not isinstance(lines, list) or not lines:
            return False
        if not all(isinstance(s, str) and s.strip() for s in lines):
            return False
        if any(_META_WORDS.search(s) for s in lines):
            return False
        return word_min <= sum(len(s.split()) for s in lines) <= word_max
    return _v


def narrate_page(panels: list[dict], page_number: int, context: list[str], gists: list[str], *,
                 roster: str = "",
                 progress: Callable[[str], None] | None = None) -> tuple[list[str], str, str]:
    """One LLM call → the prose for one page. Returns (sentences, gist, model_used)."""
    word_min = _PAGE_WORDS_MIN
    word_max = max(word_min + _SENT_MED_WORDS, len(panels) * _WORDS_PER_PANEL_MAX)
    system = _SYSTEM.format(
        med=_SENT_MED_WORDS, lo=_SENT_MIN_WORDS, hi=_SENT_MAX_WORDS,
        short_pct=_SHORT_SENT_PCT, long_pct=_LONG_SENT_PCT,
        op_every=_OPINION_EVERY_SENTENCES, name_every=_NAME_REFRESH_WORDS,
        roster=roster, wmin=word_min, wmax=word_max,
    )
    story_so_far = ("STORY SO FAR (one line per page):\n"
                    + "\n".join(f"- {g}" for g in gists) + "\n\n") if gists else ""
    prior = ("THE LAST LINES YOU WROTE (continue from these, do not repeat them):\n"
             + "\n".join(f"- {s}" for s in context) + "\n\n") if context else ""
    user = (f"{story_so_far}{prior}PAGE {page_number} — {len(panels)} panels.\n\n"
            f"{_page_block(panels)}\n\n"
            f"Write {word_min}-{word_max} words telling what happens on this page.")
    # Re-ask with the OVERAGE QUOTED. call_with_chain's own retries resend an identical
    # prompt, so a length miss just repeats; telling the writer how far over it went is what
    # actually converges. Two rounds, then trim — a trimmed page still tells its story.
    log = progress or (lambda _m: None)
    note = ""
    for attempt in range(2):
        content, used = call_with_chain(
            system=system, user=user + note, max_tokens=1600, progress=progress,
            label=f"panel-walk p{page_number}", validator=_valid_page(word_min, word_max),
        )
        data = json.loads(_strip_fence(content))
        sentences = [" ".join(s.split()).strip() for s in data["sentences"]]
        total = sum(len(s.split()) for s in sentences)
        if total <= word_max:
            break
        note = (f"\n\nYour previous attempt ran {total} words. The limit is {word_max} and it "
                f"is not negotiable. Rewrite this page in {word_min}-{word_max} words. Do not "
                f"just tighten the wording — DROP the smaller moments and keep only what the "
                f"story needs. Skipping a drawing entirely is correct and expected.")
        log(f"[panel-walk p{page_number}] {total}w over the {word_max}w limit — re-asking")
    else:
        kept = _trim_to_budget(sentences, word_max)
        log(f"[panel-walk p{page_number}] still {total}w — trimmed to "
            f"{sum(len(s.split()) for s in kept)}w ({len(sentences) - len(kept)} line(s) cut)")
        sentences = kept
    return sentences, " ".join(str(data["gist"]).split()).strip(), used


def _write_block(system: str, gists: list[str], title: str, word_min: int, word_max: int,
                 label: str, progress: Callable[[str], None] | None) -> list[str]:
    """Cold open / outro. Both need the WHOLE story, which the per-page gists already give us —
    no second pass over the pages and no extra reading cost."""
    log = progress or (lambda _m: None)
    base = (f"TITLE: {title}\n\nTHE STORY, one line per page, in order:\n"
            + "\n".join(f"- {g}" for g in gists))
    # Structure-only validator; LENGTH is handled here with the miss quoted back. Both blocks
    # used to be validated on length inside call_with_chain, whose retries resend an IDENTICAL
    # prompt — so a model that writes 70 words against a 90 floor writes 70 three times and the
    # whole run dies at the last step, after 74 good pages. Same failure the page path had.
    shape = _valid_block(0, 10**6)
    note = ""
    for _ in range(3):
        content, _mdl = call_with_chain(
            system=system.format(wmin=word_min, wmax=word_max), user=base + note,
            max_tokens=900, progress=progress, label=label, validator=shape,
        )
        lines = [" ".join(s.split()).strip()
                 for s in json.loads(_strip_fence(content))["lines"]]
        total = sum(len(l.split()) for l in lines)
        if word_min <= total <= word_max:
            return lines
        side = "short of the" if total < word_min else "over the"
        note = (f"\n\nYour previous attempt was {total} words — {side} required "
                f"{word_min}-{word_max}. Write it again at the required length. "
                + ("Add more of the story: name what happens, do not pad with adjectives."
                   if total < word_min else
                   "Cut whole sentences rather than thinning every one of them."))
        log(f"[{label}] {total}w vs {word_min}-{word_max} — re-asking")
    # Three informed tries and still outside the band: ship it rather than lose 74 good pages
    # to a bookend. The count is logged so it is visible, not silent.
    log(f"[{label}] ⚠ still {total}w after 3 tries — shipping outside the "
        f"{word_min}-{word_max} band")
    return lines


def _assign_tiers(sentences: list[str], rows: list[list[dict]]) -> list[int]:
    """Pick the panel each sentence is anchored to — the first panel of its tier.

    The visual unit is the tier, but a Scene can only carry one `panel_ref`, so we hand Stage 5
    the tier's leading panel and let the (still to be written) longform render path widen it to
    the tier's bounding box. Sentences are spread across the page's tiers in reading order, so
    a page with more prose walks further down the page. Extra tiers past the last sentence are
    simply not shown — that is how panel-skipping emerges without a rule for it."""
    if not rows:
        return [-1] * len(sentences)
    out = []
    for i in range(len(sentences)):
        row = rows[min(i * len(rows) // max(1, len(sentences)), len(rows) - 1)]
        out.append(int(row[0].get("index", 0)))
    return out


def build_narration(project: str, *, title: str = "", hook: str = "",
                    progress: Callable[[str], None] | None = None) -> Narration:
    """Walk every story page in order and return a Narration ready for Stage 4."""
    log = progress or (lambda _m: None)
    pages = _load_pages(project)
    assert_vlm_descriptions(pages)

    ctx_path = PROJECTS_ROOT / project / "comic_context.json"
    ctx = json.loads(ctx_path.read_text()) if ctx_path.exists() else {}
    subject = str(ctx.get("title") or project).strip()
    show_title = title.strip() or subject
    roster = _roster_line(project)

    total_panels = sum(len(p.get("panels") or []) for p in pages)
    log(f"[panel-walk] {len(pages)} story page(s), {total_panels} panel(s) — "
        f"one LLM call per page" + (" (roster loaded)" if roster else ""))

    body: list[Scene] = []
    context: list[str] = []
    gists: list[str] = []
    model_used = ""

    for page in pages:
        panels = (page.get("panels") or [])[:_MAX_PANELS_PER_PAGE]
        if not panels:
            continue
        pn = int(page["page_number"])
        try:
            sentences, gist, model_used = narrate_page(
                panels, pn, context, gists[-_CONTEXT_GISTS:], roster=roster, progress=progress)
        except Exception as exc:                     # noqa: BLE001 - one bad page must not
            log(f"[panel-walk]   p{pn:03d}: SKIPPED ({type(exc).__name__}: {exc})")
            continue                                 # kill a 90-page walk
        refs = _assign_tiers(sentences, tiers_of(panels))
        for sentence, ref in zip(sentences, refs):
            body.append(Scene(scene_id=0, text=sentence, page_ref=pn, panel_ref=ref,
                              word_count=len(sentence.split())))
        gists.append(gist)
        context = [s.text for s in body[-_CONTEXT_SENTENCES:]]
        log(f"[panel-walk]   p{pn:03d}: {len(sentences)} line(s) "
            f"({sum(len(s.split()) for s in sentences)}w)")

    if not body:
        raise RuntimeError("panel-walk produced no narration — every page call failed")

    # Cold open and outro come LAST because both need the finished story; the gists supply it.
    first_page, last_page = int(pages[0]["page_number"]), body[-1].page_ref
    if hook.strip():
        opening = [hook.strip()]
    else:
        opening = _write_block(_COLD_OPEN_SYSTEM, gists, show_title,
                               _COLD_OPEN_WORDS_MIN, _COLD_OPEN_WORDS_MAX,
                               "panel-walk cold-open", progress)
    closing = _write_block(_OUTRO_SYSTEM, gists, show_title,
                           _OUTRO_WORDS_MIN, _OUTRO_WORDS_MAX, "panel-walk outro", progress)

    scenes: list[Scene] = []
    for line in opening:
        scenes.append(Scene(scene_id=0, text=line, page_ref=first_page, panel_ref=-1,
                            word_count=len(line.split()), is_intro=True))
    scenes.extend(body)
    for line in closing:
        scenes.append(Scene(scene_id=0, text=line, page_ref=last_page, panel_ref=-1,
                            word_count=len(line.split()), is_outro=True))
    for i, s in enumerate(scenes, start=1):
        s.scene_id = i
        s.target_seconds = round(s.word_count / _WORDS_PER_SEC, 2)

    total_words = sum(s.word_count for s in scenes)
    log(f"[panel-walk] done — {len(scenes)} scene(s), {total_words} words, "
        f"~{round(total_words / _WORDS_PER_SEC / 60, 1)} min "
        f"(cold open {sum(len(l.split()) for l in opening)}w, "
        f"outro {sum(len(l.split()) for l in closing)}w)")
    return Narration(
        mode="panel_walk",
        title=show_title,
        hook=opening[0],
        banner_title=show_title,
        scenes=scenes,
        total_word_count=total_words,
        estimated_duration_seconds=round(total_words / _WORDS_PER_SEC, 2),
        words_per_second=_WORDS_PER_SEC,
        source_project=project,
        llm_model=model_used,
    )
