"""Stage 3 beat grounding must match the FULL panel signal (description + characters +
dominant_emotion + dialog via panel_embed_text), not description alone — a panel's OCR
dialog often names the exact story moment (an unmasking line, a reveal) that the visual
description misses."""
import stages.stage_3.write_script as write_script
from stages.stage_3.schema import Beat


def _beat(bid, summary, page_refs):
    return Beat(id=bid, function="CLIMAX", name=f"b{bid}", page_refs=page_refs,
                key_panels=[], summary=summary, cause="", characters_active=[])


def _token_overlap_sim(a: str, b: str) -> float:
    aw = set(a.lower().split())
    bw = set(b.lower().split())
    return len(aw & bw) / max(1, len(aw))


def test_grounding_prefers_dialog_over_vague_description(monkeypatch):
    monkeypatch.setattr(write_script, "_semantic_sim", _token_overlap_sim)

    summary = "Doom unmasks himself before Reed in the ruined lab"
    decoy = {  # generically-similar DESCRIPTION, no dialog
        "description": "Reed stands in a ruined lab surrounded by broken machines",
        "characters": [], "dominant_emotion": "",
    }
    target = {  # vague description, but dialog carries the distinctive phrase
        "description": "A scarred face is shown close up in shadow",
        "characters": [], "dominant_emotion": "",
        "dialog": [{"text": "Doom unmasks himself before Reed"}],
    }
    story_pages = [{
        "page_number": 10,
        "panels": [decoy, target],
        "image_dimensions": {"width": 600, "height": 900},
        "text_blocks": [],
    }]
    beats = [_beat(1, summary, [10])]

    out = write_script._ground_beat_panels(beats, story_pages)

    assert out[0].key_panels == [{"page": 10, "panel": 1}]  # picks `target`, not `decoy`
