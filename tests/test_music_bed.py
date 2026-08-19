"""Music-brief orchestration: LLM Studio state plus stale-score protection."""
import json

import config
from stages import music_bed


def _project(tmp_path, slug="p", *, genre="minimal dark cinematic", text="Hulk survives impact"):
    root = tmp_path / slug
    root.mkdir()
    (root / "narration.json").write_text(json.dumps({
        "mode": "micro_moment", "title": "Hulk: Smash Everything",
        "scenes": [{"text": text}],
        "beats": [{"id": 1, "function": "COLD_OPEN", "summary": "Asteroid impact"}],
    }), encoding="utf-8")
    (root / "music.json").write_text(json.dumps({"genre": genre}), encoding="utf-8")
    return root


def _payload():
    return json.dumps({
        "genre": "epic hybrid orchestral",
        "minimax_state": {
            "mode": "studio", "description": "Primal cinematic score.",
            "instrumental": True, "title": "Hulk impact", "lyrics": "[intro]\n[instrumental]",
            "global_meta": "120 BPM, D minor.", "vocals": "Instrumental only. No vocals.",
            "arrangement": "Brass and percussion build into a cosmic finale.",
        },
        "reasoning": "The story escalates from destruction to cosmic danger.",
    })


def _fake_llm(monkeypatch, payload, seen=None):
    monkeypatch.setattr("stages.stage_3._llm._client", lambda: object())

    def _call(_client, model, system, user, timeout):
        if seen is not None:
            seen.update(model=model, system=system, user=user, timeout=timeout)
        return payload

    monkeypatch.setattr("stages.stage_3._llm._call_with_deadline", _call)


def test_brief_gives_llm_final_narration_beats_genre_and_video_duration(tmp_path, monkeypatch):
    monkeypatch.setattr(music_bed, "PROJECTS_ROOT", tmp_path)
    _project(tmp_path)
    seen = {}
    _fake_llm(monkeypatch, _payload(), seen)

    doc = music_bed.build_brief("p", 58.97, log=lambda _: None)

    assert "Hulk survives impact" in seen["user"]
    assert "Asteroid impact" in seen["user"]
    assert "minimal dark cinematic" in seen["user"]
    assert "58.97" in seen["user"]
    assert doc["minimax_state"]["arrangement"]
    assert doc["duration_seconds"] == 58.97


def test_brief_uses_review_beats_default_genre_when_no_project_setting_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(music_bed, "PROJECTS_ROOT", tmp_path)
    root = _project(tmp_path)
    (root / "music.json").unlink()
    seen = {}
    _fake_llm(monkeypatch, _payload(), seen)

    doc = music_bed.build_brief("p", 59.0, log=lambda _: None)

    assert config.MUSIC_GENRE in seen["user"]
    assert doc["genre"] == config.MUSIC_GENRE


def test_brief_identity_changes_with_narration_genre_beats_or_duration(tmp_path, monkeypatch):
    monkeypatch.setattr(music_bed, "PROJECTS_ROOT", tmp_path)
    root = _project(tmp_path)
    _fake_llm(monkeypatch, _payload())
    original = music_bed.build_brief("p", 59.0, log=lambda _: None)

    changed_duration = music_bed.build_brief("p", 60.0, log=lambda _: None)
    assert original["identity"] != changed_duration["identity"]

    narration = json.loads((root / "narration.json").read_text())
    narration["scenes"][0]["text"] = "Hulk reaches the black hole"
    (root / "narration.json").write_text(json.dumps(narration))
    changed_narration = music_bed.build_brief("p", 60.0, log=lambda _: None)
    assert changed_duration["identity"] != changed_narration["identity"]


def test_matching_final_inputs_reuse_the_existing_llm_brief(tmp_path, monkeypatch):
    monkeypatch.setattr(music_bed, "PROJECTS_ROOT", tmp_path)
    _project(tmp_path)
    _fake_llm(monkeypatch, _payload())
    first = music_bed.build_brief("p", 59.0, log=lambda _: None)

    monkeypatch.setattr("stages.stage_3._llm._call_with_deadline",
                        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("LLM called")))
    assert music_bed.build_brief("p", 59.0, log=lambda _: None) == first


def test_llm_failure_writes_no_replacement_brief(tmp_path, monkeypatch):
    monkeypatch.setattr(music_bed, "PROJECTS_ROOT", tmp_path)
    root = _project(tmp_path)
    _fake_llm(monkeypatch, "not json")

    assert music_bed.build_brief("p", 59.0, log=lambda _: None) is None
    assert json.loads((root / "music.json").read_text()) == {"genre": "minimal dark cinematic"}


def test_generate_bed_reuses_only_matching_brief_and_never_keeps_stale_output(tmp_path, monkeypatch):
    monkeypatch.setattr(music_bed, "PROJECTS_ROOT", tmp_path)
    root = _project(tmp_path)
    _fake_llm(monkeypatch, _payload())
    brief = music_bed.build_brief("p", 59.0, log=lambda _: None)
    out = root / "bgm.mp3"
    calls = []

    def _generate(state, seconds, target, *, log):
        calls.append((state, seconds, target))
        target.write_bytes(b"MP3")
        return target

    monkeypatch.setattr("stages.minimax_music.generate_music", _generate)
    assert music_bed.generate_bed("p", brief, log=lambda _: None) == out
    assert music_bed.generate_bed("p", brief, log=lambda _: None) == out
    assert len(calls) == 1

    stale = dict(brief, identity="different")
    monkeypatch.setattr("stages.minimax_music.generate_music", lambda *_a, **_k: None)
    assert music_bed.generate_bed("p", stale, log=lambda _: None) is None
    assert not out.exists()


def test_invalid_minimax_payload_is_rejected_without_hardcoded_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(music_bed, "PROJECTS_ROOT", tmp_path)
    _project(tmp_path)
    invalid = json.dumps({"genre": "x", "minimax_state": {"description": "x"}})
    _fake_llm(monkeypatch, invalid)

    assert music_bed.build_brief("p", 59.0, log=lambda _: None) is None
