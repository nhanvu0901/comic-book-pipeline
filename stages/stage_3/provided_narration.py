"""recap + micro_moment from a narration MASTER already wrote.

Master 2026-07-31: "recap, micro moment will be the same as the Q&A which minimal the
function of the writer. I will provide you the narration — just trim it, make intro and
outro, just that, keep minimal."

So this path does FOUR things and nothing else:

  1. splits Master's prose into scenes                       — pure code, no LLM
  2. trims it into the mode's word band, if it overruns      — LLM, drops WHOLE sentences
  3. writes a title, a hook and an outro                     — LLM, one call
  4. splits every scene into verbatim visual_beats           — the existing splitter

WHY THE SPLIT IS NOT AN LLM CALL. Q&A hands the writer research NOTES, so an LLM has to
compose sentences from them. Here the input is already finished spoken prose that Master
approved. Handing it to a model can only lose: this session alone produced a narration
whose every action beat was invented (a Batman scene that does not exist on the page) and
another where a VLM's hallucinated surname flowed straight into the script. Sentence
splitting is mechanical, so it is done mechanically.

TRIMMING DROPS WHOLE SENTENCES. It never rewrites one. A "shortened" sentence is a
reworded sentence, and rewording is the thing this path exists to avoid — so when the text
runs long the model chooses which sentences to LOSE, and the log names every one of them.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from config import PROJECTS_ROOT

from ._llm import call_with_chain
from .beat_split import _verbatim_ok, split_hook_fragments, split_visual_beats
from .schema import Beat, Glossary, Narration

# Master drops the spoken body here. Checked for every mode except explore_answer, which
# already has its own minimal writer (this path was modelled on it).
PROVIDED_FILENAMES = ("master_narration.md", "master_narration.txt")

# Bands, mirroring each mode's own writer so a provided script is held to the same length
# as a generated one. Imported lazily in _band() to avoid a circular import.
_BAND_FALLBACK = (120, 320)


def provided_narration_path(project: str) -> Path | None:
    """The Master-written narration for `project`, or None. Empty file == absent."""
    root = PROJECTS_ROOT / str(project or "")
    for name in PROVIDED_FILENAMES:
        p = root / name
        if p.exists() and p.read_text().strip():
            return p
    return None


_HOOK_PREFIX = re.compile(r"^\s*hook\s*:\s*", re.I)


def split_hook_and_body(text: str) -> tuple[str, str]:
    """(hook, body) from Master's file. Returns ("", text) when no hook is supplied.

    Master writes the hook as the FIRST paragraph, on its own, then a blank line, then the
    body — that is the shape of every script he has pasted. It is his line and it must be
    spoken verbatim; generating a replacement threw away the whole reason he wrote one.

    A leading "HOOK:" marks it explicitly. Otherwise the first paragraph is the hook only
    when it is a SINGLE sentence and more paragraphs follow — so a body that just happens
    to be split into paragraphs does not lose its opening line."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", str(text or "")) if p.strip()]
    if not paras:
        return "", ""
    first = paras[0]
    if _HOOK_PREFIX.match(first):
        return _HOOK_PREFIX.sub("", first).strip(), "\n\n".join(paras[1:])
    if len(paras) > 1 and len(split_sentences(first)) == 1:
        return first, "\n\n".join(paras[1:])
    return "", "\n\n".join(paras)


def _band(mode: str) -> tuple[int, int]:
    if mode == "micro_moment":
        from .micro_moment import _MICRO_WORDS_MIN, _MICRO_WORDS_MAX
        return _MICRO_WORDS_MIN, _MICRO_WORDS_MAX
    from .write_script import _TARGET_WORDS_MIN, _TARGET_WORDS_MAX
    return _TARGET_WORDS_MIN, _TARGET_WORDS_MAX


# Sentence end: ., ! or ? followed by space + a capital/quote. Abbreviations that would
# otherwise split a sentence in half mid-name.
_ABBREV = re.compile(r"\b(Mr|Mrs|Ms|Dr|St|Sgt|Capt|Lt|vs|etc|No|Vol|Jr|Sr)\.$", re.I)
_SENT_END = re.compile(r'(?<=[.!?])["”\')]*\s+(?=["“(\[]?[A-Z0-9])')


def split_sentences(text: str) -> list[str]:
    """Split prose into spoken sentences, VERBATIM — pure code, no model.

    Blank-line paragraphs are honoured as hard breaks so Master can force a scene split
    by pressing enter twice. `_ABBREV` keeps "Mr. Freeze" and "vs." in one piece."""
    out: list[str] = []
    for para in re.split(r"\n\s*\n", str(text or "")):
        para = " ".join(para.split())
        if not para:
            continue
        parts, buf = [], ""
        for piece in _SENT_END.split(para):
            buf = f"{buf} {piece}".strip() if buf else piece
            if _ABBREV.search(buf):
                continue            # ended on an abbreviation — keep reading
            parts.append(buf)
            buf = ""
        if buf:
            parts.append(buf)
        out.extend(p for p in parts if p.strip())
    return out


_TRIM_SYSTEM = """You are TIGHTENING a narration script that is already written. It runs long.

KEEP EVERY MEANING. LOSE ONLY WORDS. This is a compression pass, not an edit pass and not a \
cut pass. Every event, every name, every number, every consequence in the input must still be \
in your output. If you cannot fit something, you have not compressed hard enough yet — you may \
not solve it by dropping the fact.

What goes: filler, hedges, throat-clearing ("it turns out that", "what happens next is"), \
adjectives doing no work, a clause restating what the sentence already said, and background a \
neighbouring sentence already established. What stays: the actor, the act, the consequence, and \
every proper name and figure exactly as written.

TIGHTENING IS NOT VAGUENESS. The concrete part is the part that survives. Turning "he admitted \
he killed his own younger self" into "he confessed a terrible secret" is a FAILURE, not a trim — \
the specific act IS the sentence.

Rules:
  - Same number of output sentences as input, in the same order, one for one. Merge nothing, \
reorder nothing, drop nothing. A sentence already at its shortest comes back byte-identical.
  - Touch as few words as possible. A sentence that is already tight is left exactly alone.
  - Never invert a fact (if the input says the BODY dies and the MIND survives, keep it that \
way round) and never add a motive, belief or consequence the input does not state.
  - Keep the author's voice: same tense, same register, same sentence shape where possible.

Return ONLY JSON, no markdown fences:
{"scenes": ["<sentence 1, tightened or unchanged>", "<sentence 2>", ...]}"""

_FRAME_SYSTEM = """You write the framing lines around a comic narration someone else \
has already written. The body below is finished and is NOT yours to change — you are only \
writing what comes before it and after it.

  * "title" — 4-8 words, a statement. No question mark, no emoji, no hashtag, no issue \
number, no series name. Name the SUBJECT and the thing that makes them worth 60 seconds.
  * "hook" — the FIRST spoken line, at most 26 words, and it must lead straight into the \
body's opening sentence. State the CONCRETE thing at stake: name the main character in the \
first few words, then the fact that makes this worth watching. A viewer must be able to say \
what the video is about after hearing only this line.
    BANNED — content-free clickbait: "wait until you see...", "you won't believe...", \
"shouldn't even be possible", "makes no sense", or any line promising a surprise without \
naming anything. If the hook would still make sense pasted onto a different video, it is wrong.
  * "outro" — the LAST spoken line, 6-16 words. It must NOT restate the body's final \
sentence in other words. Say what the whole thing MEANS, or land one hard image the body \
earned. Closing on a word from the hook (or its exact opposite) is the strongest ending.

FIDELITY IS ABSOLUTE. Every fact you use must already be in the body below. Do not invent a \
motive, a consequence, or an event — if the body does not say it, you may not either.

Return ONLY JSON, no markdown fences:
{"title": "...", "hook": "...", "outro": "..."}"""


_KEEPWORD_RE = re.compile(r"\b(?:[A-Z][\w''-]+|\d[\d,.]*)\b")
_KEEPWORD_STOP = {"The", "A", "An", "And", "But", "Then", "He", "She", "They", "It", "His",
                  "Her", "Their", "Its", "This", "That", "There", "When", "While", "After",
                  "Before", "Because", "So", "If", "In", "On", "At", "By", "For", "To",
                  "With", "From", "Not", "No", "Now", "Only", "Every", "One", "Two", "I"}


def _keepwords(text: str) -> set[str]:
    """Proper names and figures — the concrete load a compression pass must not drop.

    Mid-sentence capitals and digits are the mechanically checkable part of "keep the
    meaning". A model that turns "he killed his own younger self" into "he confessed a
    terrible secret" is not caught by this, but one that quietly loses "Darkseid", "Damian"
    or "1977" is — and that is the failure that actually keeps happening."""
    return {w for w in _KEEPWORD_RE.findall(text or "") if w not in _KEEPWORD_STOP}


def _trim_to_band(sentences: list[str], word_max: int, *,
                  log: Callable[[str], None], model: str | None) -> list[str]:
    """Tighten the body into the band. Compression only — no sentence is ever dropped.

    Master 2026-07-31: cutting whole sentences was too aggressive; the point is to
    summarise WITHOUT losing meaning. So the model rewords as little as it can, one
    sentence in one sentence out, and the validator refuses any answer that lost a proper
    name or a number."""
    total = sum(len(s.split()) for s in sentences)
    if total <= word_max:
        return sentences
    log(f"[provided] body is {total}w, band ceiling {word_max}w — tightening "
        f"(no sentence is dropped)")
    numbered = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sentences))
    user = (f"WORD BUDGET: at most {word_max} words across all sentences "
            f"(currently {total} — you need to save about {total - word_max}).\n\n"
            f"SENTENCES ({len(sentences)} in, {len(sentences)} out):\n{numbered}")
    must_keep = _keepwords(" ".join(sentences))

    def _valid(out: str) -> bool:
        try:
            got = json.loads(re.sub(r"^```[a-z]*|```$", "", out.strip())).get("scenes")
        except Exception:
            return False
        if not isinstance(got, list) or len(got) != len(sentences):
            return False
        if not all(isinstance(s, str) and s.strip() for s in got):
            return False
        if sum(len(s.split()) for s in got) > word_max:
            return False
        return not (must_keep - _keepwords(" ".join(got)))

    try:
        content, _ = call_with_chain(system=_TRIM_SYSTEM, user=user, max_tokens=1600,
                                     label="provided-tighten", validator=_valid,
                                     models=[model] if model else None)
        kept = [" ".join(str(s).split()).strip()
                for s in json.loads(re.sub(r"^```[a-z]*|```$", "", content.strip()))["scenes"]]
    except Exception as exc:
        # Ship Master's words UNCHANGED and say so. The old fallback silently cut the tail;
        # an over-long script Master can see beats a short one that lost its ending.
        log(f"[provided] ⚠ tighten failed ({exc!r}) — shipping the body at {total}w, "
            f"{total - word_max}w over. Nothing was changed or dropped.")
        return sentences
    for before, after in zip(sentences, kept):
        if before != after:
            log(f"[provided] tightened: {before}\n[provided]         -> {after}")
    log(f"[provided] {len(kept)} sentence(s) kept, {total}w -> "
        f"{sum(len(s.split()) for s in kept)}w")
    return kept


def _frame_lines(body: list[str], comic_context: dict, *,
                 log: Callable[[str], None], model: str | None,
                 given_hook: str = "") -> tuple[str, str, str]:
    """(title, hook, outro). Returns empty strings if the model cannot be reached — the
    caller then ships the body alone rather than inventing framing itself."""
    title_hint = str(comic_context.get("title") or "")
    # A hook Master wrote is FINAL. Show it to the model so the title and outro answer it,
    # and forbid a replacement — regenerating it is how his opening line got thrown away.
    hook_block = (f"HOOK (Master wrote this — it is FIXED, do not rewrite it, do not return "
                  f"a different one):\n{given_hook}\n\n" if given_hook else "")
    user = (f"COMIC: {title_hint}\n\n{hook_block}"
            f"BODY (already written — do not change it):\n" + "\n".join(body))

    def _valid(out: str) -> bool:
        try:
            d = json.loads(re.sub(r"^```[a-z]*|```$", "", out.strip()))
        except Exception:
            return False
        need = ("title", "outro") if given_hook else ("title", "hook", "outro")
        if not all(str(d.get(k, "")).strip() for k in need):
            return False
        hk = given_hook or str(d.get("hook", ""))
        return len(hk.split()) <= 40 and len(str(d["outro"]).split()) <= 20

    try:
        content, used = call_with_chain(system=_FRAME_SYSTEM, user=user, max_tokens=500,
                                        label="provided-frame", validator=_valid,
                                        models=[model] if model else None)
        d = json.loads(re.sub(r"^```[a-z]*|```$", "", content.strip()))
        log(f"[provided] framing via {used}")
        hook = given_hook or str(d.get("hook", "")).strip()
        return str(d["title"]).strip(), hook, str(d["outro"]).strip()
    except Exception as exc:
        log(f"[provided] ⚠ framing call failed ({exc!r}) — no title/outro generated")
        return title_hint, given_hook, ""


def write_from_provided(
    comic_context: dict,
    story_pages: list[dict],
    mode: str,
    text: str,
    *,
    model: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> Narration:
    """Build a Narration from prose Master wrote. See the module docstring."""
    from .write_script import _to_narration

    log = progress or (lambda _m: None)
    word_min, word_max = _band(mode)
    given_hook, body_text = split_hook_and_body(text)
    if given_hook:
        log(f"[provided] hook is Master's, spoken verbatim: {given_hook}")
    sentences = split_sentences(body_text)
    if not sentences:
        raise RuntimeError("provided narration is empty — nothing to narrate")
    log(f"[provided] {len(sentences)} sentence(s), "
        f"{sum(len(s.split()) for s in sentences)}w (band {word_min}-{word_max})")

    body = _trim_to_band(sentences, word_max, log=log, model=model)
    total = sum(len(s.split()) for s in body)
    if total < word_min:
        # Never pad — that would be writing. Say it plainly and let Master decide.
        log(f"[provided] ⚠ body is {total}w, under the {word_min}w floor for {mode} — "
            f"shipping as-is (the writer does not pad)")

    title, hook, outro = _frame_lines(body, comic_context, log=log, model=model,
                                      given_hook=given_hook)

    scenes: list[dict] = []
    if hook:
        scenes.append({"text": hook, "page_ref": 0, "panel_ref": -1, "connective": None,
                       "beat_id": 0, "is_intro": True,
                       # Fragment the hook so it cuts instead of freezing on one panel.
                       "visual_beats": split_hook_fragments(hook)})
    # page_ref 0 / panel_ref -1 on purpose: under manual-first there is no matcher to
    # anchor to, and Master picks every panel in the review UI. Leaving a guessed anchor
    # here would just be a wrong number that looks authoritative.
    for i, s in enumerate(body, start=1):
        scenes.append({"text": s, "page_ref": 0, "panel_ref": -1, "connective": None,
                       "beat_id": i, "is_intro": False, "is_outro": False})
    if outro:
        scenes.append({"text": outro, "page_ref": 0, "panel_ref": -1, "connective": None,
                       "beat_id": len(body) + 1, "is_outro": True,
                       "visual_beats": split_hook_fragments(outro)})

    split_visual_beats([s for s in scenes if not s.get("is_intro")
                        and not s.get("is_outro")], progress=log)
    for s in scenes:
        vb = [b for b in (s.get("visual_beats") or []) if str(b).strip()]
        if vb and not _verbatim_ok(s["text"], [str(b) for b in vb]):
            log(f"[provided] ⚠ non-verbatim split dropped on scene {s.get('beat_id')}")
            s["visual_beats"] = []

    beats = [Beat(id=i, function="BODY", name=f"line {i}", summary=s)
             for i, s in enumerate(body, start=1)]
    nar = _to_narration({"scenes": scenes, "title": title, "hook": hook},
                        beats, Glossary(), mode, "provided (Master-written)")
    nar.title = title or nar.title
    nar.hook = hook or nar.hook
    log(f"[provided] {len(nar.scenes)} scene(s), {nar.total_word_count}w, "
        f"~{nar.estimated_duration_seconds:.0f}s")
    return nar
