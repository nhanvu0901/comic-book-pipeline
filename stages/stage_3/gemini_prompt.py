"""
Helper for Gemini-first narration workflow:
1. Generates ready-to-use Gemini prompt (.md) from project context.
2. Parses Master/Gemini-written narration text directly into a valid narration.json
   without using Claude or any external API.
3. Robustly handles Gemini's raw output (hook candidates, chosen hook, audit sections,
   paragraph grouping mapped to answer items).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Callable

from config import PROJECTS_ROOT
from .beat_split import split_hook_fragments, _verbatim_ok
from .explore_answer import build_answer_beats
from .pipeline import load_inputs, filter_story_pages
from .provided_narration import split_sentences


TEMPLATE_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "gemini_qa_writer_template.md"


def generate_gemini_writer_prompt(project_name: str) -> tuple[str, Path]:
    """Build the Gemini markdown prompt for `project_name` and save it as
    projects/<project_name>/<project_name>_writer_prompt.md.
    Returns (prompt_text, file_path).
    """
    root = PROJECTS_ROOT / project_name
    if not root.exists():
        raise FileNotFoundError(f"Project directory not found: {root}")

    answer_path = root / "answer_context.json"
    comic_path = root / "comic_context.json"

    answer_ctx = json.loads(answer_path.read_text(encoding="utf-8")) if answer_path.exists() else {}
    comic_ctx = json.loads(comic_path.read_text(encoding="utf-8")) if comic_path.exists() else {}

    question = (
        answer_ctx.get("question")
        or comic_ctx.get("title")
        or project_name.replace("_", " ").title()
    ).strip()

    items = answer_ctx.get("items") or []
    scout_json_str = json.dumps(items, indent=2, ensure_ascii=False) if items else json.dumps(comic_ctx, indent=2, ensure_ascii=False)

    template = TEMPLATE_PATH.read_text(encoding="utf-8") if TEMPLATE_PATH.exists() else ""
    if not template:
        template = (
            "GEMINI PROMPT — Q&A NARRATION WRITER\n\n"
            "THE QUESTION:\n{{QUESTION}}\n\n"
            "THE SCOUT JSON:\n```json\n{{SCOUT_JSON}}\n```\n"
        )

    rendered = template.replace("{{QUESTION}}", question).replace("{{SCOUT_JSON}}", scout_json_str)

    # Save to project folder as <project_name>_writer_prompt.md
    out_path = root / f"{project_name}_writer_prompt.md"
    out_path.write_text(rendered, encoding="utf-8")

    return rendered, out_path


def clean_and_parse_gemini_script(raw_text: str) -> tuple[str, list[str], str]:
    """Parse raw Gemini output into (hook, body_paragraphs, outro).

    Robustly handles:
    - Trailing metadata: '---', '**Word Count:**', '### FACT TRACE / AUDIT', etc.
    - Multiple hook options: 'Hook 1:', 'Hook 2:', 'Hook 3:'
    - Chosen hook indicators: '**[CHOSEN HOOK: Hook 2]** <text>', '[CHOSEN HOOK: ...] <text>'
    - Paragraph grouping for body scenes
    - Explicit or punchy loop closers as outro
    """
    text = (raw_text or "").strip()
    if not text:
        return "", [], ""

    # 1. Cut trailing audit / metadata / word count
    tail_markers = [
        r"(?m)^---+\s*$",
        r"(?mi)^#+\s*fact\s*trace",
        r"(?mi)^#+\s*audit",
        r"(?mi)^\*+\s*word\s*count\s*:",
        r"(?mi)^word\s*count\s*:",
    ]
    cleaned = text
    for tm in tail_markers:
        m = re.search(tm, cleaned)
        if m:
            cleaned = cleaned[:m.start()].strip()

    # 2. Extract chosen hook if present
    chosen_hook = ""
    m = re.search(r"(?mi)^\*?\*?\[?\s*CHOSEN\s+HOOK[^\]\n]*\]?\*?\*?:?\s*(.+)$", cleaned)
    if m:
        chosen_hook = re.sub(r"^\*+|\*+$", "", m.group(1).strip())
    else:
        # Check if first line/paragraph starts with Hook:
        m2 = re.search(r"(?mi)^\*?\*?(?:HOOK|Hook)\s*(?:\d+)?\*?\*?:\s*(.+)$", cleaned)
        if m2:
            chosen_hook = re.sub(r"^\*+|\*+$", "", m2.group(1).strip())

    # 3. Clean hook candidate lines from body
    lines = []
    for line in cleaned.splitlines():
        l_strip = line.strip()
        if re.match(r"(?i)^\*?\*?Hook\s*\d+\s*\*?\*?:", l_strip):
            continue
        if re.match(r"(?i)^\*?\*?\[?\s*CHOSEN\s+HOOK", l_strip):
            continue
        lines.append(line)

    body_cleaned = "\n".join(lines).strip()

    # 4. Split into paragraphs
    paras = [p.strip() for p in re.split(r"\n\s*\n", body_cleaned) if p.strip()]

    # If no chosen hook found earlier, check if first paragraph is a short opening line
    if not chosen_hook and paras:
        first_sents = split_sentences(paras[0])
        if len(first_sents) == 1 and len(paras[0].split()) <= 20:
            chosen_hook = paras[0]
            paras = paras[1:]

    # 5. Extract outro: check last paragraph
    outro = ""
    if paras:
        last_para = paras[-1]
        m_outro = re.search(r"(?mi)^\*?\*?(?:LOOP\s+CLOSER|OUTRO|Outro|CLOSING\s+LINE)[^:\n]*\*?\*?:\s*(.+)$", last_para)
        if m_outro:
            outro = m_outro.group(1).strip()
            paras = paras[:-1]
        elif len(split_sentences(last_para)) == 1 and len(last_para.split()) <= 20 and len(paras) > 1:
            outro = last_para
            paras = paras[:-1]

    return chosen_hook, paras, outro


def parse_and_save_script(
    project_name: str,
    raw_text: str,
    *,
    log: Callable[[str], None] = print,
) -> dict:
    """Parse Master/Gemini-written narration text into a complete, valid narration.json.

    - Identifies Hook, Body scenes (paragraph-aware), and Outro.
    - Anchors scenes to pages/chapters via answer_context beats.
    - Generates visual_beats for video pacing.
    - Persists narration.json and narration.tts.sha256.
    """
    text = (raw_text or "").strip()
    if not text:
        raise ValueError("Narration text is empty.")

    root = PROJECTS_ROOT / project_name
    comic_ctx, pages = load_inputs(project_name)
    story_pages = filter_story_pages(pages)

    answer_path = root / "answer_context.json"
    answer_ctx = json.loads(answer_path.read_text(encoding="utf-8")) if answer_path.exists() else {}

    # Robust Gemini parse: extract clean hook, body paragraphs, outro
    hook, paras, outro = clean_and_parse_gemini_script(text)

    # Fallback if no paragraphs were extracted
    if not paras and not hook:
        raise ValueError("Could not extract any narrative scenes from the input.")

def _clean_tokens(text: str) -> set[str]:
    words = re.findall(r"\b[a-zA-Z0-9_'-]{3,}\b", text.lower())
    stopwords = {
        "the", "and", "for", "with", "that", "this", "from", "into",
        "his", "her", "their", "was", "were", "then", "out", "all",
        "about", "after", "over", "under", "again", "been", "being",
    }
    return set(words) - stopwords


def _align_paras_to_beats(paras: list[str], beats: list) -> list:
    """Bijectively align k narration paragraphs to k beats based on entity/vocabulary overlap.

    When Master or Gemini writes narration with a different pacing/order (e.g. escalating
    from low twist to highest twist), paragraphs may appear in a different order from the
    original download/candidate order. This finds the optimal 1-to-1 permutation rather than
    blindly assigning paragraph i to beat i.
    """
    if len(paras) != len(beats) or len(beats) <= 1 or len(beats) > 8:
        return beats

    scores = []
    for p in paras:
        p_toks = _clean_tokens(p)
        row = []
        for b in beats:
            chars = getattr(b, "characters_active", []) or []
            b_text = f"{getattr(b, 'name', '')} {' '.join(chars)} {getattr(b, 'summary', '')}"
            b_toks = _clean_tokens(b_text)
            row.append(len(p_toks & b_toks))
        scores.append(row)

    import itertools
    best_perm = None
    best_score = -1
    for perm in itertools.permutations(range(len(beats))):
        total = sum(scores[i][perm[i]] for i in range(len(paras)))
        if total > best_score:
            best_score = total
            best_perm = perm

    identity_score = sum(scores[i][i] for i in range(len(paras)))
    # Only re-order if best permutation is strictly better than sequential order
    if best_perm and best_score > identity_score:
        return [beats[best_perm[i]] for i in range(len(paras))]
    return beats


    # Map to answer beats if available
    beats = []
    if answer_ctx and answer_ctx.get("items"):
        try:
            beats = build_answer_beats(comic_ctx, answer_ctx, story_pages)
        except Exception as e:
            log(f"[parse_script] build_answer_beats error ({e}); fallback to sequential")

    aligned_beats = beats
    if beats and len(paras) == len(beats) and len(beats) > 1:
        aligned_beats = _align_paras_to_beats(paras, beats)

    scenes: list[dict] = []
    scene_id = 1

    # Intro / Hook scene
    hook_page = aligned_beats[0].page_refs[0] if aligned_beats and aligned_beats[0].page_refs else (story_pages[0].get("page_number", 1) if story_pages else 1)
    if hook:
        wc = len(hook.split())
        scenes.append({
            "scene_id": scene_id,
            "text": hook,
            "page_ref": int(hook_page),
            "panel_ref": -1,
            "word_count": wc,
            "target_seconds": round(wc / 2.9, 2),
            "connective": None,
            "beat_id": 0,
            "is_intro": True,
            "is_outro": False,
            "visual_beats": split_hook_fragments(hook) or [hook],
        })
        scene_id += 1

    # Body scenes: Paragraph-aware assignment
    n_beats = len(aligned_beats)
    n_paras = len(paras)

    for p_idx, para in enumerate(paras):
        sentences = split_sentences(para)
        if not sentences:
            continue

        # Determine anchor beat for this paragraph
        if n_beats > 0:
            if n_paras == n_beats:
                b = aligned_beats[p_idx]
            else:
                b_idx = min(int(p_idx / max(n_paras, 1) * n_beats), n_beats - 1)
                b = aligned_beats[b_idx]
            pref = int(b.page_refs[0]) if b.page_refs else 1
            bid = p_idx + 1 if n_paras == n_beats else b.id
        else:
            page_idx = min(int(p_idx / max(n_paras, 1) * len(story_pages)), len(story_pages) - 1) if story_pages else 0
            pref = int(story_pages[page_idx].get("page_number", 1)) if story_pages else 1
            bid = p_idx + 1

        for s in sentences:
            s_clean = s.strip()
            if not s_clean:
                continue
            wc = len(s_clean.split())
            frags = split_hook_fragments(s_clean)
            scenes.append({
                "scene_id": scene_id,
                "text": s_clean,
                "page_ref": pref,
                "panel_ref": -1,
                "word_count": wc,
                "target_seconds": round(wc / 2.9, 2),
                "connective": None,
                "beat_id": bid,
                "is_intro": False,
                "is_outro": False,
                "visual_beats": frags if frags else [s_clean],
            })
            scene_id += 1

    # Outro scene
    outro_page = aligned_beats[-1].page_refs[0] if aligned_beats and aligned_beats[-1].page_refs else (story_pages[-1].get("page_number", 1) if story_pages else 1)
    if outro:
        wc = len(outro.split())
        scenes.append({
            "scene_id": scene_id,
            "text": outro,
            "page_ref": int(outro_page),
            "panel_ref": -1,
            "word_count": wc,
            "target_seconds": round(wc / 2.9, 2),
            "connective": None,
            "beat_id": beats[-1].id if beats else (len(scenes) + 1),
            "is_intro": False,
            "is_outro": True,
            "visual_beats": split_hook_fragments(outro) or [outro],
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

    # Assemble beats list for schema compatibility
    beats_out = []
    if aligned_beats:
        for idx, b in enumerate(aligned_beats, start=1):
            beats_out.append({
                "id": idx,
                "function": "COLD_OPEN" if idx == 1 else ("LANDING" if idx == len(aligned_beats) else "SETUP"),
                "name": b.name,
                "page_refs": b.page_refs,
                "key_panels": b.key_panels,
                "summary": b.summary,
                "characters_active": b.characters_active,
                "cause": b.cause,
            })
    else:
        for i, s in enumerate(scenes, start=1):
            beats_out.append({
                "id": i,
                "function": "BODY",
                "name": f"line {i}",
                "page_refs": [s["page_ref"]],
                "key_panels": [],
                "summary": s["text"],
                "characters_active": [],
                "cause": s["text"],
            })

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
