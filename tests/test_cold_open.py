"""Cold-open (#1): pick a striking STORY panel for frame 1, not the cover — and never
the final pages (no ending spoiler). Frame-1 evidence (2026-07-03): largest-area alone
opened on a WIDE bubble-heavy dinner-table splash (Doom) and a LANDSCAPE strip that
letterboxed AND rendered MIRRORED (spider-man 'ONE I'M SLYDE' backwards). The scorer
now prefers a portrait, clean, character panel; the cold-open is never mirrored."""
from stages.stage_5 import shots
from stages.stage_5.shots import _build_shots_per_chunk, _cold_open_panel


def _panel(w, h):
    return {"bbox": {"x": 0, "y": 0, "w": w, "h": h}}


def _pg(panels, src="p.png"):
    return {"panels": panels, "source_image": src,
            "image_dimensions": {"width": 1000, "height": 1500}}


def test_cold_open_picks_largest_non_ending_panel():
    pages = {
        1: _pg([_panel(400, 400)]),
        2: _pg([_panel(900, 1400), _panel(200, 200)]),   # biggest eligible panel
        3: _pg([_panel(500, 500)]),
        4: _pg([_panel(999, 1499)]),   # last 2 = ending → excluded even though huge
        5: _pg([_panel(999, 1499)]),
    }
    panel, src = _cold_open_panel(pages)
    assert panel is not None
    assert panel["_page_number"] == 2 and panel["bbox"]["w"] == 900
    assert src == "p.png"


def test_cold_open_skips_cover_and_ending():
    pages = {
        1: {"panels": [_panel(999, 1499)], "page_type": "cover", "source_image": "c.png"},
        2: _pg([_panel(400, 400)]),
        3: _pg([_panel(800, 800)]),   # biggest of the eligible (2,3)
        4: _pg([_panel(999, 1499)]),  # ending excluded
        5: _pg([_panel(999, 1499)]),  # ending excluded
    }
    panel, _ = _cold_open_panel(pages)
    assert panel is not None and panel["_page_number"] == 3   # cover skipped, ending excluded


def test_cold_open_empty_returns_none():
    assert _cold_open_panel({}) == (None, "")


def test_cold_open_prefers_portrait_clean_over_wide_bubble_heavy():
    """Frame-1 defect #1: the scorer must pick a PORTRAIT clean-character panel over a
    LARGER wide panel crammed with speech bubbles (the Doom dinner-table opener)."""
    wide_bubble = {"bbox": {"x": 0, "y": 0, "w": 1400, "h": 650},   # landscape, LARGER area
                   "characters": ["Doom"],
                   "dialog": [{"text": f"line {i}"} for i in range(12)]}  # ~12 bubbles
    portrait_clean = {"bbox": {"x": 0, "y": 0, "w": 700, "h": 1200},  # portrait, smaller area
                      "characters": ["Hero"], "dialog": []}
    pages = {
        1: {"panels": [wide_bubble, portrait_clean], "source_image": "p.png",
            "image_dimensions": {"width": 1500, "height": 1500}},
        2: _pg([_panel(100, 100)]),
        3: _pg([_panel(100, 100)]),
        4: _pg([_panel(100, 100)]),   # ending → excluded
        5: _pg([_panel(100, 100)]),   # ending → excluded
    }
    panel, _ = _cold_open_panel(pages)
    assert panel is not None
    # portrait clean wins DESPITE the wide panel having the larger raw area
    assert panel["bbox"]["w"] == 700 and panel["bbox"]["h"] == 1200


def _intro_narration_inputs(intro_panel):
    """Minimal caption-chunk inputs with a single is_intro scene, plus a monkeypatch
    target that forces _match_panels to return `intro_panel` for the cold-open unit."""
    narration = {"scenes": [{"scene_id": 1, "is_intro": True, "text": "the hook"}]}
    caption_chunks = [{"text": "the hook", "start": 0.0, "end": 2.0}]
    scene_timings = [{"scene_id": 1, "start": 0.0, "end": 2.0}]
    pages = {1: {"panels": [intro_panel], "source_image": "s.png",
                 "image_dimensions": {"width": 1500, "height": 1500}}}
    return narration, caption_chunks, pages, scene_timings


def test_cold_open_shot_is_never_mirrored(monkeypatch):
    """Frame-1 defect #2: the intro/cold-open shot must be un-mirrored UNCONDITIONALLY,
    even for a panel that would otherwise be flipped — a landscape dialogue strip with no
    critical-text hint (the spider-man 'ONE I'M SLYDE' opener that rendered backwards)."""
    intro_panel = {"bbox": {"x": 0, "y": 0, "w": 1400, "h": 500},   # landscape, has dialog
                   "description": "a wide action panel",             # no critical-text hint
                   "dialog": [{"text": "ONE I'M SLYDE"}]}
    # Sanity: nothing but is_intro should suppress the mirror for this panel.
    assert shots._panel_has_critical_text(intro_panel) is False
    assert not intro_panel.get("_whole_page")
    narration, chunks, pages, timings = _intro_narration_inputs(intro_panel)
    monkeypatch.setattr(shots, "_match_panels", lambda *a, **k: [(intro_panel, "s.png")])
    built = _build_shots_per_chunk(narration, chunks, pages, timings)
    assert built and built[0].is_intro is True
    assert built[0].no_mirror is True
