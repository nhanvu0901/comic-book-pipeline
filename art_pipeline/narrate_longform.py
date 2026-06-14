"""A4b long-form: chapter-by-chapter scene writer driven by outline.json.

One LLM call per chapter (small calls stay reliable — a single 1,500-word call
does not). Scene/visual schema identical to Shorts so hunt + assemble reuse
free. Research rules enforced here: re-hook endings on chapters at
ART_LF_REHOOK_POSITIONS, no-CTA thematic close, per-chapter variety scope +
cross-chapter rules (spec 2026-06-12 §A4b)."""
import json

from config import CREATIVE_LLM_MODELS
from stages.stage_3._llm import call_with_chain
from stages.stage_3.schema import Narration, Scene

from ._json import extract_json
from .narrate import _hook_is_concrete, _starts_with_connective, region_catalog
from .dedupe import dedupe_scenes
from .visual_plan import (
    assign_motions, parse_visual, save_plan, validate_variety_longform,
    visual_target,
)
from .config import (
    ART_LF_CHAPTER_WORDS_BAND, ART_LF_REGION_REUSE_WINDOW, ART_LF_REHOOK_POSITIONS,
    ART_LF_SAID_LINES_MAX, ART_LF_SCENE_MAX_WORDS, ART_LF_SCENES_PER_CHAPTER_MAX,
    ART_LF_SCENES_PER_CHAPTER_MIN, ART_LF_TOTAL_WORDS_FLOOR, ART_WORDS_PER_SEC,
    get_art_project_path,
)

# Forward-reference cues for chapter-ending re-hooks ("but here's where it
# gets stranger"). Lexicon, not a model call — cheap, deterministic, tunable.
_FORWARD_CUES = (
    "what happened next", "stranger ", "wasn't the end", "was not the end",
    "not the whole story", "no one expected", "about to change",
    "would change everything", "the real story", "until ", "that was only",
    "only the beginning", "what the x-ray", "what came next", "hides one more",
)
_FORWARD_OPENERS = ("but ", "yet ", "and yet ")

_CTA_PHRASES = ("subscribe", "like this video", "comment below", "hit the bell",
                "smash that", "follow for more")


def _is_forward_hook(text: str) -> bool:
    low = " ".join(text.lower().split())
    return low.startswith(_FORWARD_OPENERS) or any(c in low for c in _FORWARD_CUES)


def _has_cta(text: str) -> bool:
    low = text.lower()
    return any(p in low for p in _CTA_PHRASES)


def _role_budget(role: str) -> str:
    """Description budget by chapter role: only the cold_open and the evidence
    chapter may catalog the painting's appearance; interpretive chapters must
    reference features to build meaning, not re-describe them."""
    if role in ("cold_open", "evidence"):
        return ("DESCRIPTION BUDGET: You MAY describe the painting's visual "
                "appearance in this chapter.")
    return ("DESCRIPTION BUDGET: Do NOT catalog the painting's appearance — it "
            "has been described already. Reference a feature only to make a new "
            "interpretive or historical point.")


def _said_block(said_lines: list[str], *, limit: int = ART_LF_SAID_LINES_MAX) -> str:
    """The most-recent `limit` already-narrated sentences, as a bullet block for
    the prompt. Empty list → empty string (chapter 1 has nothing prior)."""
    recent = said_lines[-limit:]
    if not recent:
        return ""
    return ("ALREADY NARRATED (do NOT restate any of these — add only NEW "
            "information):\n" + "\n".join(f"- {s}" for s in recent))


_LF_SYSTEM = """You are writing CHAPTER {pos} of {total} of an 8-12 minute
educational art video. Neutral, intimate second-person where natural
("notice how…"), precise — never sensational, never invented.

Hard rules:
1. EVERY factual claim must come from THIS CHAPTER'S FACTS below. No outside
   facts, no speculation.
2. Write {n_min}-{n_max} scenes totalling ABOUT {target_words} words (the
   validator rejects chapters far off target). MOST scenes should be 14-22
   words (hard cap {scene_max}); scenes under 12 words are rare accents.
   Causal chaining: each scene follows from the previous with "therefore"
   or "but" logic — never disconnected observations.
3. EVERY scene carries a "visual" object (same schema as the region catalog):
   - {{"kind": "painting_region", "panel_ref": N}} — zooms into that region.
   - {{"kind": "painting_full"}} — whole artwork, AT MOST once in this chapter
     ({full_note}).
   - {{"kind": "related", "subject": "<concrete searchable image>"}} — artist,
     era, place, technique, x-ray. Aim for roughly 30% related scenes.
4. VARIETY: no two consecutive scenes show the same thing; a region may
   RETURN later, but never within {window} scenes of its last use; related
   subjects all differ. The previous chapter ended on these region
   panel_refs: {recent_regions} — do not reopen with them.
   Do NOT use these related subjects (already used): {blocked_subjects}.
5. {position_rule}
6. Educational register, explain terms in-line, no hype words, never say
   subscribe/like/comment.
Respond with ONLY valid JSON."""

_POSITION_RULES = {
    "first": ('Scene 1 is the video hook (is_intro=true), max 26 words: a '
              'pattern-interrupt naming a concrete, surprising, verified detail '
              'of THIS artwork — it MUST mention the title or the artist.'),
    "rehook": ('The LAST scene of this chapter must be a forward-reference '
               're-hook teasing the next chapter without revealing it, e.g. '
               '"But what the x-ray revealed was stranger still."'),
    "middle": "End the chapter on a complete thought.",
    "last": ('The LAST scene (is_outro=true) closes with a thematic observation '
             'or open question — it names the artwork and that it hangs in The '
             'Met. NEVER a call to action.'),
}


def _repair_region_spacing(scenes: list[Scene], decls: list[dict],
                           pages_by_number: dict[int, dict], *, window: int,
                           history: list | None = None, log=print) -> int:
    """Deterministically re-aim painting_region decls that violate the variety
    rules (consecutive-same target, or same region within `window` scenes) to
    the least-recently-used region on the same page. The writer's intent is the
    KIND of shot; which exact region carries a near-miss repeat is mechanical —
    same lesson as the comic pipeline's deterministic beat anchoring. Returns
    the number of repairs (logged per repair).

    `history` = visual targets of the previous chapter's tail scenes, so the
    spacing also holds across the chapter boundary. painting_full and related
    are never repaired: full has its own cap, related is bound to the text
    (a duplicated subject must go back to the writer, not be re-aimed)."""
    history = list(history or [])
    last_seen: dict[tuple, int] = {}
    prev_target: tuple | None = None
    for idx, t in enumerate(history):
        last_seen[t] = idx
        prev_target = t
    base = len(history)
    repairs = 0
    for i, (sc, d) in enumerate(zip(scenes, decls)):
        idx = base + i
        target = visual_target(sc.__dict__, d)
        if d["kind"] == "painting_region":
            page = sc.page_ref
            n_panels = len((pages_by_number.get(page) or {}).get("panels") or [])
            # effective window: with fewer regions than the raw window, LRU
            # rotation can only guarantee a gap of n_panels — shrink to match
            eff = min(window, n_panels)
            seen = last_seen.get(target)
            violates = target == prev_target or (seen is not None and idx - seen < eff)
            if violates and n_panels:
                ranked = []
                for p in range(n_panels):
                    cand = ("r", page, p)
                    if cand == prev_target:
                        continue
                    cand_seen = last_seen.get(cand)
                    fits = cand_seen is None or idx - cand_seen >= eff
                    ranked.append((0 if fits else 1,
                                   cand_seen if cand_seen is not None else -1, p))
                if ranked:
                    ranked.sort()
                    new_p = ranked[0][2]
                    if new_p != d["panel_ref"]:
                        log(f"[narrate-lf] repaired scene {sc.scene_id}: region "
                            f"p{page}/r{d['panel_ref']} → r{new_p} (spacing)")
                        d["panel_ref"] = new_p
                        sc.panel_ref = new_p
                        target = ("r", page, new_p)
                        repairs += 1
        last_seen[target] = idx
        prev_target = target
    return repairs


def build_chapter_scenes(raw: str, pages: list[dict], ctx: dict, chapter: dict,
                         *, scene_id_offset: int, rehook_required: bool,
                         is_first: bool = False, is_last: bool = False,
                         history: list | None = None,
                         used_subjects: set[str] | None = None,
                         log=print) -> tuple[list[Scene], list[dict]]:
    """Parse + validate ONE chapter's LLM output. Scene ids continue from
    scene_id_offset. Raises ValueError with a feed-back-able message."""
    data = extract_json(raw)
    if data is None:
        raise ValueError(f"chapter {chapter['chapter_id']}: unparseable JSON")
    scenes_raw = data.get("scenes") or []
    # The floor scales with the chapter's word target: the model reliably
    # follows a SCENE COUNT but lands ~15-18 words/scene regardless of word
    # instructions (e2e rounds 4-6: every word-band failure was a chapter that
    # used few scenes — 14 scenes can never reach a 320-word target).
    scenes_min_eff = min(ART_LF_SCENES_PER_CHAPTER_MAX,
                         max(ART_LF_SCENES_PER_CHAPTER_MIN,
                             -(-int(chapter["target_words"]) // 17)))
    if not scenes_min_eff <= len(scenes_raw) <= ART_LF_SCENES_PER_CHAPTER_MAX:
        raise ValueError(
            f"chapter {chapter['chapter_id']}: {len(scenes_raw)} scenes — "
            f"~{chapter['target_words']} words needs {scenes_min_eff}-"
            f"{ART_LF_SCENES_PER_CHAPTER_MAX} scenes (≈17 words each)")

    by_number = {p["page_number"]: p for p in pages}
    scenes: list[Scene] = []
    decls: list[dict] = []
    for j, s in enumerate(scenes_raw):
        i = scene_id_offset + j + 1
        if not isinstance(s, dict):
            raise ValueError(f"chapter {chapter['chapter_id']}: scene {i} not an object")
        text = str(s.get("text") or "").strip()
        if not text:
            raise ValueError(f"chapter {chapter['chapter_id']}: scene {i} empty text")
        wc = len(text.split())
        if wc > ART_LF_SCENE_MAX_WORDS:
            raise ValueError(
                f"chapter {chapter['chapter_id']}: scene {i} too long ({wc} words)")
        try:
            pref = int(s.get("page_ref") or 0)
        except (TypeError, ValueError):
            raise ValueError(f"chapter {chapter['chapter_id']}: scene {i} bad page_ref")
        if pref not in by_number:
            raise ValueError(
                f"chapter {chapter['chapter_id']}: scene {i} bad page_ref {pref}")
        page = by_number[pref]
        decl = parse_visual(s.get("visual") or {}, scene_id=i)
        if decl["kind"] == "painting_region":
            n_panels = len(page.get("panels") or [])
            if decl["panel_ref"] >= n_panels:
                raise ValueError(
                    f"chapter {chapter['chapter_id']}: scene {i} panel_ref "
                    f"{decl['panel_ref']} out of range (page {pref} has {n_panels})")
            panel_ref = decl["panel_ref"]
        else:
            panel_ref = -1
        decl["chapter_id"] = chapter["chapter_id"]
        decls.append(decl)
        scenes.append(Scene(
            scene_id=i, text=text, page_ref=pref, panel_ref=panel_ref,
            word_count=wc, target_seconds=round(wc / ART_WORDS_PER_SEC, 2),
            connective=_starts_with_connective(text), beat_id=i,
            is_intro=bool(s.get("is_intro")) and is_first and j == 0,
            is_outro=bool(s.get("is_outro")) and is_last and j == len(scenes_raw) - 1,
        ))

    # Cross-chapter related-subject dedup must fail INSIDE this chapter's retry
    # loop — caught only at the end-of-video gate it throws away every already-
    # written chapter (e2e round 5 2026-06-12: scene 36 reused a chapter-2
    # subject after all 5 chapters had been paid for).
    if used_subjects:
        for d in decls:
            if d["kind"] != "related":
                continue
            subj = " ".join(str(d.get("subject") or "").lower().split())
            if subj in used_subjects:
                raise ValueError(
                    f"chapter {chapter['chapter_id']}: related subject {subj!r} "
                    f"already used in an earlier chapter — pick a DIFFERENT image "
                    f"(do not reuse: {sorted(used_subjects)[:8]})")

    # painting_full ≤1 mid-chapter (intro/outro fulls excluded) — kept from the
    # original per-chapter rule set; the window check below does not cover it.
    mid_fulls = [sc.scene_id for sc, d in zip(scenes, decls)
                 if d["kind"] == "painting_full"
                 and not sc.is_intro and not sc.is_outro]
    if len(mid_fulls) > 1:
        raise ValueError(
            f"chapter {chapter['chapter_id']}: painting_full used mid-chapter in "
            f"scenes {mid_fulls} — at most ONE mid-chapter full view")
    # Deterministic repair BEFORE validation: re-aim near-miss region repeats
    # (window-6-on-6-regions has no combinatorial slack for LLM retries —
    # e2e round 2 2026-06-12). The writer keeps kind/subject/text authority.
    _repair_region_spacing(scenes, decls, by_number,
                           window=ART_LF_REGION_REUSE_WINDOW,
                           history=history, log=log)
    # Per-chapter variety: consecutive-distinct / region-reuse window /
    # related-dedup. After repair this only fires on related/full violations.
    validate_variety_longform(
        [sc.__dict__ for sc in scenes], {d["scene_id"]: d for d in decls},
        window=ART_LF_REGION_REUSE_WINDOW,
        panels_by_page={pn: len(p.get("panels") or []) for pn, p in by_number.items()})

    if rehook_required and not _is_forward_hook(scenes[-1].text):
        raise ValueError(
            f"chapter {chapter['chapter_id']}: last scene must be a forward-looking "
            f"re-hook (e.g. start with 'But…' or tease what comes next)")
    if is_first:
        if not scenes[0].is_intro:
            raise ValueError("chapter 1: scene 1 must set is_intro=true")
        if not _hook_is_concrete(scenes[0].text, ctx):
            raise ValueError(
                "chapter 1: hook is generic — it must name a concrete detail of "
                "this artwork (pattern-interrupt)")
    if is_last:
        if not scenes[-1].is_outro:
            raise ValueError("final chapter: last scene must set is_outro=true")
        if _has_cta(scenes[-1].text):
            raise ValueError("final chapter: outro contains a call-to-action — "
                             "close thematic instead")
    chapter_words = sum(sc.word_count for sc in scenes)
    target = int(chapter["target_words"])
    lo, hi = ART_LF_CHAPTER_WORDS_BAND
    if not target * lo <= chapter_words <= target * hi:
        raise ValueError(
            f"chapter {chapter['chapter_id']}: {chapter_words} words vs target "
            f"{target} (stay within {round(lo * 100)}-{round(hi * 100)}%)")
    return scenes, decls


def validate_cross_chapter(scenes: list[dict], decls: list[dict],
                           panels_by_page: dict[int, int] | None = None) -> None:
    """Whole-video rules, run on the FULL ordered scene list: related subjects
    globally distinct, and the region-reuse window also holds ACROSS chapter
    boundaries (per-chapter checks cannot see a repeat that straddles two
    chapters). Replaces the old adjacent-chapter region ban, which was
    unsatisfiable for low-region artworks (e2e 2026-06-12)."""
    validate_variety_longform(scenes, {d["scene_id"]: d for d in decls},
                              window=ART_LF_REGION_REUSE_WINDOW,
                              panels_by_page=panels_by_page)


def _inject_chapter_flags(narration: dict, chapters_meta: list[dict]) -> None:
    """In-place on the SERIALIZED narration dict (comic Scene dataclass is
    read-only; Stage 4 reads narration.json as plain dicts and ignores unknown
    keys): tag every scene with its chapter_id, and mark the LAST scene of each
    re-hook chapter (positions in ART_LF_REHOOK_POSITIONS) with is_rehook=true."""
    sid_to_ch = {sid: cm["chapter_id"] for cm in chapters_meta
                 for sid in cm["scene_ids"]}
    rehook_sids = {cm["scene_ids"][-1] for cm in chapters_meta
                   if cm.get("rehook") and cm["scene_ids"]}
    for s in narration["scenes"]:
        s["chapter_id"] = sid_to_ch.get(s["scene_id"], 0)
        if s["scene_id"] in rehook_sids:
            s["is_rehook"] = True


def _run_dedupe(all_scenes, ctx, chapters_meta, root, *, log=print) -> dict:
    """Run the cross-scene anti-repetition guard on the full ordered scene list
    and persist a per-project report. Kept as a seam so it is unit-testable
    without driving the whole writer."""
    roles_by_sid = {sid: cm["role"] for cm in chapters_meta for sid in cm["scene_ids"]}
    report = dedupe_scenes(all_scenes, ctx, roles_by_sid, log=log)
    (root / "repetition_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False))
    log(f"[narrate-lf] dedupe: {report['rewrites']} rewrite(s), "
        f"max cross-scene sim now {report['max_similarity_after']}")
    return report


def write_longform_narration(project_name: str, *, log=print) -> dict:
    root = get_art_project_path(project_name)
    outline = json.loads((root / "outline.json").read_text())
    ctx = json.loads((root / "art_context.json").read_text())
    pages = [json.loads(p.read_text())
             for p in sorted((root / "preprocessed").glob("page_*.json"))]
    if not pages:
        raise FileNotFoundError("no preprocessed pages — run regions first")

    chapters = outline["chapters"]
    total = len(chapters)
    panels_by_page = {p["page_number"]: len(p.get("panels") or []) for p in pages}

    # The 8-minute guarantee lives HERE, not in the per-chapter band: chapter
    # floors near the model's natural pace (~13-17 w/scene) failed runs by a
    # handful of words (e2e round 8: 267 vs 272). One cheap redraw of all
    # chapters beats renders that come out at 7:43.
    for draw in (1, 2):
        all_scenes: list[Scene] = []
        all_decls: list[dict] = []
        chapters_meta: list[dict] = []
        used_subjects: list[str] = []
        said_lines: list[str] = []
        recent_regions: list[int] = []   # panel_refs of the previous chapter's tail
        prev_tail = ""
        model_used = ""
        for pos, ch in enumerate(chapters, start=1):
            is_first, is_last = pos == 1, pos == total
            rehook = pos in ART_LF_REHOOK_POSITIONS and not is_last
            position_rule = (_POSITION_RULES["first"] if is_first else
                             _POSITION_RULES["last"] if is_last else
                             _POSITION_RULES["rehook"] if rehook else
                             _POSITION_RULES["middle"])
            system = _LF_SYSTEM.format(
                pos=pos, total=total,
                n_min=ART_LF_SCENES_PER_CHAPTER_MIN, n_max=ART_LF_SCENES_PER_CHAPTER_MAX,
                scene_max=ART_LF_SCENE_MAX_WORDS,
                target_words=ch["target_words"],
                full_note=("the very first scene may be the full painting"
                           if is_first else "use sparingly"),
                window=ART_LF_REGION_REUSE_WINDOW,
                recent_regions=recent_regions or "none",
                blocked_subjects=sorted(set(used_subjects)) or "none",
                position_rule=position_rule)
            tw = int(ch["target_words"])
            user = (
                f"VIDEO THROUGH-LINE: {outline.get('through_line', '')}\n"
                f"CHAPTER {pos}/{total}: {ch['title']} (role: {ch['role']}, "
                f"target ~{tw} words)\n"
                f"WORD BUDGET: ~{tw} words total. Write AT LEAST "
                f"{min(22, max(14, -(-tw // 17)))} scenes averaging ~17 words — "
                f"fewer scenes WILL be rejected.\n"
                # the system-prompt block list alone was ignored twice in e2e
                # round 7 — repeat it at the top of the user turn, where it sticks
                f"FORBIDDEN related subjects (already shown, any variation will be "
                f"rejected): {sorted(set(used_subjects)) or 'none'}\n\n"
                f"PREVIOUS CHAPTER ENDED WITH: {prev_tail or '(video start)'}\n\n"
                f"{_role_budget(ch['role'])}\n\n"
                + (_said_block(said_lines) + "\n\n" if said_lines else "")
                +
                f"THIS CHAPTER'S FACTS (the ONLY allowed source of claims):\n"
                + "\n".join(f"- {f}" for f in ch["facts"]) +
                f"\n\nREGION CATALOG (page_ref/panel_ref targets):\n{region_catalog(pages)}\n\n"
                'Return STRICT JSON only:\n'
                '{"scenes": [{"text": "...", "page_ref": 1, "panel_ref": 0,\n'
                '             "visual": {"kind": "painting_region", "panel_ref": 0},\n'
                '             "is_intro": false, "is_outro": false}]}'
            )
            # visual targets of the last `window` scenes already written — keeps
            # the repair pass honest across the chapter boundary
            history = [visual_target(sc.__dict__, d) for sc, d in
                       list(zip(all_scenes, all_decls))[-ART_LF_REGION_REUSE_WINDOW:]]
            last_err: Exception | None = None
            for attempt in (1, 2, 3):
                raw, model_used = call_with_chain(
                    system=system, user=user, models=CREATIVE_LLM_MODELS,
                    max_tokens=4000, progress=log,
                    label=f"art-lf-ch{pos}#{attempt}",
                    validator=lambda c: extract_json(c) is not None)
                try:
                    scenes, decls = build_chapter_scenes(
                        raw, pages, ctx, ch, scene_id_offset=len(all_scenes),
                        rehook_required=rehook, is_first=is_first, is_last=is_last,
                        history=history, used_subjects=set(used_subjects), log=log)
                    break
                except ValueError as exc:
                    last_err = exc
                    log(f"[narrate-lf] chapter {pos} attempt {attempt} rejected: {exc}")
                    user += (f"\n\nYOUR PREVIOUS ATTEMPT FAILED VALIDATION: {exc}. "
                             "Fix exactly that.")
            else:
                raise ValueError(f"chapter {pos} failed after 3 attempts: {last_err}")

            chapters_meta.append({"chapter_id": ch["chapter_id"], "title": ch["title"],
                                  "role": ch["role"], "rehook": rehook,
                                  "scene_ids": [sc.scene_id for sc in scenes],
                                  "start": None})
            used_subjects += [" ".join(str(d.get("subject") or "").lower().split())
                              for d in decls if d["kind"] == "related"]
            said_lines += [sc.text for sc in scenes]
            # only the previous chapter's TAIL matters for the reuse window —
            # blocking the whole chapter starved low-region artworks (e2e)
            recent_regions = [d["panel_ref"] for d in decls[-5:]
                              if d["kind"] == "painting_region"]
            prev_tail = " ".join(sc.text for sc in scenes[-2:])
            all_scenes += scenes
            all_decls += decls
            log(f"[narrate-lf] ✓ chapter {pos}/{total} — {len(scenes)} scenes, "
                f"{sum(sc.word_count for sc in scenes)} words")

        total_words = sum(sc.word_count for sc in all_scenes)
        if total_words >= ART_LF_TOTAL_WORDS_FLOOR:
            break
        log(f"[narrate-lf] draw {draw}: {total_words} words < floor "
            f"{ART_LF_TOTAL_WORDS_FLOOR} (≈ 8 min) — redrawing all chapters")
    else:
        raise ValueError(
            f"long-form narration too short after 2 draws: {total_words} words "
            f"(need >= {ART_LF_TOTAL_WORDS_FLOOR} for an 8-minute video)")

    _run_dedupe(all_scenes, ctx, chapters_meta, root, log=log)
    total_words = sum(sc.word_count for sc in all_scenes)  # rewrites may shift it
    validate_cross_chapter([sc.__dict__ for sc in all_scenes], all_decls,
                           panels_by_page=panels_by_page)
    assign_motions(all_decls, intro_scene_id=all_scenes[0].scene_id)

    narration = Narration(
        mode=outline["mode"], title=str(ctx.get("title") or ""),
        hook=all_scenes[0].text, scenes=all_scenes,
        total_word_count=total_words,
        estimated_duration_seconds=round(total_words / ART_WORDS_PER_SEC, 1),
        words_per_second=ART_WORDS_PER_SEC,
        source_project=project_name, llm_model=model_used,
    ).to_dict()
    _inject_chapter_flags(narration, chapters_meta)

    (root / "narration.json").write_text(
        json.dumps(narration, indent=2, ensure_ascii=False))
    save_plan(root, all_decls)
    # A hunt manifest describes scene ids of a narration that no longer
    # exists — force-restoring it onto THIS narration flips fresh
    # painting_region scenes back to "related" (measured e2e round 9:
    # 23 related became 41). Stale manifests die with the old narration.
    (root / "hunt_manifest.json").unlink(missing_ok=True)
    (root / "chapters.json").write_text(
        json.dumps(chapters_meta, indent=2, ensure_ascii=False))
    n_rel = sum(1 for d in all_decls if d["kind"] == "related")
    log(f"[narrate-lf] ✓ {len(all_scenes)} scenes / {total} chapters, "
        f"{total_words} words (~{narration['estimated_duration_seconds']}s) "
        f"[{n_rel} related]")
    return narration
