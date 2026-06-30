"""panel_embed_text (Stage-2 ingest text) + _panel_content_score (Stage-5 pure-vector
scorer). Both run offline — the scorer is fed explicit unit-vectors so cosine is exact."""
import numpy as np

from stages._panel_index import panel_embed_text, panel_dialog, page_dialog
import stages.stage_5.shots as shots


def test_embed_retry_fills_missing_on_second_attempt(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)
    from stages._panel_index import _embed_with_retry
    calls = {"n": 0}

    def fake(texts):
        calls["n"] += 1
        return [None, None] if calls["n"] == 1 else [[1.0], [2.0]]   # fail, then succeed

    assert _embed_with_retry(fake, ["a", "b"], tries=4) == [[1.0], [2.0]]
    assert calls["n"] == 2


def test_embed_retry_gives_up_after_tries(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)
    from stages._panel_index import _embed_with_retry
    calls = {"n": 0}

    def always_fail(texts):
        calls["n"] += 1
        return [None] * len(texts)

    assert _embed_with_retry(always_fail, ["a", "b"], tries=3) == [None, None]
    assert calls["n"] == 3   # initial + 2 retries


def test_panel_embed_text_reads_nested_dialog():
    panel = {"index": 0, "description": "Hulk smashes", "characters": ["Hulk"],
             "dominant_emotion": "rage",
             "dialog": [{"text": "PUNY GOD", "type": "speech"}]}
    # nested dialog wins; page_text_blocks not needed
    assert panel_embed_text(panel) == "Hulk smashes — Hulk — rage — PUNY GOD"


def test_panel_dialog_nested_vs_flat_fallback():
    nested = {"index": 1, "dialog": [{"text": "A"}, {"text": "B"}]}
    assert panel_dialog(nested) == [{"text": "A"}, {"text": "B"}]
    # old cached schema: no nested dialog → filter page-level text_blocks by panel_index
    old = {"index": 1}
    page_tb = [{"panel_index": 0, "text": "X"}, {"panel_index": 1, "text": "Y"}]
    assert panel_dialog(old, page_tb) == [{"panel_index": 1, "text": "Y"}]


def test_page_dialog_flattens_panels_or_uses_old_text_blocks():
    new_page = {"panels": [{"index": 0, "dialog": [{"text": "A"}]},
                           {"index": 1, "dialog": [{"text": "B"}]}]}
    assert [d["text"] for d in page_dialog(new_page)] == ["A", "B"]
    old_page = {"panels": [], "text_blocks": [{"text": "Z"}]}
    assert page_dialog(old_page) == [{"text": "Z"}]


def test_panel_embed_text_joins_desc_chars_emotion_dialog():
    panel = {"index": 0, "description": "Hulk smashes the rooftop",
             "characters": ["Hulk", "Loki"], "dominant_emotion": "rage"}
    tb = [{"panel_index": 0, "text": "PUNY GOD"}, {"panel_index": 1, "text": "elsewhere"}]
    assert panel_embed_text(panel, tb) == "Hulk smashes the rooftop — Hulk Loki — rage — PUNY GOD"


def test_panel_embed_text_falls_back_to_description_only():
    panel = {"index": 0, "description": "A quiet street", "characters": [], "dominant_emotion": ""}
    assert panel_embed_text(panel, []) == "A quiet street"


def _panel():
    return {"index": 0, "bbox": {"x": 0, "y": 0, "w": 600, "h": 900},
            "_page_area": 600 * 2700, "description": "x", "characters": []}


def test_content_score_is_pure_cosine_plus_render():
    cv = np.array([1.0, 0.0, 0.0], dtype="float32")
    pv = np.array([1.0, 0.0, 0.0], dtype="float32")     # cos(chunk,panel) = 1.0
    sv = np.array([0.0, 1.0, 0.0], dtype="float32")     # cos(scene,panel) = 0.0
    score, sim = shots._panel_content_score(_panel(), pv, cv, sv, [],
                                            chunk_text="x", scene_text="y")
    assert sim == 1.0
    # W_COS*1.0 + W_COS_SCENE*0.0 + render(salience only, ≈+1.0); render is small vs content
    assert score > shots.W_COS                          # content dominates
    assert score < shots.W_COS + 5.0


def test_low_cosine_falls_below_floor():
    cv = np.array([1.0, 0.0, 0.0], dtype="float32")
    pv = np.array([0.0, 1.0, 0.0], dtype="float32")     # orthogonal → cos 0
    _score, sim = shots._panel_content_score(_panel(), pv, cv, None, [],
                                             chunk_text="x", scene_text="y")
    assert sim < shots.PANEL_COS_FLOOR                  # → matcher HOLDS, no wrong panel
