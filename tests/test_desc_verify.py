"""DESC_VERIFY gate (2026-07-02): a panel description is sometimes attached to the
WRONG bbox (batch shift) or hallucinated outright, and nothing previously checked the
description against the actual pixels. verify_page_descriptions crops the bbox + asks
the VLM to look — this file tests the pure parsing/selection/decision logic with no
network calls, plus one wiring test with the VLM call mocked at the client boundary."""
from unittest.mock import patch

from stages.stage_2.vlm_extract import (
    _parse_verify_verdicts,
    _select_verify_panels,
    verify_page_descriptions,
)


# ─── _parse_verify_verdicts (pure parsing, keyed by ACTUAL panel index) ────────

def test_verdicts_all_match():
    raw = '[{"index": 0, "match": true}, {"index": 1, "match": true}]'
    verdicts = _parse_verify_verdicts(raw, sampled_indexes=[3, 7])
    assert verdicts == {3: True, 7: True}
    assert all(verdicts.values())


def test_verdicts_one_mismatch():
    raw = '[{"index": 0, "match": true}, {"index": 1, "match": false, "why": "wrong panel"}]'
    verdicts = _parse_verify_verdicts(raw, sampled_indexes=[3, 7])
    assert verdicts == {3: True, 7: False}
    assert not all(verdicts.values())


def test_verdicts_tolerant_of_markdown_fences():
    raw = '```json\n[{"index": 0, "match": true}]\n```'
    verdicts = _parse_verify_verdicts(raw, sampled_indexes=[5])
    assert verdicts == {5: True}


def test_verdicts_garbage_input_fails_closed():
    verdicts = _parse_verify_verdicts("not json at all", sampled_indexes=[0, 1])
    assert verdicts == {0: False, 1: False}


def test_verdicts_missing_crop_defaults_false():
    # Model only answered for crop 0; crop 1 never got a verdict — must not be assumed True.
    raw = '[{"index": 0, "match": true}]'
    verdicts = _parse_verify_verdicts(raw, sampled_indexes=[4, 9])
    assert verdicts == {4: True, 9: False}


# ─── _select_verify_panels (largest-N with a description) ─────────────────────

def test_select_picks_largest_first():
    panels = [
        {"index": 0, "description": "small", "bbox": {"w": 10, "h": 10}},
        {"index": 1, "description": "big", "bbox": {"w": 500, "h": 500}},
        {"index": 2, "description": "medium", "bbox": {"w": 100, "h": 100}},
    ]
    sampled = _select_verify_panels(panels, k=2)
    assert [p["index"] for p in sampled] == [1, 2]


def test_select_skips_empty_description_and_degenerate_bbox():
    panels = [
        {"index": 0, "description": "", "bbox": {"w": 999, "h": 999}},        # blank desc
        {"index": 1, "description": "ok", "bbox": {"w": 0, "h": 50}},         # degenerate
        {"index": 2, "description": "kept", "bbox": {"w": 30, "h": 30}},
    ]
    sampled = _select_verify_panels(panels)
    assert [p["index"] for p in sampled] == [2]


def test_select_caps_at_k():
    panels = [
        {"index": i, "description": "x", "bbox": {"w": 10 + i, "h": 10}}
        for i in range(5)
    ]
    assert len(_select_verify_panels(panels, k=3)) == 3


def test_select_default_covers_full_page():
    # Doom & Rocket p28 regression: 6-panel page, the poisoned description sat on the
    # SMALLEST panel — a largest-first sample of 3 never checked it and the gate passed.
    # The default must cover every described panel of a normal page (one VLM call either way).
    panels = [
        {"index": i, "description": f"panel {i}", "bbox": {"w": 100 + i, "h": 100}}
        for i in range(6)
    ]
    sampled = _select_verify_panels(panels)
    assert len(sampled) == 6
    assert {p["index"] for p in sampled} == set(range(6))


# ─── verify_page_descriptions wiring (VLM call mocked at the client boundary) ──

def _fake_page(tmp_path):
    return {
        "page_number": 12,
        "panels": [
            {"index": 0, "description": "Reed Richards raises a sonic gun.",
             "bbox": {"x": 0, "y": 0, "w": 200, "h": 200}},
        ],
    }


def test_verify_passes_when_vlm_confirms_match(tmp_path):
    from PIL import Image
    img_path = tmp_path / "page.jpg"
    Image.new("RGB", (400, 400), "white").save(img_path)

    fake_resp = type("R", (), {"choices": [type("C", (), {
        "message": type("M", (), {"content": '[{"index": 0, "match": true}]'})()
    })()]})()

    with patch("stages.stage_2.vlm_extract.OPENROUTER_API_KEY", "dummy-key"), \
         patch("stages.stage_2.vlm_extract._client") as mock_client:
        mock_client.return_value.chat.completions.create.return_value = fake_resp
        ok = verify_page_descriptions(_fake_page(tmp_path), img_path, log=lambda _m: None)
    assert ok is True


def test_verify_fails_and_logs_when_vlm_flags_mismatch(tmp_path):
    from PIL import Image
    img_path = tmp_path / "page.jpg"
    Image.new("RGB", (400, 400), "white").save(img_path)

    fake_resp = type("R", (), {"choices": [type("C", (), {
        "message": type("M", (), {
            "content": '[{"index": 0, "match": false, "why": "shows Doom, not Reed"}]'
        })()
    })()]})()

    logged = []
    with patch("stages.stage_2.vlm_extract.OPENROUTER_API_KEY", "dummy-key"), \
         patch("stages.stage_2.vlm_extract._client") as mock_client:
        mock_client.return_value.chat.completions.create.return_value = fake_resp
        ok = verify_page_descriptions(_fake_page(tmp_path), img_path, log=logged.append)
    assert ok is False
    assert any("MISMATCH" in m and "shows Doom" in m for m in logged)


def test_verify_skips_gracefully_without_api_key(tmp_path):
    from PIL import Image
    img_path = tmp_path / "page.jpg"
    Image.new("RGB", (400, 400), "white").save(img_path)

    with patch("stages.stage_2.vlm_extract.OPENROUTER_API_KEY", ""):
        ok = verify_page_descriptions(_fake_page(tmp_path), img_path, log=lambda _m: None)
    assert ok is True  # soft gate — no key means "can't check", not "fail"


def test_verify_no_panels_to_check_is_trivially_true():
    page = {"page_number": 1, "panels": [{"index": 0, "description": "", "bbox": {"w": 100, "h": 100}}]}
    assert verify_page_descriptions(page, "/nonexistent.jpg", log=lambda _m: None) is True
