"""panel_embed_text (Stage-2 ingest text) + _panel_content_score (Stage-5 pure-vector
scorer). Both run offline — the scorer is fed explicit unit-vectors so cosine is exact."""
import numpy as np

from stages._panel_index import panel_embed_text
import stages.stage_5.shots as shots


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
