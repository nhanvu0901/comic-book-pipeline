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
from .narrate import _hook_is_concrete, _starts_with_connective, cap_facts, region_catalog
from .visual_plan import assign_motions, parse_visual, save_plan, validate_variety
from .config import (
    ART_LF_REHOOK_POSITIONS, ART_LF_SCENE_MAX_WORDS,
    ART_LF_SCENES_PER_CHAPTER_MAX, ART_LF_SCENES_PER_CHAPTER_MIN,
    ART_WORDS_PER_SEC, get_art_project_path,
)

# Forward-reference cues for chapter-ending re-hooks ("but here's where it
# gets stranger"). Lexicon, not a model call — cheap, deterministic, tunable.
_FORWARD_CUES = (
    "what happened next", "stranger", "wasn't the end", "was not the end",
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


_LF_SYSTEM = """You are writing CHAPTER {pos} of {total} of an 8-12 minute
educational art video. Neutral, intimate second-person where natural
("notice how…"), precise — never sensational, never invented.

Hard rules:
1. EVERY factual claim must come from THIS CHAPTER'S FACTS below. No outside
   facts, no speculation.
2. Write {n_min}-{n_max} short scenes. One idea per scene, 8-22 words each
   (hard cap {scene_max}). Causal chaining: each scene follows from the
   previous with "therefore" or "but" logic — never disconnected observations.
3. EVERY scene carries a "visual" object (same schema as the region catalog):
   - {{"kind": "painting_region", "panel_ref": N}} — zooms into that region.
   - {{"kind": "painting_full"}} — whole artwork, AT MOST once in this chapter
     ({full_note}).
   - {{"kind": "related", "subject": "<concrete searchable image>"}} — artist,
     era, place, technique, x-ray. Aim for roughly 30% related scenes.
4. VARIETY: no two consecutive scenes show the same thing; each region at most
   once IN THIS CHAPTER; related subjects all differ. Do NOT use these region
   panel_refs (used by the previous chapter): {blocked_regions}.
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


def build_chapter_scenes(raw: str, pages: list[dict], ctx: dict, chapter: dict,
                         *, scene_id_offset: int, rehook_required: bool,
                         is_first: bool = False, is_last: bool = False,
                         log=print) -> tuple[list[Scene], list[dict]]:
    """Parse + validate ONE chapter's LLM output. Scene ids continue from
    scene_id_offset. Raises ValueError with a feed-back-able message."""
    data = extract_json(raw)
    if data is None:
        raise ValueError(f"chapter {chapter['chapter_id']}: unparseable JSON")
    scenes_raw = data.get("scenes") or []
    if not ART_LF_SCENES_PER_CHAPTER_MIN <= len(scenes_raw) <= ART_LF_SCENES_PER_CHAPTER_MAX:
        raise ValueError(
            f"chapter {chapter['chapter_id']}: {len(scenes_raw)} scenes "
            f"(need {ART_LF_SCENES_PER_CHAPTER_MIN}-{ART_LF_SCENES_PER_CHAPTER_MAX})")

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

    # Per-chapter variety scope (rule: consecutive / region-once / related-dedup /
    # at most one mid-chapter painting_full — validate_variety's existing rules).
    validate_variety([sc.__dict__ for sc in scenes],
                     {d["scene_id"]: d for d in decls})

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
    if not target * 0.6 <= chapter_words <= target * 1.5:
        raise ValueError(
            f"chapter {chapter['chapter_id']}: {chapter_words} words vs target "
            f"{target} (stay within 60-150%)")
    return scenes, decls


def validate_cross_chapter(scenes: list[dict], decls: list[dict]) -> None:
    """Whole-video rules: related subjects globally distinct; a painting region
    must not appear in two ADJACENT chapters."""
    seen_subjects: dict[str, int] = {}
    region_chapters: dict[tuple, set[int]] = {}
    by_id = {s["scene_id"]: s for s in scenes}
    for d in decls:
        ch = int(d.get("chapter_id") or 0)
        if d["kind"] == "related":
            key = " ".join(str(d.get("subject") or "").lower().split())
            if key in seen_subjects and seen_subjects[key] != d["scene_id"]:
                raise ValueError(
                    f"cross-chapter: related subject reused: {key!r} "
                    f"(scenes {seen_subjects[key]} and {d['scene_id']})")
            seen_subjects[key] = d["scene_id"]
        elif d["kind"] == "painting_region":
            s = by_id.get(d["scene_id"]) or {}
            rk = (s.get("page_ref"), d.get("panel_ref"))
            region_chapters.setdefault(rk, set()).add(ch)
    for rk, chs in region_chapters.items():
        ordered = sorted(chs)
        for a, b in zip(ordered, ordered[1:]):
            if b - a == 1:
                raise ValueError(
                    f"cross-chapter: region page {rk[0]} panel {rk[1]} used in "
                    f"adjacent chapters {a} and {b}")


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
    all_scenes: list[Scene] = []
    all_decls: list[dict] = []
    chapters_meta: list[dict] = []
    used_subjects: list[str] = []
    prev_regions: list[int] = []
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
            full_note=("the very first scene may be the full painting"
                       if is_first else "use sparingly"),
            blocked_regions=prev_regions or "none",
            blocked_subjects=used_subjects or "none",
            position_rule=position_rule)
        user = (
            f"VIDEO THROUGH-LINE: {outline.get('through_line', '')}\n"
            f"CHAPTER {pos}/{total}: {ch['title']} (role: {ch['role']}, "
            f"target ~{ch['target_words']} words)\n\n"
            f"PREVIOUS CHAPTER ENDED WITH: {prev_tail or '(video start)'}\n\n"
            f"THIS CHAPTER'S FACTS (the ONLY allowed source of claims):\n"
            + "\n".join(f"- {f}" for f in ch["facts"]) +
            f"\n\nREGION CATALOG (page_ref/panel_ref targets):\n{region_catalog(pages)}\n\n"
            'Return STRICT JSON only:\n'
            '{"scenes": [{"text": "...", "page_ref": 1, "panel_ref": 0,\n'
            '             "visual": {"kind": "painting_region", "panel_ref": 0},\n'
            '             "is_intro": false, "is_outro": false}]}'
        )
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
                    log=log)
                break
            except ValueError as exc:
                last_err = exc
                log(f"[narrate-lf] chapter {pos} attempt {attempt} rejected: {exc}")
                user += (f"\n\nYOUR PREVIOUS ATTEMPT FAILED VALIDATION: {exc}. "
                         "Fix exactly that.")
        else:
            raise ValueError(f"chapter {pos} failed after 3 attempts: {last_err}")

        chapters_meta.append({"chapter_id": ch["chapter_id"], "title": ch["title"],
                              "role": ch["role"],
                              "scene_ids": [sc.scene_id for sc in scenes],
                              "start": None})
        used_subjects += [" ".join(str(d.get("subject") or "").lower().split())
                          for d in decls if d["kind"] == "related"]
        prev_regions = [d["panel_ref"] for d in decls
                        if d["kind"] == "painting_region"]
        prev_tail = " ".join(sc.text for sc in scenes[-2:])
        all_scenes += scenes
        all_decls += decls
        log(f"[narrate-lf] chapter {pos}/{total} — {len(scenes)} scenes, "
            f"{sum(sc.word_count for sc in scenes)} words")

    validate_cross_chapter([sc.__dict__ for sc in all_scenes], all_decls)
    assign_motions(all_decls, intro_scene_id=all_scenes[0].scene_id)

    total_words = sum(sc.word_count for sc in all_scenes)
    narration = Narration(
        mode=outline["mode"], title=str(ctx.get("title") or ""),
        hook=all_scenes[0].text, scenes=all_scenes,
        total_word_count=total_words,
        estimated_duration_seconds=round(total_words / ART_WORDS_PER_SEC, 1),
        words_per_second=ART_WORDS_PER_SEC,
        source_project=project_name, llm_model=model_used,
    ).to_dict()
    # chapter_id rides on the SERIALIZED dict (comic Scene dataclass is
    # read-only; Stage 4 reads narration.json as plain dicts and ignores
    # unknown keys).
    sid_to_ch = {sid: cm["chapter_id"] for cm in chapters_meta
                 for sid in cm["scene_ids"]}
    for s in narration["scenes"]:
        s["chapter_id"] = sid_to_ch.get(s["scene_id"], 0)

    (root / "narration.json").write_text(
        json.dumps(narration, indent=2, ensure_ascii=False))
    save_plan(root, all_decls)
    (root / "chapters.json").write_text(
        json.dumps(chapters_meta, indent=2, ensure_ascii=False))
    n_rel = sum(1 for d in all_decls if d["kind"] == "related")
    log(f"[narrate-lf] {len(all_scenes)} scenes / {total} chapters, "
        f"{total_words} words (~{narration['estimated_duration_seconds']}s) "
        f"[{n_rel} related]")
    return narration
