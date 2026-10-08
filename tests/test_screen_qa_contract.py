"""Interface contracts of screen_qa's visuals (video-qa/p3-visual):

  * with video-qa/p3-core — the narration.json / screen_context.json it writes are what the beat
    planner, shot builder and Stage 5 consume;
  * with the stable comic path — nothing here may touch utils/text_card.render_text_card, the
    PipelineMode enum, the Stage-3 mode catalog, or any comic-only module.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from stages.stage_3.schema import Narration, Scene
from stages.stage_5.screen_beats import plan_windows, screen_beat_rows
from stages.stage_5.screen_shots import build_shots_for_screen_qa

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "screen_qa_project"
ROOT = Path(__file__).resolve().parents[1]


def test_screen_context_contract_schema():
    """screen_context.json = {question, items:[{entity, event, adaptation_title, year, summary,
    visual_query, source_urls}]} (video-qa/p3-core)."""
    data = json.loads((FIXTURE_DIR / "screen_context.json").read_text())
    assert isinstance(data["question"], str) and data["question"]
    assert isinstance(data["items"], list) and data["items"]
    required = {"entity", "event", "adaptation_title", "year", "summary", "visual_query", "source_urls"}
    for item in data["items"]:
        assert required <= item.keys(), required - set(item)
        assert isinstance(item["year"], int) and item["year"] > 1900
        assert isinstance(item["source_urls"], list) and item["source_urls"]


def test_fixture_narration_is_exactly_what_p3_core_writes():
    """Scene schema, mode screen_qa, visual_beats {text, query} (no beat_id of our own: keys are
    derived from the scheme, not stored)."""
    data = json.loads((FIXTURE_DIR / "narration.json").read_text())
    assert data["mode"] == "screen_qa"
    for sc in data["scenes"]:
        scene = Scene(scene_id=sc["scene_id"], text=sc["text"], page_ref=sc["page_ref"],
                      panel_ref=sc["panel_ref"], word_count=sc["word_count"],
                      target_seconds=sc["target_seconds"], is_intro=sc["is_intro"],
                      is_outro=sc["is_outro"], visual_beats=sc["visual_beats"])
        for vb in scene.visual_beats:
            assert set(vb) == {"text", "query"}


def _p3_core_narration() -> dict:
    """The shape stages/stage_3/screen_qa.write_screen_qa saves: a hook scene, two scenes per
    item, an outro, built with the REAL Scene/Narration dataclasses and saved via to_dict()."""
    def scene(i, text, beats, **kw):
        return Scene(scene_id=i, text=text, page_ref=0, panel_ref=-1, word_count=len(text.split()),
                     target_seconds=round(len(text.split()) / 3.4, 2), visual_beats=beats, **kw)
    scenes = [
        scene(1, "How did the Avengers go back in time?", [
            {"text": "How did the Avengers go back in time?", "query": "Avengers time travel"}],
            is_intro=True),
        scene(2, "In Avengers: Endgame (2019), Scott Lang proposes the Quantum Realm.", [
            {"text": "In Avengers: Endgame (2019),", "query": "Endgame title"},
            {"text": "Scott Lang proposes the Quantum Realm.", "query": "Scott Lang van"}]),
        scene(3, "Tony Stark then solves the navigation problem overnight.", [
            {"text": "Tony Stark then solves the navigation problem overnight.",
             "query": "Tony Stark Mobius"}]),
        scene(4, "That is why the heist worked.", [
            {"text": "That is why the heist worked.", "query": "Avengers hand over circle"}],
            is_outro=True),
    ]
    nar = Narration(mode="screen_qa", title="How They Went Back", hook=scenes[0].text,
                    banner_title="How They Went Back", scenes=scenes)
    return json.loads(json.dumps(nar.to_dict()))


def test_p3_core_shaped_narration_plans_and_builds_end_to_end():
    narration = _p3_core_narration()
    context = json.loads((FIXTURE_DIR / "screen_context.json").read_text())
    rows = screen_beat_rows(narration, context)
    # bookend scenes with ONE fragment keep the legacy keys; the 2-fragment body scene is 0-based
    assert [r.key for r in rows] == ["intro", "2:0", "2:1", "3:0", "outro"]
    assert rows[0].is_intro and rows[-1].is_outro
    for scene in narration["scenes"]:
        mine = [r for r in rows if r.scene_id == scene["scene_id"]]
        assert " ".join(r.text for r in mine).split() == scene["text"].split()      # tiles the scene
        assert [r.query for r in mine] == [b["query"] for b in scene["visual_beats"]]

    t, timings = 0.0, []
    for sc in narration["scenes"]:
        d = sc["word_count"] / 3.4
        timings.append({"scene_id": sc["scene_id"], "start": t, "end": t + d})
        t += d
    wins = plan_windows(narration, timings, audio_duration=t, screen_context=context)
    assert sum(w.frames for w in wins) / 30 >= t
    assert [k for w in wins for k in w.keys] == [r.key for r in rows]

    shots = build_shots_for_screen_qa(narration, timings, None, screen_context=context,
                                      audio_duration=t)
    assert shots and all(s.source_image == "" for s in shots)
    assert sum(s.duration_seconds for s in shots) >= t


def test_p3_core_calls_the_builder_positionally_with_empty_timings():
    """(narration, {}, {}) — the call its first draft of the render step made."""
    assert build_shots_for_screen_qa(_p3_core_narration(), {}, {})


# ─── the stable comic path is untouched ───────────────────────────────────────

def test_render_text_card_and_its_module_are_unchanged():
    """The outro-card Pillow fallback (stages/stage_5/pipeline._build_outro_card) and
    art_pipeline.assemble import render_text_card: a draft once deleted it (B1). The screen card
    is a NEW module (utils/screen_card.py); this file stays byte-for-byte what it was."""
    text = (ROOT / "utils" / "text_card.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert hashlib.sha256(text.encode()).hexdigest() == \
        "c4af2104c29390438ec5fe60d127b978e6eefff72733d85b8f6cdb037d50b372"
    from utils.text_card import render_text_card
    import inspect
    assert list(inspect.signature(render_text_card).parameters) == [
        "path", "width", "height", "background", "lines", "font_path", "logo_path",
        "logo_width", "logo_center_y"]
    import art_pipeline.assemble  # noqa: F401 — still importable (B1 broke exactly this)


def test_screen_qa_adds_no_pipeline_mode_member():
    """ui/screens/s1_identify iterates PipelineMode and indexes MODE_LABELS — a new member there
    is a KeyError on every comic run. screen_qa is identified by narration.json's mode."""
    from config import PipelineMode
    assert "screen_qa" not in {m.value for m in PipelineMode}
    from ui.screens.s1_identify import MODE_LABELS
    assert all(m in MODE_LABELS for m in PipelineMode)


def test_the_stage_3_comic_mode_catalog_is_unchanged_by_the_visual_modules():
    from stages.stage_3.modes import describe_catalog
    assert "screen_qa" not in describe_catalog()


def test_card_fonts_come_from_the_repo_never_a_system_font_by_name():
    for rel in ("utils/screen_card.py", "stages/stage_5/screen_shots.py",
                "stages/stage_5/screen_beats.py", "stages/stage_5/screen_selection.py",
                "ui/screens/s_screen_gate.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "Arial" not in src and "arial" not in src, rel
    from utils.screen_card import REPO_FONT
    assert REPO_FONT.is_file() and REPO_FONT.parent.name == "fonts"


def test_no_comic_only_module_is_imported_by_the_screen_modules():
    """screen_qa is independent of panels: nothing here reaches into the comic panel machinery."""
    import ast
    forbidden = ("_panel_pool", "pages_by_number", "_load_preprocessed_pages", "panel_sheet",
                 "answer_research", "subject_panels", "_crop_panel")
    for rel in ("stages/stage_5/screen_shots.py", "stages/stage_5/screen_beats.py",
                "stages/stage_5/screen_selection.py", "ui/screens/s_screen_gate.py"):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
               {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        imported = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                    for a in n.names} | {n.module or "" for n in ast.walk(tree)
                                         if isinstance(n, ast.ImportFrom)}
        for name in forbidden:
            # pages_by_number is accepted (and ignored) by the builder for build_shots parity
            if name == "pages_by_number" and rel.endswith("screen_shots.py"):
                continue
            assert name not in used and not any(name in i for i in imported), f"{rel} uses {name}"
