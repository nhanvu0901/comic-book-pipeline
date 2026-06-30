import json
import stages.stage_3.story_architect as sa
from stages.stage_3.story_architect import _panel_character_index, _coerce_story_map


def _page(panels):
    return {"panels": panels, "source_image": "p.png"}


def test_panel_character_index_from_characters_and_desc():
    pages = [_page([
        {"characters": ["Jim Gordon"], "description": "a security monitor"},
        {"characters": [], "description": "The Grim Knight points a gun"},
    ])]
    idx = _panel_character_index(pages)
    assert "gordon" in idx and "grim" in idx and "knight" in idx
    assert "galactus" not in idx


def test_coerce_drops_malformed_and_defaults_enums():
    raw = {"structure": "linear", "spine": "x", "confidence": "high",
           "characters": [{"name": "A", "role": "weird", "mention": "nope",
                           "visual_available": True},
                          {"role": "x"}]}  # 2nd has no name → dropped
    out = _coerce_story_map(raw)
    assert out is not None and len(out["characters"]) == 1
    c = out["characters"][0]
    assert c["mention"] == "supporting" and c["role"] == "supporting"


def test_coerce_requires_structure_and_spine():
    assert _coerce_story_map({"characters": []}) is None
    assert _coerce_story_map({"structure": "linear"}) is None


def test_analyze_story_disabled(monkeypatch):
    monkeypatch.setattr(sa, "ENABLE_STORY_ARCHITECT", False)
    assert sa.analyze_story({"plot_summary": "x"}, [], model=None, progress=None) is None


def test_analyze_story_bwl_no_panel(monkeypatch):
    monkeypatch.setattr(sa, "ENABLE_STORY_ARCHITECT", True)
    payload = {"structure": "framed_flashback", "spine": "a batman who kills",
               "characters": [
                   {"name": "The Grim Knight", "role": "protagonist",
                    "visual_available": True, "mention": "core"},
                   {"name": "The Batman Who Laughs", "role": "antagonist",
                    "visual_available": False, "mention": "skip",
                    "note": "no distinct panel"}],
               "omit": [], "framing_notes": "BWL unseen master", "confidence": "high"}
    monkeypatch.setattr(sa, "call_with_chain", lambda **k: (json.dumps(payload), "mock"))
    pages = [_page([{"characters": ["The Grim Knight"], "description": "gun batman"}])]
    m = sa.analyze_story({"plot_summary": "p", "title": "Grim Knight"}, pages,
                         model=None, progress=None)
    assert m is not None
    bwl = [c for c in m["characters"] if "Laughs" in c["name"]][0]
    assert bwl["visual_available"] is False and bwl["mention"] == "skip"
    block = sa.render_story_map_block(m)
    assert "Batman Who Laughs" in block and "skip" in block.lower()


def test_analyze_story_graceful_on_bad_llm(monkeypatch):
    monkeypatch.setattr(sa, "ENABLE_STORY_ARCHITECT", True)
    def boom(**k):
        raise RuntimeError("chain exhausted")
    monkeypatch.setattr(sa, "call_with_chain", boom)
    assert sa.analyze_story({"plot_summary": "p"}, [], model=None, progress=None) is None


def test_render_block_empty_when_none():
    assert sa.render_story_map_block(None) == ""


def _map(confidence):
    return {"structure": "framed_flashback", "spine": "s",
            "telling_order": "tell the flashback first",
            "framing_notes": "", "omit": [], "confidence": confidence,
            "characters": [{"name": "Mystery Man", "role": "antagonist",
                            "visual_available": False, "mention": "skip", "note": ""}]}


def test_render_no_panel_only_asserted_when_confident():
    # high confidence → hard "NO PANEL" claim is OK
    assert "NO PANEL" in sa.render_story_map_block(_map("high"))
    # medium/low → softened to "uncertain" (the BWL false-positive guard)
    for conf in ("medium", "low"):
        block = sa.render_story_map_block(_map(conf))
        assert "NO PANEL" not in block
        assert "uncertain" in block.lower()


def test_render_omits_telling_order():
    # telling_order must never leak into the prompt — beat order is the
    # deterministic orderer's job, the soft prose only conflicts with it.
    assert "telling order" not in sa.render_story_map_block(_map("high")).lower()
