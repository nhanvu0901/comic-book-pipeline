"""
Helper for Gemini-first narration workflow:
1. Generates ready-to-use Gemini prompt (.md) from project context.
2. Parses Master/Gemini-written narration text directly into a valid narration.json
   without using Claude or any external API.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Callable

from config import PROJECTS_ROOT
from .beat_split import split_hook_fragments, split_visual_beats, _verbatim_ok
from .explore_answer import build_answer_beats, _ordered_items
from .pipeline import load_inputs, filter_story_pages
from .provided_narration import split_hook_and_body, split_sentences


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


def parse_and_save_script(
    project_name: str,
    raw_text: str,
    *,
    log: Callable[[str], None] = print,
) -> dict:
    """Parse Master/Gemini-written narration text into a complete, valid narration.json.

    - Identifies Hook, Body scenes, and Outro.
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

    # Extract hook & body
    hook, body_text = split_hook_and_body(text)
    if not hook:
        # Fallback: check if text begins with 'Hook:' or 'HOOK:'
        hook_match = re.search(r"^(?:HOOK|Hook):\s*(.+)$", text, re.MULTILINE)
        if hook_match:
            hook = hook_match.group(1).strip()
            body_text = re.sub(r"^(?:HOOK|Hook):\s*.+$", "", text, flags=re.MULTILINE).strip()
        else:
            # Check if first paragraph is a short sentence
            paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
            if paras and len(split_sentences(paras[0])) == 1 and len(paras[0].split()) <= 20:
                hook = paras[0]
                body_text = "\n\n".join(paras[1:])
            else:
                hook = ""
                body_text = text

    # Extract outro if marked or distinct
    outro = ""
    outro_match = re.search(r"^(?:OUTRO|Outro):\s*(.+)$", body_text, re.MULTILINE)
    if outro_match:
        outro = outro_match.group(1).strip()
        body_text = re.sub(r"^(?:OUTRO|Outro):\s*.+$", "", body_text, flags=re.MULTILINE).strip()

    # Split body into sentences
    body_sentences = split_sentences(body_text)
    if not body_sentences and not hook:
        raise ValueError("Could not parse any spoken sentences from the input.")

    # If outro wasn't explicitly marked, check if the last sentence looks like a loop/closing line
    if not outro and len(body_sentences) >= 4:
        last_s = body_sentences[-1]
        if len(last_s.split()) <= 18:
            outro = last_s
            body_sentences = body_sentences[:-1]

    # Map to answer beats if available
    beats = []
    if answer_ctx and answer_ctx.get("items"):
        try:
            beats = build_answer_beats(comic_ctx, answer_ctx, story_pages)
        except Exception as e:
            log(f"[parse_script] build_answer_beats error ({e}); fallback to sequential")

    scenes: list[dict] = []
    scene_id = 1

    # Intro / Hook scene
    hook_page = beats[0].page_refs[0] if beats and beats[0].page_refs else (story_pages[0].get("page_number", 1) if story_pages else 1)
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
            "visual_beats": split_hook_fragments(hook),
        })
        scene_id += 1

    # Body scenes
    n_body = len(body_sentences)
    n_beats = len(beats)
    for i, s in enumerate(body_sentences):
        wc = len(s.split())
        if n_beats > 0:
            # Distribute body scenes across beats
            b_idx = min(int(i / max(n_body, 1) * n_beats), n_beats - 1)
            b = beats[b_idx]
            pref = int(b.page_refs[0]) if b.page_refs else 1
            bid = b.id
        else:
            # Distribute across story pages
            p_idx = min(int(i / max(n_body, 1) * len(story_pages)), len(story_pages) - 1) if story_pages else 0
            pref = int(story_pages[p_idx].get("page_number", 1)) if story_pages else 1
            bid = i + 1

        scenes.append({
            "scene_id": scene_id,
            "text": s,
            "page_ref": pref,
            "panel_ref": -1,
            "word_count": wc,
            "target_seconds": round(wc / 2.9, 2),
            "connective": None,
            "beat_id": bid,
            "is_intro": False,
            "is_outro": False,
        })
        scene_id += 1

    # Split visual beats on body scenes rule-based (no LLM call)
    for s in scenes:
        if not s.get("visual_beats"):
            txt = s.get("text", "").strip()
            frags = split_hook_fragments(txt)
            s["visual_beats"] = frags if frags else [txt]

    # Outro scene
    outro_page = beats[-1].page_refs[0] if beats and beats[-1].page_refs else (story_pages[-1].get("page_number", 1) if story_pages else 1)
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
            "visual_beats": split_hook_fragments(outro),
        })

    # Fallback visual beats check
    for s in scenes:
        vb = [b for b in (s.get("visual_beats") or []) if str(b).strip()]
        if vb and not _verbatim_ok(s["text"], [str(b) for b in vb]):
            s["visual_beats"] = []

    total_words = sum(s["word_count"] for s in scenes)
    duration = round(total_words / 2.9, 2)
    wps = round(total_words / max(duration, 0.1), 2)

    title = answer_ctx.get("question") or comic_ctx.get("title") or project_name.replace("_", " ").title()

    # Assemble beats list for schema compatibility
    beats_out = []
    if beats:
        for b in beats:
            beats_out.append({
                "id": b.id,
                "function": b.function,
                "name": b.name,
                "page_refs": b.page_refs,
                "key_panels": b.key_panels,
                "summary": b.summary,
                "characters_active": b.characters_active,
                "cause": b.cause,
            })
    else:
        for i, s in enumerate(body_sentences, start=1):
            beats_out.append({
                "id": i,
                "function": "BODY",
                "name": f"line {i}",
                "page_refs": [scenes[i]["page_ref"] if i < len(scenes) else 1],
                "key_panels": [],
                "summary": s,
                "characters_active": [],
                "cause": s,
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

    # Save master_narration.md backup
    (root / "master_narration.md").write_text(raw_text, encoding="utf-8")

    log(f"[parse_script] Saved narration.json: {len(scenes)} scenes, {total_words} words, ~{duration}s")
    return narration_dict
