import json
import pytest

from art_pipeline.outline import (
    build_outline_from_raw, fact_is_grounded, _ROLE_SEQUENCES,
)

CTX = {
    "title": "View of Toledo",
    "plot_summary": (
        "El Greco painted View of Toledo around 1599. The painting shows the "
        "city under a stormy sky. He rearranged the buildings of the city, "
        "moving the cathedral to the left of the Alcazar palace. The work is "
        "one of only two surviving landscapes by El Greco. It entered the "
        "Metropolitan Museum of Art in 1929 with the Havemeyer bequest."
    ),
    "artworks": [{"object_id": 436575, "title": "View of Toledo"}],
    "mode": "painting_story",
}


def _chapter(cid, role, facts, words=300, ids=(436575,)):
    return {"chapter_id": cid, "title": f"ch{cid}", "role": role,
            "facts": facts, "target_words": words, "artwork_ids": list(ids)}


def _good_outline():
    f = ["El Greco painted View of Toledo around 1599",
         "the city under a stormy sky",
         "moving the cathedral to the left of the Alcazar palace",
         "one of only two surviving landscapes by El Greco",
         "entered the Metropolitan Museum of Art in 1929"]
    return {"mode": "painting_story",
            "through_line": "Why did El Greco lie about his own city?",
            "chapters": [
                _chapter(1, "cold_open", [f[0], f[1]]),
                _chapter(2, "backfill", [f[3], f[0] + "."], words=280),
                _chapter(3, "evidence", [f[2], f[1] + " over the city"], words=320),
                _chapter(4, "twist", [f[3] + " known", f[2] + "!"], words=300),
                _chapter(5, "resolution", [f[4], f[0] + " circa"], words=300),
            ]}


def test_fact_grounded_substring():
    assert fact_is_grounded("moving the cathedral to the left", CTX["plot_summary"])


def test_fact_grounded_token_overlap():
    # paraphrase keeping >=80% of its content tokens present in the context
    assert fact_is_grounded(
        "cathedral moved left of the Alcazar palace", CTX["plot_summary"])


def test_fact_not_grounded():
    assert not fact_is_grounded(
        "Napoleon stole the painting during the war", CTX["plot_summary"])


def test_role_sequences_match_config():
    from art_pipeline.config import ART_LF_CHAPTER_ROLES_4, ART_LF_CHAPTER_ROLES_5
    assert _ROLE_SEQUENCES == {4: ART_LF_CHAPTER_ROLES_4, 5: ART_LF_CHAPTER_ROLES_5}


def test_good_outline_passes():
    out = build_outline_from_raw(json.dumps(_good_outline()), CTX, "painting_story")
    assert len(out["chapters"]) == 5
    assert out["through_line"].endswith("?")


def test_bad_chapter_count():
    o = _good_outline(); o["chapters"] = o["chapters"][:3]
    with pytest.raises(ValueError, match="chapters"):
        build_outline_from_raw(json.dumps(o), CTX, "painting_story")


def test_bad_role_order():
    o = _good_outline()
    o["chapters"][1]["role"] = "twist"; o["chapters"][3]["role"] = "backfill"
    with pytest.raises(ValueError, match="role"):
        build_outline_from_raw(json.dumps(o), CTX, "painting_story")


def test_ungrounded_fact_rejected():
    o = _good_outline()
    o["chapters"][2]["facts"][0] = "aliens restored the canvas in 1850"
    with pytest.raises(ValueError, match="not grounded"):
        build_outline_from_raw(json.dumps(o), CTX, "painting_story")


def test_duplicate_fact_rejected():
    o = _good_outline()
    o["chapters"][3]["facts"][0] = o["chapters"][1]["facts"][0]
    with pytest.raises(ValueError, match="assigned twice"):
        build_outline_from_raw(json.dumps(o), CTX, "painting_story")


def test_total_words_out_of_band():
    o = _good_outline()
    for c in o["chapters"]:
        c["target_words"] = 160          # sum 800 < 1200
    with pytest.raises(ValueError, match="target_words"):
        build_outline_from_raw(json.dumps(o), CTX, "painting_story")


def test_journey_requires_question_through_line():
    o = _good_outline(); o["mode"] = "artist_journey"
    o["through_line"] = "The life of El Greco."
    with pytest.raises(ValueError, match="through_line"):
        build_outline_from_raw(json.dumps(o), CTX, "artist_journey")


def test_journey_artwork_coverage():
    ctx = dict(CTX)
    ctx["artworks"] = [{"object_id": 1, "title": "A"}, {"object_id": 2, "title": "B"}]
    o = _good_outline(); o["mode"] = "artist_journey"
    for c in o["chapters"]:
        c["artwork_ids"] = [1]           # artwork 2 never used
    with pytest.raises(ValueError, match="artwork"):
        build_outline_from_raw(json.dumps(o), ctx, "artist_journey")
