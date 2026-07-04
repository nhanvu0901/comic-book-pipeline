"""Stage 3 director spec: projects/<name>/direction.json is optional human input
(POV, naming policy, reveal placement, must-have beats) that gets rendered into a
BINDING prompt block and prepended to every phase's user message."""
import json

from stages.stage_3 import write_script as ws
from stages.stage_3.write_script import _direction_block, _load_direction


def test_load_direction_missing_project_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "PROJECTS_ROOT", tmp_path)
    assert _load_direction("no-such-project") == {}


def test_load_direction_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "PROJECTS_ROOT", tmp_path)
    (tmp_path / "doom-2099").mkdir()
    assert _load_direction("doom-2099") == {}


def test_load_direction_bad_json_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "PROJECTS_ROOT", tmp_path)
    proj = tmp_path / "doom-2099"
    proj.mkdir()
    (proj / "direction.json").write_text("{not json")
    assert _load_direction("doom-2099") == {}


def test_load_direction_valid_file_returns_dict(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "PROJECTS_ROOT", tmp_path)
    proj = tmp_path / "doom-2099"
    proj.mkdir()
    data = {"pov": "he believes he is Doom", "naming": ["call him Doom from line 1"]}
    (proj / "direction.json").write_text(json.dumps(data))
    assert _load_direction("doom-2099") == data


def test_direction_block_empty_when_no_direction():
    assert _direction_block({}) == ""
    assert _direction_block(None) == ""


def test_direction_block_contains_binding_header_and_fields():
    direction = {
        "pov": "tell it from the amnesiac's belief he IS Doom",
        "naming": ["call him Doom from the start", "never name the cult"],
        "reveal": "hold the flip to scene 14",
        "must_have_beats": ["the enforcer hunts him and he strikes her down"],
        "notes": "mirrors Master's hand-fixed narration",
    }
    block = _direction_block(direction)
    assert "DIRECTOR'S SPEC (HUMAN — BINDING)" in block
    assert "OVERRIDE" in block
    assert direction["pov"] in block
    assert "call him Doom from the start" in block
    assert direction["reveal"] in block
    assert "the enforcer hunts him and he strikes her down" in block
    assert direction["notes"] in block


def test_direction_block_prepended_in_outline_prompt(monkeypatch):
    """The block must land near the top of the Phase-A (outline) user message."""
    direction = {"pov": "UNIQUE_POV_MARKER"}
    captured = {}

    def fake_call_with_chain(*, system, user, **kw):
        captured["user"] = user
        return json.dumps({"beats": [{
            "id": 1, "function": "COLD_OPEN", "name": "n", "page_refs": [1],
            "key_panels": [], "summary": "s", "cause": "", "characters_active": [],
        }]}), "fake-model"

    monkeypatch.setattr(ws, "call_with_chain", fake_call_with_chain)
    monkeypatch.setattr(ws, "_validate_outline", lambda beats, max_gap=5: [])
    monkeypatch.setattr(ws, "_ground_beat_panels", lambda beats, pages, progress=None: beats)

    ctx = {"title": "T", "plot_summary": "A hero fights a villain."}
    pages = [{"page_number": 1, "panels": []}]
    ws.outline_beats(ctx, pages, "recap_summary", direction=direction)

    user = captured["user"]
    assert "UNIQUE_POV_MARKER" in user
    assert user.index("UNIQUE_POV_MARKER") < user.index("TASK:"), \
        "director block must appear near the top, before the task instructions"


def test_direction_block_prepended_in_writer_prompt(monkeypatch):
    """The block must land near the top of the Phase-C (writer) user message."""
    direction = {"pov": "UNIQUE_POV_MARKER_2"}
    captured = {}

    def fake_call_with_chain(*, system, user, **kw):
        captured["user"] = user
        return json.dumps({"scenes": [{"text": "x", "connective": None, "beat_id": 1}]}), "fake-model"

    monkeypatch.setattr(ws, "call_with_chain", fake_call_with_chain)

    from stages.stage_3.schema import Beat, Glossary
    beats = [Beat(id=1, function="COLD_OPEN", name="n", page_refs=[1], key_panels=[],
                  summary="s", cause="", characters_active=[])]
    glossary = Glossary(characters={})
    ctx = {"title": "T", "plot_summary": "A hero fights a villain."}
    pages = [{"page_number": 1, "panels": []}]
    ws.write_scenes(beats, glossary, ctx, pages, "recap_summary", direction=direction)

    user = captured["user"]
    assert "UNIQUE_POV_MARKER_2" in user
    assert user.index("UNIQUE_POV_MARKER_2") < user.index("BEATS —"), \
        "director block must appear near the top, before the beats block"


if __name__ == "__main__":
    test_load_direction_missing_project_returns_empty
    test_direction_block_empty_when_no_direction()
    test_direction_block_contains_binding_header_and_fields()
    print("ok (run via pytest for the fixture-based tests)")
