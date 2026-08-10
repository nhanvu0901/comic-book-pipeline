"""The ad / back-matter guard must actually receive the page's text — and must lose to
real story content (2026-08-10).

Two defects, one at each end:

1. Magi keys its own text entries as "ocr" (panel_detect._parse_magi_page), but the
   guard read "text", so the corpus was ALWAYS empty and both _looks_like_ad and
   _looks_like_backmatter returned False on their `if not low` line. With the VLM off —
   the normal setup for Q&A — nothing else contributed text either, so the filter had
   never run once. Measured after the fix: 10 real ad pages across the projects on disk.

2. Naively fixing that key deletes real pages: a story page's last panel routinely
   carries a "TO BE CONTINUED" caption, which _looks_like_backmatter matches regardless
   of everything else on the page. Measured: cap-shield-broken p25 is the Fear Itself
   climax (3 panels of dialogue) and matched.
"""
from pathlib import Path

import pytest

from stages.stage_2.pipeline import _assemble_page_dict


def _magi(texts, n_panels):
    """Magi's in-memory shape — note the "ocr" key, which is the whole point."""
    return {
        "panels": [{"bbox": {"x": 0, "y": i * 100, "w": 600, "h": 90}, "confidence": 1.0}
                   for i in range(n_panels)],
        "characters": [],
        "texts": [{"bbox": {"x": 10, "y": 10, "w": 100, "h": 20}, "ocr": t,
                   "type": "speech", "speaker_char_idx": None,
                   "speaker_cluster_id": None, "is_essential": True} for t in texts],
    }


def _assemble(texts, n_panels, tmp_path):
    magi = _magi(texts, n_panels)
    img = tmp_path / "p.jpg"
    img.write_bytes(b"x")
    return _assemble_page_dict(
        page_number=7, issue_label="t", image_path=img,
        panels_raw=magi["panels"], dimensions=(1000, 1500),
        vlm_data={},                      # VLM off — the Q&A default
        content_hash="h", vlm_model_used="", magi_data=magi,
    )


# ── defect 1: the corpus must reach the guard ────────────────────────────────

def test_ad_page_is_skipped(tmp_path):
    page = _assemble(["ON SALE NOW!", "Visit marvel.com to subscribe"], 1, tmp_path)
    assert page["is_story_page"] is False
    assert page["skip_reason"] == "advertisement"


def test_ad_detection_reads_magi_ocr_key_not_text(tmp_path):
    """The regression itself: same words under the key Magi actually writes."""
    magi = _magi(["ON SALE NOW!", "Visit marvel.com to subscribe"], 1)
    assert all("text" not in t for t in magi["texts"]), "Magi writes 'ocr', never 'text'"
    page = _assemble(["ON SALE NOW!", "Visit marvel.com to subscribe"], 1, tmp_path)
    assert page["skip_reason"] == "advertisement"


def test_credits_backmatter_page_is_skipped(tmp_path):
    page = _assemble(["Created by Stan Lee", "to be continued"], 1, tmp_path)
    assert page["is_story_page"] is False
    assert page["skip_reason"] == "back_matter"


# ── defect 2: real story content wins ────────────────────────────────────────

def test_climax_page_ending_on_to_be_continued_survives(tmp_path):
    """cap-shield-broken p25 in miniature: 3 panels of dialogue plus the TBC caption."""
    page = _assemble(
        ["Stand down?!", "Cap, how could-- why?", "I told him he could stand down.",
         "To be continued."],
        3, tmp_path,
    )
    assert page["is_story_page"] is True, "a multi-panel dialogue page is not back-matter"
    assert page["page_type"] == "story"


def test_single_panel_teaser_still_skips(tmp_path):
    """One panel and nothing but the teaser = the real back-matter shape."""
    page = _assemble(["To be continued."], 1, tmp_path)
    assert page["is_story_page"] is False
    assert page["skip_reason"] == "back_matter"


def test_story_signal_does_not_rescue_an_advertisement(tmp_path):
    """Ads have art and panels too — sales copy is specific enough to act on alone."""
    page = _assemble(["DON'T MISS THE PRESTIGE ONE-SHOT", "ON SALE in October"], 4, tmp_path)
    assert page["is_story_page"] is False
    assert page["skip_reason"] == "advertisement"


# ── ordinary pages are untouched ─────────────────────────────────────────────

def test_plain_story_page_unaffected(tmp_path):
    page = _assemble(["Where do you think you're going?", "Away from you."], 3, tmp_path)
    assert page["is_story_page"] is True
    assert page["skip_reason"] == ""


def test_wordless_page_unaffected(tmp_path):
    page = _assemble([], 4, tmp_path)
    assert page["is_story_page"] is True
