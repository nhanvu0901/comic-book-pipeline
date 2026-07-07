"""Pre-VLM gate (stage 2): covers/ads classified from Magi output alone, no VLM call.

Rule A: first page of its own issue → cover (unless it looks like a cold-open).
Rule B: 0 panels + 0 characters + 0 speech balloons → non-story (ad/text page).
"""
from stages.stage_2.pipeline import _prevlm_gate, _prevlm_page

BOUNDS = {"#1": (1, 30), "#2": (31, 60)}


def _magi(panels=0, chars=0, texts=()):
    return {
        "panels": [{"bbox": {}}] * panels,
        "characters": [{"bbox": {}}] * chars,
        "texts": list(texts),
    }


# ── Rule A: cover ────────────────────────────────────────────────────────────

def test_first_page_one_panel_is_cover():
    assert _prevlm_gate(_magi(panels=1), 1, "#1", BOUNDS) == ("cover", "", "Cover page")


def test_first_page_of_second_issue_is_cover():
    assert _prevlm_gate(_magi(panels=1, chars=2), 31, "#2", BOUNDS)[0] == "cover"


def test_first_page_with_speech_falls_through():
    # Cold open: cover missing from the scan — must go to the VLM, not be eaten.
    speech = [{"type": "speech", "ocr": "WE HAVE TO RUN!"}]
    assert _prevlm_gate(_magi(panels=1, texts=speech), 1, "#1", BOUNDS) is None


def test_first_page_multi_panel_falls_through():
    assert _prevlm_gate(_magi(panels=3), 1, "#1", BOUNDS) is None


# ── Rule B: non-story ────────────────────────────────────────────────────────

def test_blank_no_content_page_skips():
    verdict = _prevlm_gate(_magi(), 12, "#1", BOUNDS)
    assert verdict is not None and verdict[0] == "skip"


def test_ad_ocr_classified_advertisement():
    ads = [{"type": "caption", "ocr": "ON SALE NOW! Subscribe at marvel.com"}]
    verdict = _prevlm_gate(_magi(texts=ads), 12, "#1", BOUNDS)
    assert verdict is not None and verdict[1] in ("advertisement", "back_matter")


def test_whole_page_render_with_characters_falls_through():
    # 0 panels but Magi sees characters → real story art, VLM must run.
    assert _prevlm_gate(_magi(chars=1), 12, "#1", BOUNDS) is None


def test_speech_without_panels_falls_through():
    speech = [{"type": "speech", "ocr": "..."}]
    assert _prevlm_gate(_magi(texts=speech), 12, "#1", BOUNDS) is None


def test_normal_story_page_falls_through():
    assert _prevlm_gate(_magi(panels=5, chars=3), 12, "#1", BOUNDS) is None


# ── Record shape ─────────────────────────────────────────────────────────────

def test_prevlm_page_record(tmp_path):
    img = tmp_path / "p.jpg"
    img.write_bytes(b"x")
    d = _prevlm_page(
        page_number=1, issue_label="#1", image_path=img, dimensions=(100, 200),
        content_hash="abc", page_type="cover", skip_reason="", page_summary="Cover page",
    )
    assert d["is_story_page"] is False
    assert d["page_type"] == "cover"
    assert d["panels"] == []
    assert d["preprocessing_method"] == "heuristic_skip"
    assert d["image_dimensions"] == {"width": 100, "height": 200}
