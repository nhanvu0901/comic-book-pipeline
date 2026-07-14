"""WRITER-PICKS-PANEL (1:1 narration↔panel for micro_moment mode).

The micro writer now emits visual_beats as {"text","page","panel"} objects, each pinning the
exact comic panel that draws that clause. Verifies:
  (a) a writer response with dict beats survives Stage 3 onto the Narration as dicts;
  (b) Stage 5 (_build_shots_per_chunk) binds a pinned beat straight to that page/panel and
      does NOT call the cosine matcher for it;
  (c) recap-shape string beats route through the matcher for EVERY unit, in order (the old
      behavior is byte-identical — no pin ever short-circuits a recap render);
  (d) a pin to a panel that isn't in the pool logs a warning and falls back to the matcher;
  (e) the writer's user prompt carries the PANEL MENU it must pick from.

No network: the outliner, the LLM writer/banner calls, and _match_panels are monkeypatched.
"""
import json

import pytest

import stages.stage_3.micro_moment as mm
import stages.stage_3.write_script as ws
import stages.stage_5.shots as shots
from stages.stage_3.schema import Beat


# ── shared Stage-5 fixtures ──────────────────────────────────────────────────
def _page(pn, npanels):
    return {"page_number": pn, "is_story_page": True, "source_image": f"/p{pn}.png",
            "image_dimensions": {"width": 1000, "height": 1500},
            "panels": [{"index": i, "bbox": {"x": i * 10, "y": 0, "w": 100 + i, "h": 200},
                        "description": f"panel {i} on page {pn}"} for i in range(npanels)]}


_PAGES = {3: _page(3, 2)}
_CHUNKS = [{"text": "A big fight", "start": 0.0, "end": 1.5},
           {"text": "Frank wins", "start": 1.5, "end": 3.0}]
_TIMINGS = [{"scene_id": 1, "start": 0.0, "end": 3.0}]


def _narr(visual_beats):
    return {"scenes": [{"scene_id": 1, "text": "A big fight Frank wins",
                        "page_ref": 3, "panel_ref": -1, "visual_beats": visual_beats}]}


def _bbox_w(shot):
    return shot.panel_bbox["w"]


# ── (b) pinned beats bind directly, matcher NOT called ───────────────────────
def test_pinned_beats_bind_directly_and_skip_matcher(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("_match_panels must NOT be called when every beat is pinned")
    monkeypatch.setattr(shots, "_match_panels", _boom)

    narr = _narr([{"text": "A big fight", "page": 3, "panel": 0},
                  {"text": "Frank wins", "page": 3, "panel": 1}])
    built = shots._build_shots_per_chunk(narr, _CHUNKS, _PAGES, _TIMINGS, project=None)

    assert len(built) == 2
    assert _bbox_w(built[0]) == 100 and _bbox_w(built[1]) == 101   # panel 0 then panel 1
    assert built[0].source_image == "/p3.png"


# ── (c) recap string beats → every unit goes through the matcher, in order ───
def test_recap_string_beats_route_every_unit_through_matcher(monkeypatch):
    calls = {}
    panel_a = {"index": 0, "bbox": {"x": 0, "y": 0, "w": 700, "h": 900}, "_page_number": 3}
    panel_b = {"index": 1, "bbox": {"x": 0, "y": 0, "w": 800, "h": 900}, "_page_number": 3}

    def _fake_match(units_arg, pbn, c2n, *, project=None, narration=None):
        calls["units"] = list(units_arg)
        return [(panel_a, "/pA.png"), (panel_b, "/pB.png")][:len(units_arg)]
    monkeypatch.setattr(shots, "_match_panels", _fake_match)

    narr = _narr(["A big fight", "Frank wins"])       # recap shape: plain strings, no pins
    built = shots._build_shots_per_chunk(narr, _CHUNKS, _PAGES, _TIMINGS, project=None)

    # every unit reached the matcher, in order, with the exact old (scene, spoken) input
    assert [t for _sc, t in calls["units"]] == ["A big fight", "Frank wins"]
    assert len(built) == 2
    assert _bbox_w(built[0]) == 700 and _bbox_w(built[1]) == 800


# ── (d) invalid pin → warning + matcher fallback for that unit only ──────────
def test_invalid_pin_falls_back_to_matcher_with_warning(monkeypatch, capsys):
    fallback_panel = {"index": 1, "bbox": {"x": 0, "y": 0, "w": 800, "h": 900}, "_page_number": 3}

    def _fake_match(units_arg, pbn, c2n, *, project=None, narration=None):
        assert [t for _sc, t in units_arg] == ["Frank wins"]   # only the invalid-pin unit
        return [(fallback_panel, "/pB.png")]
    monkeypatch.setattr(shots, "_match_panels", _fake_match)

    narr = _narr([{"text": "A big fight", "page": 3, "panel": 0},     # valid → direct
                  {"text": "Frank wins", "page": 9, "panel": 5}])     # page 9 not in pool
    built = shots._build_shots_per_chunk(narr, _CHUNKS, _PAGES, _TIMINGS, project=None)

    out = capsys.readouterr().out
    assert "not in panel pool" in out
    assert len(built) == 2
    assert _bbox_w(built[0]) == 100     # pinned panel 0 kept
    assert _bbox_w(built[1]) == 800     # matcher fallback panel


# ── (a) dict beats survive Stage 3 onto the Narration ────────────────────────
def _beat(bid, page):
    return Beat(id=bid, function="SETUP", name=f"beat {bid}", summary=f"summary {bid}",
                page_refs=[page], characters_active=["Frank"])


_BEATS2 = [_beat(1, 3), _beat(2, 4)]
_TARGET2 = "the payoff on page 4"     # page hint → peak is beat 2; window = both beats


def _dict_beat_writer(*, system, user, models=None, max_tokens=1600, progress=None,
                      label="llm", validator=None):
    texts = ["Frank stalks the docks and waits in the dark for his moment tonight.",
             "Frank drives the spike home and the giant doubles over sick on the floor."]
    scenes = []
    for i, t in enumerate(texts):
        words = t.split()
        mid = len(words) // 2
        scenes.append({"text": t, "connective": None, "beat_id": i + 1, "visual_beats": [
            {"text": " ".join(words[:mid]), "page": _BEATS2[i].page_refs[0], "panel": 0},
            {"text": " ".join(words[mid:]), "page": _BEATS2[i].page_refs[0], "panel": 1},
        ]})
    raw = json.dumps({"hook": "The day Frank finally made the unstoppable giant sick.",
                      "ending_style": "thesis", "scenes": scenes})
    if validator is not None:
        assert validator(raw)
    return raw, "fake-micro-model"


def _raising_call(*a, **k):
    raise RuntimeError("no network in tests")


def test_writer_dict_beats_survive_to_narration(monkeypatch):
    monkeypatch.setattr(mm, "outline_beats", lambda *a, **k: (_BEATS2, "outline-model"))
    monkeypatch.setattr(mm, "call_with_chain", _dict_beat_writer)
    monkeypatch.setattr(ws, "call_with_chain", _raising_call)   # banner → title fallback

    comic_context = {"title": "Frank vs the Giant", "target_moment": _TARGET2,
                     "plot_summary": "Frank Castle fights a giant on the docks."}
    # NB: story_pages WITHOUT panels → the PANEL MENU is empty → pins are NOT validated/dropped,
    # so the writer's dict beats reach the Narration verbatim (the survival path under test).
    story_pages = [{"page_number": p, "is_story_page": True} for p in (3, 4)]

    nar = ws.write_script(comic_context, story_pages, "micro_moment", debug_dump={})

    assert nar.scenes[0].is_intro
    assert nar.scenes[0].visual_beats == []          # hook carries no beats
    body = nar.scenes[1:]
    assert len(body) == 2
    for s in body:
        assert len(s.visual_beats) == 2
        for vb in s.visual_beats:
            assert isinstance(vb, dict)              # dict, not stringified
            assert "text" in vb and "page" in vb and "panel" in vb
        # verbatim contract still holds on the beat TEXTS
        assert " ".join(vb["text"] for vb in s.visual_beats).split() == s.text.split()


# ── (e) the writer prompt carries a PANEL MENU ───────────────────────────────
def test_panel_menu_reaches_writer_prompt(monkeypatch):
    captured = {}

    def _capture(*, system, user, models=None, max_tokens=1600, progress=None,
                 label="llm", validator=None):
        captured["user"] = user
        # minimal valid response for _valid (hook + one scene per window beat)
        scenes = [{"text": "x", "visual_beats": [], "connective": None, "beat_id": b.id}
                  for b in _BEATS2]
        return json.dumps({"hook": "h", "ending_style": "thesis", "scenes": scenes}), "m"
    monkeypatch.setattr(mm, "call_with_chain", _capture)

    story_pages = [_page(3, 2), _page(4, 2)]         # real panels → menu is built
    mm._call_micro_writer(_BEATS2, {"title": "t", "plot_summary": "p"}, _TARGET2,
                          model=None, progress=None, debug_dump={}, story_pages=story_pages)
    assert "PANEL MENU" in captured["user"]
    assert "p3/0:" in captured["user"] and "p4/1:" in captured["user"]


if __name__ == "__main__":
    mp = pytest.MonkeyPatch()
    try:
        test_pinned_beats_bind_directly_and_skip_matcher(mp)
        mp.undo()
        test_recap_string_beats_route_every_unit_through_matcher(mp)
        print("ok")
    finally:
        mp.undo()
