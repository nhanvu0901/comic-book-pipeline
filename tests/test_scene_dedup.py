"""_to_narration collapses CONSECUTIVE near-identical scenes (the writer sometimes
stutters the same sentence 2-3× in a row) while keeping distinct scenes intact."""
from stages.stage_3.write_script import _to_narration
from stages.stage_3.schema import Glossary


def _mk(texts):
    parsed = {"title": "T", "scenes": [{"text": t, "page_ref": 1, "panel_ref": -1,
                                        "beat_id": i + 1} for i, t in enumerate(texts)]}
    return _to_narration(parsed, [], Glossary(characters=[]), "twist_reveal", "m")


def test_collapses_consecutive_duplicates():
    nar = _mk([
        "The amnesiac woke in the ruins of 2099.",
        "Doom vowed to kill any who dared claim his identity.",
        "Doom vowed to kill any who dared claim his identity.",
        "Doom vowed to kill any who dared claim his identity.",
        "Reed plummeted from the castle, his limbs stretching as he fell.",
    ])
    texts = [s.text for s in nar.scenes]
    assert texts.count("Doom vowed to kill any who dared claim his identity.") == 1, texts
    assert len(nar.scenes) == 3, texts
    assert [s.scene_id for s in nar.scenes] == [1, 2, 3], "scene_ids stay contiguous"


def test_keeps_distinct_scenes():
    nar = _mk(["Scene about the hammer.", "Scene about the castle.", "Scene about the fall."])
    assert len(nar.scenes) == 3


if __name__ == "__main__":
    test_collapses_consecutive_duplicates()
    test_keeps_distinct_scenes()
    print("ok")
