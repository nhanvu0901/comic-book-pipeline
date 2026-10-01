"""Feature B — Magi OCR as dialog ground truth.

Part 1 (review tile): stages.review_gate._panel_dialog_str, the dialog string the review UI shows
under each candidate panel, prefers a panel's Magi `ocr` over the VLM-fabricated `text`.
Part 2 (flag): stages.stage_2.pipeline._apply_dialog_truth_gate sets panel-level
`dialog_mismatch=True` when the VLM `text` does not match the Magi OCR.

Pure logic — no network, fake page dicts. The real failure this guards: doom-rocket-
raccoon p28 panel 1 pixels read "SO NOW WHAT DO WE DO?" but the VLM wrote "WE'VE REACHED
THE BIG BANG", which would put the invented line on the tile Master picks panels from.
"""
from stages.review_gate import _panel_dialog_str
import stages.stage_2.pipeline as pipe
from stages.stage_2.pipeline import _apply_dialog_truth_gate


# ── Part 1: the review tile's dialog string prefers Magi OCR ──────────────────

def test_dialog_prefers_ocr_over_vlm_text():
    panel = {
        "index": 1, "description": "Two figures in a cosmic void",
        "characters": ["Doctor Doom", "Rocket Raccoon"], "dominant_emotion": "tense",
        "dialog": [
            # VLM fabricated `text`; Magi `ocr` is what the pixels actually say.
            {"text": "WE'VE REACHED THE BIG BANG.", "ocr": "SO NOW WHAT DO WE DO?"},
            {"text": "", "ocr": "WE WAIT. FOR REVELATION."},
        ],
    }
    out = _panel_dialog_str(panel, None)
    assert out == "SO NOW WHAT DO WE DO? WE WAIT. FOR REVELATION."
    assert "BIG BANG" not in out            # the fabricated VLM text must NOT be shown


def test_dialog_falls_back_to_vlm_text_when_no_ocr():
    # Old cached page: dialog blocks carry no `ocr` key → behave exactly as before.
    panel = {"index": 0, "description": "Hulk smashes", "characters": ["Hulk"],
             "dominant_emotion": "rage", "dialog": [{"text": "PUNY GOD", "type": "speech"}]}
    assert _panel_dialog_str(panel, None) == "PUNY GOD"


def test_dialog_reads_the_old_flat_text_blocks_too():
    # Old schema: no nested dialog, the page-level text_blocks point at the panel by index.
    panel = {"index": 2}
    page_tb = [{"panel_index": 2, "text": "VLM LINE", "ocr": "OCR LINE"},
               {"panel_index": 3, "text": "elsewhere", "ocr": "ELSEWHERE"}]
    assert _panel_dialog_str(panel, page_tb) == "OCR LINE"


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
