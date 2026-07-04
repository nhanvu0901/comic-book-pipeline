"""Feature B — Magi OCR as dialog ground truth.

Part 1 (embedding): stages._panel_index.panel_embed_text prefers a panel's Magi `ocr`
over the VLM-fabricated `text`.
Part 2 (flag): stages.stage_2.pipeline._apply_dialog_truth_gate sets panel-level
`dialog_mismatch=True` when the VLM `text` does not match the Magi OCR.

Pure logic — no network, fake page dicts. The real failure this guards: doom-rocket-
raccoon p28 panel 1 pixels read "SO NOW WHAT DO WE DO?" but the VLM wrote "WE'VE REACHED
THE BIG BANG", poisoning the panel embedding + Stage-3/5 grounding.
"""
import stages._panel_index as pidx
from stages._panel_index import panel_embed_text
import stages.stage_2.pipeline as pipe
from stages.stage_2.pipeline import _apply_dialog_truth_gate


# ── Part 1: panel_embed_text prefers Magi OCR ────────────────────────────────

def test_embed_prefers_ocr_over_vlm_text():
    panel = {
        "index": 1, "description": "Two figures in a cosmic void",
        "characters": ["Doctor Doom", "Rocket Raccoon"], "dominant_emotion": "tense",
        "dialog": [
            # VLM fabricated `text`; Magi `ocr` is what the pixels actually say.
            {"text": "WE'VE REACHED THE BIG BANG.", "ocr": "SO NOW WHAT DO WE DO?"},
            {"text": "", "ocr": "WE WAIT. FOR REVELATION."},
        ],
    }
    out = panel_embed_text(panel)
    assert "SO NOW WHAT DO WE DO?" in out and "WE WAIT. FOR REVELATION." in out
    assert "BIG BANG" not in out            # the fabricated VLM text must NOT embed


def test_embed_falls_back_to_vlm_text_when_no_ocr():
    # Old cached page: dialog blocks carry no `ocr` key → behave exactly as before.
    panel = {"index": 0, "description": "Hulk smashes", "characters": ["Hulk"],
             "dominant_emotion": "rage", "dialog": [{"text": "PUNY GOD", "type": "speech"}]}
    assert panel_embed_text(panel) == "Hulk smashes — Hulk — rage — PUNY GOD"


def test_embed_ocr_preference_respects_kill_switch(monkeypatch):
    monkeypatch.setattr(pidx, "DIALOG_TRUTH", False)
    panel = {"index": 0, "description": "d", "characters": [], "dominant_emotion": "",
             "dialog": [{"text": "VLM LINE", "ocr": "OCR LINE"}]}
    # DIALOG_TRUTH off → embed uses the VLM `text`, not the OCR.
    assert panel_embed_text(panel) == "d — VLM LINE"


# ── Part 2: _apply_dialog_truth_gate flags fabricated dialog ─────────────────

def _story_page(dialog):
    return {"page_number": 28, "page_type": "story",
            "panels": [{"index": 1, "bbox": {"x": 0, "y": 0, "w": 10, "h": 10}, "dialog": dialog}]}


def test_gate_flags_fabricated_dialog():
    page = _story_page([
        {"text": "WE'VE REACHED THE BIG BANG... THE BIRTH OF THE UNIVERSE.",
         "ocr": "SO NOW WHAT DO WE DO?"},
        {"text": "", "ocr": "WE WAIT. FOR REVELATION. FOR AN ANSWER."},
    ])
    _apply_dialog_truth_gate(page, log=lambda *_a: None)
    assert page["panels"][0]["dialog_mismatch"] is True


def test_gate_does_not_flag_garbled_but_genuine_ocr():
    # OCR noise on the SAME line (ratio ~0.88) must not trip the gate.
    page = _story_page([{"text": "PUNY GOD", "ocr": "PUNY G0D"}])
    _apply_dialog_truth_gate(page, log=lambda *_a: None)
    assert "dialog_mismatch" not in page["panels"][0]


def test_gate_best_pair_rescues_when_one_line_matches():
    # A real transcription (one line matches OCR exactly) → best-pair 1.0 → not flagged,
    # even though a second line diverges. We only flag WHOLLY invented panels.
    page = _story_page([
        {"text": "NO.", "ocr": "NO."},
        {"text": "SOMETHING THE VLM ADDED", "ocr": "ENTROPY IS PLUS ONE"},
    ])
    _apply_dialog_truth_gate(page, log=lambda *_a: None)
    assert "dialog_mismatch" not in page["panels"][0]


def test_gate_skips_panel_with_no_ocr():
    # VLM-only panel (no Magi OCR) — nothing to cross-check → never flagged.
    page = _story_page([{"text": "SOME DIALOG", "ocr": ""}])
    _apply_dialog_truth_gate(page, log=lambda *_a: None)
    assert "dialog_mismatch" not in page["panels"][0]


def test_gate_no_flag_on_non_story_page():
    page = _story_page([{"text": "A", "ocr": "ZZZZZ"}])
    page["page_type"] = "skip"
    _apply_dialog_truth_gate(page, log=lambda *_a: None)
    assert "dialog_mismatch" not in page["panels"][0]


def test_gate_respects_kill_switch(monkeypatch):
    monkeypatch.setattr(pipe, "DIALOG_TRUTH", False)
    page = _story_page([{"text": "A", "ocr": "ZZZZZ"}])
    _apply_dialog_truth_gate(page, log=lambda *_a: None)
    assert "dialog_mismatch" not in page["panels"][0]
