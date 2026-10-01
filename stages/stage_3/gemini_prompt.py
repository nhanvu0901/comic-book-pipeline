"""
Helper for Gemini-first narration workflow:
1. Generates ready-to-use Gemini prompt (.md) from project context.
2. Parses Master/Gemini-written narration text directly into a valid narration.json
   without using Claude or any external API.
3. Handles Gemini's raw output (hook options, chosen hook, headings, labels, audit tails).

THE DOWNLOAD ORDER IS THE ORDER OF EVERYTHING (Master 2026-09-24). Answer item N was
downloaded as chapter N (stages._arc.qa_item_chapters), the prompt hands the items to the
writer numbered in that order and forbids re-ordering, so script paragraph N IS item N.
Nothing here re-sorts or guesses by vocabulary: a script whose shape does not fit the item
list is refused with a readable ScriptMappingError instead of being spread across the wrong
comics (the old proportional spread silently put one item's lines on another's pages).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Callable

from config import PROJECTS_ROOT
from stages._arc import issue_index_of_page, qa_item_chapters
from stages.user_errors import ScriptMappingError
from .beat_split import split_hook_fragments, _verbatim_ok
from .pipeline import load_inputs, filter_story_pages
from .provided_narration import split_sentences


QA_TEMPLATE_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "gemini_qa_writer_template.md"
MICRO_TEMPLATE_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "gemini_micro_moment_writer.md"
TOPIC_QA_TEMPLATE_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "gemini_topic_qa_writer.md"
TEMPLATE_PATH = QA_TEMPLATE_PATH  # backward compatibility

# A hook (template: <=14 words) or closing line is at most this long; an item paragraph
# (template: 40-60 words) is longer. Used only to tell those two apart at the ends.
_SHORT_WORDS = 20
# Below this a body paragraph is a transition line ("Now the worst one."), not an item.
_MIN_ITEM_WORDS = 8


def _find_micro_scout_candidate(project_name: str, root: Path, comic_ctx: dict, state_data: dict) -> dict:
    """Retrieve or reconstruct the verified scout candidate for a micro_moment project."""
    # 1. Existing scout_candidate.json in project
    cand_file = root / "scout_candidate.json"
    if cand_file.exists():
        loaded = _load_json(cand_file)
        if isinstance(loaded, dict) and (loaded.get("what_visibly_happens") or loaded.get("summary")):
            return loaded

    # 2. Existing comic_ctx["scout_candidate"]
    if isinstance(comic_ctx.get("scout_candidate"), dict) and (
        comic_ctx["scout_candidate"].get("what_visibly_happens") or comic_ctx["scout_candidate"].get("summary")
    ):
        return comic_ctx["scout_candidate"]

    # 3. Look up from research_sessions
    try:
        from config import RESEARCH_SESSIONS_ROOT
        # Imported here, not at the top: only a rebuild needs the scout, and loading it with this
        # module would make stage_3 import stage_1, which already imports from stage_3.
        from stages.research_scout.project_factory import build_scout_candidate
        from stages.research_scout.storage import SessionStore
        if RESEARCH_SESSIONS_ROOT.exists():
            store = SessionStore(RESEARCH_SESSIONS_ROOT)
            for sdir in RESEARCH_SESSIONS_ROOT.iterdir():
                if not sdir.is_dir():
                    continue
                s_file = sdir / "session.json"
                if not s_file.exists():
                    continue
                sdata = _load_json(s_file)
                if sdata.get("created_project") == project_name or (
                    state_data.get("scout_session_id") and sdata.get("id") == state_data.get("scout_session_id")
                ):
                    cands = []
                    for c_name in ("candidates.v1.json", "candidates.rev1.v1.json"):
                        cp = sdir / "general" / c_name
                        if cp.exists():
                            cands = _load_json(cp).get("candidates") or []
                            if cands:
                                break
                    if not cands:
                        for cp in (sdir / "general").glob("candidates*.json"):
                            cands = _load_json(cp).get("candidates") or []
                            if cands:
                                break

                    selected_ids = sdata.get("selected_specific_candidate_ids") or []
                    chosen_cand = None
                    if selected_ids and cands:
                        for c in cands:
                            if c.get("id") in selected_ids:
                                chosen_cand = c
                                break
                    if not chosen_cand and cands:
                        chosen_cand = cands[0]

                    gate_data = _load_json(sdir / "specific" / "evidence_gate.v1.json")
                    gates = gate_data.get("gates") if isinstance(gate_data, dict) else []
                    chosen_gate = gates[0] if gates and isinstance(gates[0], dict) else {}

                    if chosen_cand:
                        # Not mapped here: build_scout_candidate is what wrote this candidate when
                        # the project was created, so a rebuild cannot read it differently.
                        result = build_scout_candidate(store, store.load(sdir.name), chosen_cand, chosen_gate)
                        cand_file.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
                        comic_ctx["scout_candidate"] = result
                        (root / "comic_context.json").write_text(json.dumps(comic_ctx, indent=2, ensure_ascii=False), encoding="utf-8")
                        return result
    except Exception:
        pass

    # 4. Fallback: reconstruct from comic_context.json
    char = (comic_ctx.get("characters") or [""])[0] or comic_ctx.get("title", "")
    series_issue_year = comic_ctx.get("series_issue_year") or comic_ctx.get("issues") or comic_ctx.get("title", "")
    target_moment = comic_ctx.get("target_moment") or comic_ctx.get("plot_summary", "")
    summary = comic_ctx.get("plot_summary", "")
    reader_url = comic_ctx.get("batcave_url") or comic_ctx.get("reader_url", "")
    rebuilt = {
        "character": char,
        "series_issue_year": series_issue_year,
        "what_visibly_happens": target_moment,
        "summary": summary,
        "claim_citation": {},
        "verbatim_sentence": "",
        "source_url": reader_url,
        "evidence_urls": [reader_url] if reader_url else [],
        "verdict": "CONFIRMED",
    }
    # A recorded candidate too thin to use as is can still carry the scout's leads. Which fields
    # those are is project_factory's to say, so keep whatever else it records, as recorded.
    recorded = comic_ctx.get("scout_candidate")
    if isinstance(recorded, dict):
        rebuilt.update({key: value for key, value in recorded.items() if key not in rebuilt})
    return rebuilt


def generate_gemini_writer_prompt(project_name: str) -> tuple[str, Path]:
    """Build the Gemini markdown prompt for `project_name` and save it as
    projects/<project_name>/<project_name>_writer_prompt.md.
    Dispatches to micro_moment writer or Q&A writer based on project mode.
    Returns (prompt_text, file_path).
    """
    root = PROJECTS_ROOT / project_name
    if not root.exists():
        raise FileNotFoundError(f"Project directory not found: {root}")

    answer_ctx = _load_json(root / "answer_context.json")
    comic_ctx = _load_json(root / "comic_context.json")
    state_data = _load_json(root / "state.json")

    items = answer_ctx.get("items") or []
    pipeline_mode = (
        comic_ctx.get("pipeline_mode")
        or state_data.get("pipeline_mode")
        or ("explore_answer" if items else "micro_moment")
    )

    if pipeline_mode == "micro_moment":
        cand = _find_micro_scout_candidate(project_name, root, comic_ctx, state_data)
        char = cand.get("character") or comic_ctx.get("title") or "Character"
        event = cand.get("what_visibly_happens") or comic_ctx.get("target_moment") or cand.get("summary") or ""
        issue = cand.get("series_issue_year") or comic_ctx.get("series_issue_year") or comic_ctx.get("issues") or ""
        moment_line = f"{char} — {event} — {issue}".strip(" —")
        scout_json_str = json.dumps(cand, indent=2, ensure_ascii=False)

        template = MICRO_TEMPLATE_PATH.read_text(encoding="utf-8") if MICRO_TEMPLATE_PATH.exists() else ""
        if not template:
            template = (
                "# GEMINI PROMPT — GRIMFRAME MICRO-MOMENT WRITER (GROUNDED)\n\n"
                "## INPUT\n\n```\n"
                "MOMENT:        {{MOMENT}}\n"
                "SCOUT JSON:\n{{SCOUT_JSON}}\n```\n"
            )
        rendered = template.replace(
            "<<one line: character — what happens — series, volume, #issue (year)>>", moment_line
        ).replace("{{MOMENT}}", moment_line)
        rendered = rendered.replace("<<paste the object here>>", scout_json_str).replace(
            "{{SCOUT_JSON}}", scout_json_str
        )
    else:
        question = (
            answer_ctx.get("question")
            or comic_ctx.get("title")
            or project_name.replace("_", " ").title()
        ).strip()

        if items:
            numbered = [{"item_number": n, **item} for n, item in enumerate(items, start=1)]
            scout_json_str = json.dumps(numbered, indent=2, ensure_ascii=False)
        else:
            scout_json_str = json.dumps(comic_ctx, indent=2, ensure_ascii=False)

        template = QA_TEMPLATE_PATH.read_text(encoding="utf-8") if QA_TEMPLATE_PATH.exists() else ""
        if not template:
            template = (
                "GEMINI PROMPT — Q&A NARRATION WRITER\n\n"
                "THE QUESTION:\n{{QUESTION}}\n\n"
                "THE SCOUT JSON:\n```json\n{{SCOUT_JSON}}\n```\n\n"
                "Keep the items in item_number order, one paragraph each. Put the finished "
                "script under a line that says exactly FINAL SCRIPT.\n"
            )

        rendered = template.replace("{{QUESTION}}", question).replace("{{SCOUT_JSON}}", scout_json_str)

    # Save to project folder as <project_name>_writer_prompt.md
    out_path = root / f"{project_name}_writer_prompt.md"
    out_path.write_text(rendered, encoding="utf-8")

    return rendered, out_path


# ─── reading the pasted script ──────────────────────────────────────────────

_FINAL_MARKER_RE = re.compile(r"(?mi)^[\W_]*FINAL\s+SCRIPT[\W_]*$")
# Case-sensitive on purpose: the template tells the writer to answer "NO INFO"; a
# narration line opening "No info on who..." must not be read as a refusal.
_NO_INFO_RE = re.compile(r"(?m)^[\W_]*NO\s+INFO\b(.*)$")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s")
_RULE_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,}|={3,})\s*$")
_TAIL_RE = re.compile(
    r"^[\s#>(\[]*(?:word\s*count|fact[\s-]*trace|audit|sources|citations|references|notes)"
    r"\b[^.!?\n]*$", re.I)
# A writer's self-check heading can name its section anywhere in a short line ("Body
# Word Count: 201 words", "Joke Shape Audit:", "Fact Trace Table:"). Only unambiguous
# audit words here — "sources"/"notes" can open a narration line, so they stay anchored
# to the start in _TAIL_RE above.
_AUDIT_WORD_RE = re.compile(
    r"\b(?:word\s*count|fact[\s-]*trace|audit|self[\s-]*check|check\s*list|checklist)\b", re.I)
# The other audit labels of the micro template's OUTPUT block (the word count and fact trace
# are caught above). Gemini sometimes reorders that block, so each label ends the script on its
# own. A label followed by ":" or the end of the line is strict enough to trust even on a
# markdown heading ("### OPENING CHAIN"), where _TAIL_RE's looser words are not.
_AUDIT_LABEL_RE = re.compile(
    r"^[\s#>(\[\-•–]*(?:opening\s+chain|story\s+frame\s+check|aftermath\s+check|open\s+loops|"
    r"reactions|unsupported\s+facts)\s*(?::|$)", re.I)
_HOOK_OPTION_RE = re.compile(r"^\s*hook\s*(\d+)?\s*[:.)\-–—]\s*(.+)$", re.I)
_CHOSEN_RE = re.compile(r"^\s*\[?\s*chosen\s+hook\b(.*)$", re.I)
_OUTRO_LABEL_RE = re.compile(
    r"^\s*(?:loop\s*closer|outro|closing(?:\s*line)?)\s*[:\-–—]\s*(.+)$", re.I)
_LABEL_WORD_RE = re.compile(
    r"^\s*(?:items?|answers?|entry|entries|parts?|paragraphs?|body|script|narration|final|"
    r"hooks?|outro|closing|intro|loop)\b", re.I)
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-•–]\s+|\d{1,2}[.)]\s+|\[#?\d{1,2}\]\s*|#\d{1,2}[:.)]?\s+)")
_ITEM_PREFIX_RE = re.compile(r"^\s*(?:item|answer)\s*#?\d+\s*[:.)\-–—]\s*", re.I)
_OPTION_REF_RE = re.compile(r"^(?:hook\s*)?(?:option\s*)?#?(\d+)\W*$", re.I)


def _strip_emphasis(line: str) -> str:
    """Markdown emphasis never belongs in narration (TTS and captions would carry it)."""
    return line.replace(" ", " ").replace("*", "").replace("__", "").lstrip("> ").rstrip()


def _is_audit_heading(clean: str) -> bool:
    """A short heading with no sentence punctuation that names a self-check section. The
    writer appends these after the closing line; nothing after one is script."""
    words = clean.split()
    return (0 < len(words) <= 8 and not re.search(r"[.!?\"”]\s*$", clean)
            and bool(_AUDIT_WORD_RE.search(clean)))


def _is_label_line(clean: str, raw: str, *, followed_by_text: bool) -> bool:
    """A short title-like line with no sentence punctuation: 'Item 1 — Deadpool',
    '**Script**'. Narration lines end in punctuation; labels do not.

    A bare bold line ('**Black Panther**') counts only when text follows on the very next
    line — that is a header over its paragraph. Standing alone it may be a bold hook, so
    it is kept: if it is not one, the paragraph count no longer fits and the script is
    refused with a listing, instead of a hook vanishing silently."""
    words = clean.split()
    if not words or len(words) > 8 or re.search(r"[.!?\"”]\s*$", clean):
        return False
    if _LABEL_WORD_RE.match(clean):
        return True
    return followed_by_text and bool(re.match(r"^\s*(\*\*|__).+(\*\*|__)\s*:?\s*$", raw))


def _chosen_hook_text(rest: str, options: dict) -> str:
    """Text of a '[CHOSEN HOOK: Hook 2] <text>' / 'Chosen hook: Hook 2' marker."""
    m = re.match(r"^\s*[:\-–—]?\s*([^\]\n]*?)\s*\]\s*[:\-–—]?\s*(.*)$", rest)
    inner, after = (m.group(1), m.group(2)) if m else (rest.strip(" :-–—"), "")
    candidate = (after or inner).strip()
    ref = _OPTION_REF_RE.match(candidate)
    if ref:
        return options.get(int(ref.group(1)), "")
    return candidate


def _read_script(raw_text: str) -> tuple[str | None, str | None, list[list[str]]]:
    """Clean raw writer output into (explicit_hook, explicit_outro, paragraphs-as-lines).

    Explicit means the writer labelled it (Hook:/CHOSEN HOOK/Outro:). Everything else is
    decided by position later. Headings, label lines and horizontal rules become
    paragraph breaks; an audit/word-count tail ends the script."""
    text = (raw_text or "").strip()
    finals = list(_FINAL_MARKER_RE.finditer(text))
    if finals:
        text = text[finals[-1].end():]

    no_info = _NO_INFO_RE.search(text)
    if no_info:
        note = no_info.group(1).strip(" :-–—")
    # Capture hook options & chosen hook from preamble if FINAL SCRIPT is present
    full_options: dict = {}
    preamble_chosen: str | None = None
    in_opts = False
    for raw in (raw_text or "").splitlines():
        clean_line = _strip_emphasis(raw).strip()
        if _FINAL_MARKER_RE.match(clean_line):
            break
        if re.match(r"(?i)^\s*hook\s+options\b", clean_line):
            in_opts = True
            continue
        if in_opts and re.match(r"(?i)^\s*(?:chosen\s+hook|spoken\s+word|fact\s+trace)\b", clean_line):
            in_opts = False
        m_opt = re.match(r"^\s*(?:hook\s*)?(\d+)\s*[:.)\-–—]\s*(.+)$", clean_line, re.I)
        if m_opt and (in_opts or clean_line.lower().startswith("hook")):
            full_options[int(m_opt.group(1))] = m_opt.group(2).strip()
        m_ch = _CHOSEN_RE.match(clean_line)
        if m_ch:
            preamble_chosen = _chosen_hook_text(m_ch.group(1), full_options)

    options: dict = dict(full_options)
    chosen: str | None = preamble_chosen or None
    outro: str | None = None
    kept: list[str] = []            # "" = paragraph break
    seen_content = False
    lines = text.splitlines()
    for i, raw in enumerate(lines):
        clean = _strip_emphasis(raw)
        if seen_content and _AUDIT_LABEL_RE.match(clean):
            break
        if _HEADING_RE.match(raw) or _RULE_RE.match(raw):
            kept.append("")
            continue
        if not clean.strip():
            kept.append("")
            continue
        if seen_content and (_TAIL_RE.match(clean) or _is_audit_heading(clean)):
            break
        m = _CHOSEN_RE.match(clean)
        if m:
            chosen = _chosen_hook_text(m.group(1), options)
            kept.append("")
            continue
        m = _HOOK_OPTION_RE.match(clean)
        if m:
            options[int(m.group(1)) if m.group(1) else 0] = m.group(2).strip()
            kept.append("")
            continue
        m = _OUTRO_LABEL_RE.match(clean)
        if m:
            outro = m.group(1).strip()
            kept.append("")
            continue
        followed = i + 1 < len(lines) and bool(_strip_emphasis(lines[i + 1]).strip())
        if _is_label_line(clean, raw, followed_by_text=followed):
            kept.append("")
            continue
        line = _ITEM_PREFIX_RE.sub("", _LIST_MARKER_RE.sub("", clean)).strip()
        if line:
            kept.append(line)
            seen_content = True

    hook: str | None = chosen or None
    if hook is None and options and not finals:
        if len(options) > 1:
            raise ScriptMappingError(
                f"Found {len(options)} hook options but no chosen one. Keep only the hook "
                "you want (or paste just the part under FINAL SCRIPT)."
            )
        hook = next(iter(options.values()))

    paragraphs: list[list[str]] = [[]]
    for line in kept:
        if line:
            paragraphs[-1].append(line)
        elif paragraphs[-1]:
            paragraphs.append([])
    return hook, outro, [p for p in paragraphs if p]


def _is_short(paragraph: str) -> bool:
    return len(paragraph.split()) <= _SHORT_WORDS


def _qa_layout(paras: list[str], n_items: int, hook: str | None,
               outro: str | None) -> tuple[str, list[str], str] | None:
    """(hook, body, outro) when exactly one reading of `paras` gives one body paragraph
    per item, else None. A hook/closing slot is either labelled (explicit) or taken from
    the first/last paragraph — and only when that paragraph is short and the body
    paragraphs all look like items. When an end paragraph is NOT taken, it must not be
    short, or it could be a hook/closing line with an item missing elsewhere."""
    fits = []
    for take_hook in ((False,) if hook else (True, False)):
        for take_outro in ((False,) if outro else (True, False)):
            body = paras[int(take_hook): len(paras) - int(take_outro)]
            if len(body) != n_items or not body:
                continue
            if take_hook and not _is_short(paras[0]):
                continue
            if take_outro and not _is_short(paras[-1]):
                continue
            if not hook and not take_hook and _is_short(paras[0]):
                continue
            if not outro and not take_outro and _is_short(paras[-1]):
                continue
            if any(len(p.split()) < _MIN_ITEM_WORDS for p in body):
                continue
            fits.append((paras[0] if take_hook else (hook or ""), body,
                         paras[-1] if take_outro else (outro or "")))
    return fits[0] if len(fits) == 1 else None


def _same_words(a: str, b: str) -> bool:
    return re.findall(r"\w+", a.lower()) == re.findall(r"\w+", b.lower())


def _drop_repeated_ends(hook: str | None, outro: str | None,
                        paragraphs: list[list[str]]) -> list[list[str]]:
    """A labelled hook/closing that the script then repeats as its own paragraph is one line.
    The writer prints its chosen hook again as the first line of FINAL SCRIPT."""
    if hook and paragraphs and _same_words(" ".join(paragraphs[0]), hook):
        paragraphs = paragraphs[1:]
    if outro and paragraphs and _same_words(" ".join(paragraphs[-1]), outro):
        paragraphs = paragraphs[:-1]
    return paragraphs


def _split_qa_script(raw_text: str, n_items: int) -> tuple[str, list[str], str]:
    hook, outro, paragraphs = _read_script(raw_text)
    paragraphs = _drop_repeated_ends(hook, outro, paragraphs)
    paras = [" ".join(p) for p in paragraphs]
    layout = _qa_layout(paras, n_items, hook, outro)
    multi = [p for p in paragraphs if len(p) > 1]
    if layout is None and multi and all(re.search(r"[.!?\"”]$", line) for p in multi for line in p):
        # Parts written with single line breaks between them: read one part per line.
        # Only when every such line ends a sentence — a hard-wrapped paragraph breaks
        # mid-sentence, and reading it line by line would split one item into two.
        lines = [line for p in paragraphs for line in p]
        layout = _qa_layout(lines, n_items, hook, outro)
        if layout is not None:
            paras = lines
    if layout is None:
        listing = "\n".join(
            f"  [{i}] {len(p.split())} words: \"{p[:70]}{'…' if len(p) > 70 else ''}\""
            for i, p in enumerate(paras, start=1))
        raise ScriptMappingError(
            f"The script does not fit the {n_items} answer item(s). Expected an optional hook "
            "line, then exactly ONE paragraph per item in item order, then an optional "
            "closing line — separated by blank lines."
            + (f"\nHook found: \"{hook}\"" if hook else "")
            + f"\nFound {len(paras)} paragraph(s):\n{listing or '  (none)'}"
            + "\nMerge an item that was split, restore one that was dropped, or regenerate "
              "the script with the current prompt."
        )
    return layout


def _split_free_script(raw_text: str) -> tuple[str, list[str], str]:
    """No answer items (recap / micro): paragraphs are spread over the story pages, so
    only the hook and closing line need recognising — a short first/last paragraph."""
    hook, outro, paragraphs = _read_script(raw_text)
    paragraphs = _drop_repeated_ends(hook, outro, paragraphs)
    paras = [" ".join(p) for p in paragraphs]
    if not hook and len(paras) > 1 and _is_short(paras[0]):
        hook = paras.pop(0)
    if not outro and len(paras) > 1 and _is_short(paras[-1]):
        outro = paras.pop()
    return hook or "", paras, outro or ""


# ─── items ↔ downloaded chapters ────────────────────────────────────────────


def _qa_item_anchors(root: Path, items: list[dict], pages: list[dict], *, allow_pending: bool = True) -> list[int]:
    """First page of each item's own chapter, in item order — or ScriptMappingError
    naming every item whose comic is not (or no longer) the one on disk.
    If allow_pending is True, missing source files use placeholder page 1
    so narration can be drafted in Stage 2 before download/preprocessing is complete."""
    manifest = _load_json(root / "raw_comic" / "manifest.json", default=[])
    if allow_pending and (not manifest or not pages):
        return [1] * len(items)

    downloaded = {int(m.get("chapter_index") or 0): str(m.get("reader_url") or "").strip()
                  for m in manifest if isinstance(m, dict)}
    urls = [str(it.get("reader_url") or "").strip() for it in items]
    anchors: list[int] = []
    problems: list[str] = []
    for n, (item, url, chapter) in enumerate(zip(items, urls, qa_item_chapters(urls)), start=1):
        name = f"#{n} {item.get('entity') or item.get('source_comic') or ''}".strip()
        if chapter not in downloaded:
            problems.append(f"item {name}: its comic ({url or 'no reader URL'}) was not "
                            "downloaded — run Download Comic (Stage 3)")
            anchors.append(0)
            continue
        if downloaded[chapter] != url:
            cites = f"now cites {url}" if url else "has no reader URL"
            problems.append(f"item {name}: chapter {chapter} on disk is {downloaded[chapter]}, "
                            f"but the item {cites} — re-download (Stage 3) and "
                            "preprocess (Stage 4)")
            anchors.append(0)
            continue
        chapter_pages = [p for p in pages if issue_index_of_page(p) == chapter]
        story = [int(p.get("page_number") or 0) for p in chapter_pages if p.get("is_story_page")]
        every = [int(p.get("page_number") or 0) for p in chapter_pages]
        anchor = min((pn for pn in story if pn), default=0) or min((pn for pn in every if pn), default=0)
        if not anchor:
            if allow_pending:
                anchor = 1
            else:
                problems.append(f"item {name}: no preprocessed pages for chapter {chapter} — "
                                "run preprocessing (Stage 4)")
        anchors.append(anchor)
    if problems:
        raise ScriptMappingError(
            "Cannot place the script on the downloaded comics:\n  - " + "\n  - ".join(problems))
    return anchors


def answer_items_signature(answer_ctx: dict) -> str:
    """Identity of the item list a script was written for (which comics, in which order)."""
    key = [[str(it.get("entity") or ""), str(it.get("source_comic") or ""),
            str(it.get("reader_url") or "")] for it in answer_ctx.get("items") or []]
    return hashlib.sha1(json.dumps(key, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def saved_script_for_editor(project_name: str) -> tuple[str, str]:
    """(text to pre-fill the script box, note) for the narration step.

    The saved narration is rebuilt one paragraph per beat — hook, one paragraph per item,
    closing line — so approving it again maps the same way. (Joining one scene per
    paragraph turned every SENTENCE into a paragraph.) A script written for a different
    item list is not offered at all: its paragraph N describes some other comic."""
    root = PROJECTS_ROOT / project_name
    narration = _load_json(root / "narration.json")
    scenes = narration.get("scenes") or []
    if scenes:
        answer = _load_json(root / "answer_context.json")
        written_for = narration.get("answer_items_signature")
        if answer.get("items") and written_for and written_for != answer_items_signature(answer):
            return "", ("The answer items changed since the saved script was written — "
                        "paste a new script for the current items.")
        return _script_from_scenes(scenes), ""
    saved = root / "master_narration.md"
    if saved.exists():
        return saved.read_text(encoding="utf-8").strip(), ""
    return "", ""


def _script_from_scenes(scenes: list[dict]) -> str:
    parts: list[str] = []
    intro = [str(s.get("text") or "").strip() for s in scenes if s.get("is_intro")]
    outro = [str(s.get("text") or "").strip() for s in scenes if s.get("is_outro")]
    if any(intro):
        parts.append(" ".join(t for t in intro if t))
    current, buf = object(), []
    for s in scenes:
        if s.get("is_intro") or s.get("is_outro"):
            continue
        if s.get("beat_id") != current and buf:
            parts.append(" ".join(buf))
            buf = []
        current = s.get("beat_id")
        text = str(s.get("text") or "").strip()
        if text:
            buf.append(text)
    if buf:
        parts.append(" ".join(buf))
    if any(outro):
        parts.append(" ".join(t for t in outro if t))
    return "\n\n".join(parts)


# ─── narration.json ─────────────────────────────────────────────────────────


def _scene(scene_id: int, text: str, page_ref: int, beat_id: int, *,
           intro: bool = False, outro: bool = False) -> dict:
    wc = len(text.split())
    frags = split_hook_fragments(text)
    return {
        "scene_id": scene_id,
        "text": text,
        "page_ref": int(page_ref),
        "panel_ref": -1,
        "word_count": wc,
        "target_seconds": round(wc / 2.9, 2),
        "connective": None,
        "beat_id": beat_id,
        "is_intro": intro,
        "is_outro": outro,
        "visual_beats": frags or [text],
    }


def _load_script_inputs(project_name: str) -> tuple[dict, list[dict]]:
    """Load project context and any pages already available for a Stage 2 script draft.

    Page preprocessing happens in Stage 4, so an absent or empty preprocessed/
    directory is a valid state here. Re-anchoring still uses strict load_inputs().
    """
    root = PROJECTS_ROOT / project_name
    ctx_path = root / "comic_context.json"
    if not ctx_path.exists():
        raise FileNotFoundError(f"comic_context.json missing: {ctx_path}")
    comic_ctx = json.loads(ctx_path.read_text(encoding="utf-8"))

    pages: list[dict] = []
    for page_path in sorted((root / "preprocessed").glob("page_*.json")):
        try:
            page = json.loads(page_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(page, dict):
            pages.append(page)
    return comic_ctx, pages


def parse_and_save_script(
    project_name: str,
    raw_text: str,
    *,
    log: Callable[[str], None] = print,
) -> dict:
    """Parse Master/Gemini-written narration text into a complete, valid narration.json.

    Q&A (answer items present): hook, then body paragraph N → item N → the first page of
    item N's downloaded chapter, then the closing line. A script that does not fit the
    items, or items whose comics are not the ones on disk, raise ScriptMappingError.
    Recap / micro (no items): paragraphs are spread over the story pages in order.
    Persists narration.json, narration.tts.sha256 and master_narration.md.
    """
    text = (raw_text or "").strip()
    if not text:
        raise ScriptMappingError("Narration text is empty.")

    root = PROJECTS_ROOT / project_name
    comic_ctx, pages = _load_script_inputs(project_name)
    story_pages = filter_story_pages(pages)
    # Items created without a reader URL (the evidence gate found none) take the chapter
    # downloaded for them, e.g. by Stage 2's "Download from URL(s)".
    from stages.stage_1.answer_research import adopt_downloaded_reader_urls
    adopted = adopt_downloaded_reader_urls(root)
    if adopted:
        log(f"[stage4-gemini] items {adopted} had no reader URL — using the chapters "
            "downloaded for them")
    answer_ctx = _load_json(root / "answer_context.json")
    items = answer_ctx.get("items") or []

    scenes: list[dict] = []
    beats_out: list[dict] = []
    if items:
        hook, body, outro = _split_qa_script(text, len(items))
        anchors = _qa_item_anchors(root, items, pages)
        if hook:
            scenes.append(_scene(len(scenes) + 1, hook, anchors[0], 0, intro=True))
        for n, para in enumerate(body, start=1):
            for sentence in split_sentences(para):
                if sentence.strip():
                    scenes.append(_scene(len(scenes) + 1, sentence.strip(), anchors[n - 1], n))
        if outro:
            scenes.append(_scene(len(scenes) + 1, outro, anchors[-1], len(items), outro=True))
        for n, (item, anchor) in enumerate(zip(items, anchors), start=1):
            entity = str(item.get("entity") or "").strip()
            cause = str(item.get("how_or_why") or "").strip()
            beats_out.append({
                "id": n,
                "function": "COLD_OPEN" if n == 1 else ("LANDING" if n == len(items) else "SETUP"),
                "name": entity or f"item {n}",
                "page_refs": [anchor],
                "key_panels": [],
                "summary": cause,
                "characters_active": [entity] if entity else [],
                "cause": cause,
            })
        log(f"[parse_script] {len(body)} paragraph(s) → items 1..{len(items)} "
            f"(pages {', '.join(str(a) for a in anchors)})")
    else:
        hook, paras, outro = _split_free_script(text)
        if not paras and not hook:
            raise ScriptMappingError("Could not extract any narrative scenes from the input.")
        numbers = [int(p.get("page_number") or 1) for p in story_pages] or [1]
        if hook:
            scenes.append(_scene(len(scenes) + 1, hook, numbers[0], 0, intro=True))
        for p_idx, para in enumerate(paras):
            page_ref = numbers[min(int(p_idx / max(len(paras), 1) * len(numbers)), len(numbers) - 1)]
            for sentence in split_sentences(para):
                if sentence.strip():
                    scenes.append(_scene(len(scenes) + 1, sentence.strip(), page_ref, p_idx + 1))
        if outro:
            scenes.append(_scene(len(scenes) + 1, outro, numbers[-1], len(paras) + 1, outro=True))
        for i, s in enumerate(scenes, start=1):
            beats_out.append({
                "id": i, "function": "BODY", "name": f"line {i}",
                "page_refs": [s["page_ref"]], "key_panels": [],
                "summary": s["text"], "characters_active": [], "cause": s["text"],
            })

    # Fallback visual beats check
    for s in scenes:
        vb = [b for b in (s.get("visual_beats") or []) if str(b).strip()]
        if vb and not _verbatim_ok(s["text"], [str(b) for b in vb]):
            s["visual_beats"] = [s["text"]]

    total_words = sum(s["word_count"] for s in scenes)
    duration = round(total_words / 2.9, 2)
    wps = round(total_words / max(duration, 0.1), 2)

    title = answer_ctx.get("question") or comic_ctx.get("title") or project_name.replace("_", " ").title()

    narration_dict = {
        "title": title,
        "hook": hook,
        "scenes": scenes,
        "total_word_count": total_words,
        "estimated_duration_seconds": duration,
        "words_per_second": wps,
        "source_project": project_name,
        "llm_model": "manual (Gemini Master-written)",
        "beats": beats_out,
        "glossary": {"characters": {}},
    }
    if items:
        narration_dict["answer_items_signature"] = answer_items_signature(answer_ctx)

    # Save narration.json
    out_narration = root / "narration.json"
    out_narration.write_text(json.dumps(narration_dict, indent=2, ensure_ascii=False), encoding="utf-8")

    # Save sha256
    text_corpus = " ".join(s["text"] for s in scenes)
    h = hashlib.sha256(text_corpus.encode("utf-8")).hexdigest()
    (root / "narration.tts.sha256").write_text(h)

    # Save raw input to master_narration.md for audit/record
    (root / "master_narration.md").write_text(raw_text, encoding="utf-8")

    log(f"[parse_script] Saved narration.json: {len(scenes)} scenes, {total_words} words, ~{duration}s")
    return narration_dict


def _load_json(path: Path, default=None):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {} if default is None else default
    return data if isinstance(data, (dict, list)) else ({} if default is None else default)


def reanchor_narration_to_pages(project_name: str, log: Callable[[str], None] = print) -> bool:
    """Re-anchor an existing narration.json to preprocessed pages once comics are downloaded & preprocessed.
    Called after Stage 4 Preprocess Pages completes, or upon entering Stage 5 Review Beats."""
    root = PROJECTS_ROOT / project_name
    narration_file = root / "narration.json"
    if not narration_file.exists():
        return False
    narration = _load_json(narration_file)
    scenes = narration.get("scenes") or []
    if not scenes:
        return False

    comic_ctx, pages = load_inputs(project_name)
    if not pages:
        return False

    answer_ctx = _load_json(root / "answer_context.json")
    items = answer_ctx.get("items") or []

    if items:
        try:
            anchors = _qa_item_anchors(root, items, pages, allow_pending=False)
        except Exception as e:
            log(f"[reanchor] cannot re-anchor items: {e}")
            return False

        for s in scenes:
            if s.get("is_intro"):
                s["page_ref"] = anchors[0]
            elif s.get("is_outro"):
                s["page_ref"] = anchors[-1]
            else:
                beat_id = s.get("beat_id", 1)
                idx = max(0, min(int(beat_id) - 1, len(anchors) - 1))
                s["page_ref"] = anchors[idx]

        beats = narration.get("beats") or []
        for n, (b, anchor) in enumerate(zip(beats, anchors), start=1):
            b["page_refs"] = [anchor]

        log(f"[reanchor] re-anchored {len(scenes)} scenes to pages {', '.join(str(a) for a in anchors)}")
    else:
        story_pages = filter_story_pages(pages)
        numbers = [int(p.get("page_number") or 1) for p in story_pages] or [1]
        body_scenes = [s for s in scenes if not s.get("is_intro") and not s.get("is_outro")]
        for idx, s in enumerate(body_scenes):
            page_ref = numbers[min(int(idx / max(len(body_scenes), 1) * len(numbers)), len(numbers) - 1)]
            s["page_ref"] = page_ref
        for s in scenes:
            if s.get("is_intro"):
                s["page_ref"] = numbers[0]
            elif s.get("is_outro"):
                s["page_ref"] = numbers[-1]
        beats = narration.get("beats") or []
        if len(beats) == len(scenes):
            for b, s in zip(beats, scenes):
                b["page_refs"] = [s["page_ref"]]
        else:
            for b in beats:
                b_id = b.get("id", 1)
                matching = [s["page_ref"] for s in scenes if s.get("scene_id") == b_id or s.get("beat_id") == b_id]
                if matching:
                    b["page_refs"] = [matching[0]]
        log(f"[reanchor] re-anchored {len(scenes)} scenes across pages {numbers[0]}..{numbers[-1]}")

    # Save updated narration
    narration_file.write_text(json.dumps(narration, indent=2, ensure_ascii=False), encoding="utf-8")
    narration_tts = root / "narration.tts.sha256"
    raw_spoken = " ".join(str(s.get("text") or "").strip() for s in scenes)
    narration_tts.write_text(hashlib.sha256(raw_spoken.encode("utf-8")).hexdigest())
    return True
