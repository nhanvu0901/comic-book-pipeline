"""VLM_EXTRACT=0 → Magi-only mode (Master 2026-07-24).

The per-page OpenRouter describe pass is disabled by default. The SURVIVAL condition
is bubble-delete: panel['dialog'] must still carry bbox + text (from Magi OCR) so
Stage 5's inpaint mask can erase on-art speech. These tests prove:
  1. Magi-only: panel assembly builds dialog (bbox+text), panel bbox, and cluster_ids
     from Magi ALONE — and NEVER calls the VLM (patched to raise if touched).
  2. VLM on (VLM_EXTRACT=1): extract_page is still called (old behaviour), and the
     Magi-sourced dialog bbox is preserved alongside the VLM description.
"""
from pathlib import Path

import stages.stage_2.pipeline as pipeline


def _magi() -> dict:
    """A realistic Magi detect_full() dict: 2 panels side-by-side, one speech box inside
    each (with bbox + pixel OCR), one character cluster inside panel 0. Geometry is chosen
    so the real assign_to_panels() places text 0 + the char in panel 0, text 1 in panel 1."""
    return {
        "panels": [
            {"bbox": {"x": 0, "y": 0, "w": 500, "h": 500}},
            {"bbox": {"x": 500, "y": 0, "w": 500, "h": 500}},
        ],
        "characters": [
            {"bbox": {"x": 100, "y": 100, "w": 80, "h": 200}, "cluster_id": 0},
        ],
        "texts": [
            {"bbox": {"x": 50, "y": 50, "w": 120, "h": 40}, "ocr": "WE HAVE TO RUN!", "type": "speech"},
            {"bbox": {"x": 560, "y": 60, "w": 120, "h": 40}, "ocr": "NO ESCAPE.", "type": "speech"},
        ],
    }


def _boom(*_a, **_k):
    raise AssertionError("VLM extraction called in Magi-only mode (VLM_EXTRACT=0)")


def _build(tmp_path, magi):
    return pipeline._build_page_from_single(
        page_number=5, issue_label="#1", image_path=Path("/nonexistent.jpg"),
        panels_raw=magi["panels"], dimensions=(1000, 500), project_root=tmp_path,
        log=lambda *_a, **_k: None, story_context="", content_hash="hash5", magi_data=magi,
    )


def test_magi_only_builds_dialog_bbox_without_vlm(tmp_path, monkeypatch):
    """Core bubble-preservation test: VLM off, VLM funcs patched to RAISE if called."""
    monkeypatch.setattr(pipeline, "VLM_EXTRACT", False)
    monkeypatch.setattr(pipeline, "extract_page", _boom)
    monkeypatch.setattr(pipeline, "extract_pages_batch", _boom)

    out = _build(tmp_path, _magi())  # raises via _boom if any VLM path is hit

    assert out["page_type"] == "story" and out["is_story_page"] is True
    panels = out["panels"]
    assert len(panels) == 2

    # Panel bbox survives.
    assert panels[0]["bbox"] == {"x": 0, "y": 0, "w": 500, "h": 500}

    # Dialog: bbox + text (from Magi OCR) — the Stage 5 inpaint mask lives on this.
    d0 = panels[0]["dialog"]
    assert d0 and d0[0]["text"] == "WE HAVE TO RUN!"
    assert d0[0]["ocr"] == "WE HAVE TO RUN!"
    assert d0[0]["bbox"] == {"x": 50, "y": 50, "w": 120, "h": 40}
    assert panels[1]["dialog"][0]["text"] == "NO ESCAPE."
    assert panels[1]["dialog"][0]["bbox"]["w"] == 120

    # Character cluster_ids come from Magi (names filled later by cluster_namer).
    assert panels[0]["cluster_ids"] == [0]

    # Description is synthesized from the OCR dialog (last-resort fill), never a VLM call.
    assert "WE HAVE TO RUN!" in panels[0]["description"]


def test_vlm_on_still_calls_extract_page(tmp_path, monkeypatch):
    """VLM_EXTRACT=1 → old behaviour: extract_page IS called, and the Magi dialog bbox is
    still merged in alongside the VLM description."""
    monkeypatch.setattr(pipeline, "VLM_EXTRACT", True)
    called = {}

    def fake_extract_page(image_path, panels, progress=None, story_context=""):
        called["yes"] = True
        return {
            "page_type": "story",
            "panels": [{"index": 0, "description": "A hero flees down the alley",
                        "characters": ["Hero"], "dominant_emotion": "tense"}],
            "text_blocks": [],  # no VLM transcription → Magi OCR becomes the dialog
            "page_summary": "The hero runs.",
            "_vlm_model_used": "fake-model",
        }

    monkeypatch.setattr(pipeline, "extract_page", fake_extract_page)

    out = _build(tmp_path, _magi())

    assert called.get("yes") is True
    assert out["panels"][0]["description"] == "A hero flees down the alley"
    # Magi dialog bbox preserved even with VLM on.
    assert out["panels"][0]["dialog"][0]["bbox"] == {"x": 50, "y": 50, "w": 120, "h": 40}
