"""Guards for stages/music_bed.py — the LLM brief + ACE-Step bed.

The contract that matters most: EVERY failure path produces no bed and no exception. A
project with no music renders narration-only, which is what every project shipped before
this existed does, so a broken generator must never be able to fail a render.
"""
import json

import pytest

from stages import music_bed


def _project(tmp_path, slug, *, scenes=None, est=50.0):
    root = tmp_path / slug
    root.mkdir(parents=True)
    (root / "narration.json").write_text(json.dumps({
        "mode": "explore_answer", "title": "T",
        "estimated_duration_seconds": est,
        "scenes": scenes or [{"text": "first line"}, {"text": "second line"}],
    }), encoding="utf-8")
    return root


# ─── interpreter probe (same layout question as the chatterbox venv) ─────────

def test_venv_python_probes_both_layouts(tmp_path):
    posix = tmp_path / "a"
    (posix / "bin").mkdir(parents=True)
    (posix / "bin" / "python").write_text("")
    assert music_bed._venv_python(posix) == posix / "bin" / "python"

    win = tmp_path / "b"
    (win / "Scripts").mkdir(parents=True)
    (win / "Scripts" / "python.exe").write_text("")
    assert music_bed._venv_python(win) == win / "Scripts" / "python.exe"

    missing = tmp_path / "c"
    assert music_bed._venv_python(missing) == missing / "bin" / "python"


# ─── length ─────────────────────────────────────────────────────────────────

def test_length_falls_back_to_the_stage3_estimate_before_tts(tmp_path):
    """Before Stage 4 there is no audio.wav, so the estimate is all there is."""
    root = _project(tmp_path, "p", est=44.0)
    assert music_bed.audio_seconds(root) == 44.0


def test_length_is_zero_when_the_project_is_empty(tmp_path):
    assert music_bed.audio_seconds(tmp_path / "nothing") == 0.0


# ─── brief ──────────────────────────────────────────────────────────────────

def _fake_llm(monkeypatch, payload):
    monkeypatch.setattr("stages.stage_3._llm._client", lambda: object())
    monkeypatch.setattr("stages.stage_3._llm._call_with_deadline",
                        lambda *a, **k: payload)


def test_brief_writes_music_json(tmp_path, monkeypatch):
    monkeypatch.setattr(music_bed, "PROJECTS_ROOT", tmp_path)
    _project(tmp_path, "p")
    _fake_llm(monkeypatch, json.dumps({
        "genre": "dark trap", "bpm": 140,
        "prompt": "dark trap, 808 sub bass, brooding, instrumental, 140 bpm",
        "reasoning": "because"}))

    doc = music_bed.build_brief("p", log=lambda _m: None)
    assert doc["genre"] == "dark trap" and doc["bpm"] == 140
    on_disk = json.loads((tmp_path / "p" / "music.json").read_text())
    assert on_disk["prompt"].endswith("140 bpm")


def test_a_missing_instrumental_tag_is_repaired(tmp_path, monkeypatch):
    """Without it the generator sings, and a vocal bed under a voice-over is unusable —
    a silent failure that only shows up after 2.5 minutes of generation."""
    monkeypatch.setattr(music_bed, "PROJECTS_ROOT", tmp_path)
    _project(tmp_path, "p")
    _fake_llm(monkeypatch, json.dumps({"genre": "g", "bpm": 90,
                                       "prompt": "epic orchestral, 90 bpm"}))
    doc = music_bed.build_brief("p", log=lambda _m: None)
    assert "instrumental" in doc["prompt"]


def test_a_genre_chosen_in_the_ui_is_binding(tmp_path, monkeypatch):
    """The Review Beats dropdown is an instruction, so the steer must reach the model."""
    monkeypatch.setattr(music_bed, "PROJECTS_ROOT", tmp_path)
    root = _project(tmp_path, "p")
    (root / "music.json").write_text(json.dumps({"genre": "dark ambient drone"}))
    seen = {}

    monkeypatch.setattr("stages.stage_3._llm._client", lambda: object())

    def _capture(_c, _model, _system, user, _max):
        seen["user"] = user
        return json.dumps({"genre": "dark ambient drone", "bpm": 70,
                           "prompt": "dark ambient drone, instrumental, 70 bpm"})

    monkeypatch.setattr("stages.stage_3._llm._call_with_deadline", _capture)
    music_bed.build_brief("p", log=lambda _m: None)
    assert "REQUIRED GENRE: dark ambient drone" in seen["user"]


def test_llm_failure_yields_no_brief_and_no_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(music_bed, "PROJECTS_ROOT", tmp_path)
    _project(tmp_path, "p")
    monkeypatch.setattr("stages.stage_3._llm._client", lambda: object())

    def _boom(*_a, **_k):
        raise RuntimeError("rate limited")

    monkeypatch.setattr("stages.stage_3._llm._call_with_deadline", _boom)
    assert music_bed.build_brief("p", log=lambda _m: None) is None
    assert not (tmp_path / "p" / "music.json").exists()


def test_unparseable_llm_output_yields_no_brief(tmp_path, monkeypatch):
    monkeypatch.setattr(music_bed, "PROJECTS_ROOT", tmp_path)
    _project(tmp_path, "p")
    _fake_llm(monkeypatch, "I'm afraid I can't do that")
    assert music_bed.build_brief("p", log=lambda _m: None) is None


# ─── bed ────────────────────────────────────────────────────────────────────

def test_generate_without_a_brief_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.setattr(music_bed, "PROJECTS_ROOT", tmp_path)
    _project(tmp_path, "p")
    assert music_bed.generate_bed("p", log=lambda _m: None) is None


def _stamp_for(prompt: str, genre: str = "") -> str:
    """Mirror of the stamp generate_bed writes. GENRE is in it as well as the prompt: the
    review UI writes only `genre` and leaves the old `prompt`, so a prompt-only hash never
    noticed a genre change."""
    import hashlib
    return hashlib.sha1(f"{genre}|{prompt}|{music_bed.ACESTEP_STEPS}|"
                        f"{music_bed.ACESTEP_GUIDANCE}|{music_bed.ACESTEP_SEED}"
                        .encode("utf-8")).hexdigest()


def test_generate_reuses_a_bed_built_from_the_same_brief(tmp_path, monkeypatch):
    """Reuse is keyed on the brief, not on the file existing — see the genre-change
    regression below for why. An unstamped bed is NOT trusted."""
    monkeypatch.setattr(music_bed, "PROJECTS_ROOT", tmp_path)
    root = _project(tmp_path, "p")
    (root / "music.json").write_text(json.dumps({"prompt": "x, instrumental",
                                                 "length_seconds": 30}))
    bed = root / "bgm.wav"
    bed.write_bytes(b"RIFF")
    (root / "bgm.prompt.sha1").write_text(_stamp_for("x, instrumental"))

    assert music_bed.generate_bed("p", log=lambda _m: None) == bed
    assert bed.read_bytes() == b"RIFF", "a matching bed must not be regenerated"


def test_generate_degrades_when_the_venv_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(music_bed, "PROJECTS_ROOT", tmp_path)
    root = _project(tmp_path, "p")
    (root / "music.json").write_text(json.dumps({"prompt": "x, instrumental",
                                                 "length_seconds": 30}))
    monkeypatch.setattr(music_bed, "_venv_python", lambda *_a: tmp_path / "nope" / "python")
    assert music_bed.generate_bed("p", log=lambda _m: None) is None


@pytest.mark.parametrize("payload", ['{"error": "CUDA out of memory"}', "", "not json"])
def test_worker_failure_yields_no_bed(tmp_path, monkeypatch, payload):
    """A generator that dies must leave the render narration-only, never half a wav."""
    monkeypatch.setattr(music_bed, "PROJECTS_ROOT", tmp_path)
    root = _project(tmp_path, "p")
    (root / "music.json").write_text(json.dumps({"prompt": "x, instrumental",
                                                 "length_seconds": 30}))
    py = tmp_path / "venv" / "Scripts" / "python.exe"
    py.parent.mkdir(parents=True)
    py.write_text("")
    monkeypatch.setattr(music_bed, "_venv_python", lambda *_a: py)

    class _R:
        stdout, returncode = payload, 1

    monkeypatch.setattr(music_bed.subprocess, "run", lambda *_a, **_k: _R())
    assert music_bed.generate_bed("p", log=lambda _m: None) is None


# ─── regressions from the 2026-08-13 review ──────────────────────────────────

def test_changing_the_genre_invalidates_an_existing_bed(tmp_path, monkeypatch):
    """The bug the genre dropdown was invisible behind: reuse keyed on out.exists() alone, so
    picking a new genre and re-running logged "reusing bgm.wav" and shipped the OLD genre's
    music. The UI looked like it worked and the video did not change."""
    monkeypatch.setattr(music_bed, "PROJECTS_ROOT", tmp_path)
    root = _project(tmp_path, "p")
    (root / "music.json").write_text(json.dumps({"genre": "epic", "prompt": "epic, instrumental",
                                                 "length_seconds": 30}))
    (root / "bgm.wav").write_bytes(b"OLD-EPIC-BED")
    monkeypatch.setattr(music_bed, "_venv_python", lambda *_a: tmp_path / "nope" / "python")

    # No stamp at all (a bed from before this guard existed) → must not be trusted.
    msgs = []
    music_bed.generate_bed("p", log=msgs.append)
    assert any("brief changed" in m for m in msgs)

    # Stamp matching the CURRENT brief → reuse.
    (root / "bgm.wav").write_bytes(b"OLD-EPIC-BED")     # the failed run above cleared it
    (root / "bgm.prompt.sha1").write_text(_stamp_for("epic, instrumental", "epic"))
    assert music_bed.generate_bed("p", log=lambda _m: None) == root / "bgm.wav"

    # Genre changed → the stamp no longer matches → regenerate, do not ship the old bed.
    (root / "music.json").write_text(json.dumps({"genre": "ambient",
                                                 "prompt": "ambient, instrumental",
                                                 "length_seconds": 30}))
    msgs = []
    assert music_bed.generate_bed("p", log=msgs.append) is None
    assert any("brief changed" in m for m in msgs)


def test_a_corrupt_music_json_does_not_raise(tmp_path, monkeypatch):
    """Contract: every failure path yields no bed and no exception. This one raised
    JSONDecodeError straight out of generate_bed."""
    monkeypatch.setattr(music_bed, "PROJECTS_ROOT", tmp_path)
    root = _project(tmp_path, "p")
    (root / "music.json").write_text("{corrupt")
    assert music_bed.generate_bed("p", log=lambda _m: None) is None


def test_audio_seconds_survives_a_machine_with_no_ffprobe(tmp_path, monkeypatch):
    """ffprobe was invoked bare and only ValueError was caught, so a box without it on PATH
    raised FileNotFoundError out of the brief."""
    root = _project(tmp_path, "p", est=44.0)
    (root / "audio.wav").write_bytes(b"RIFF")
    monkeypatch.setattr(music_bed.shutil, "which", lambda _n: None)
    assert music_bed.audio_seconds(root) == 44.0      # falls back to the Stage 3 estimate


def test_the_default_genre_still_binds(tmp_path, monkeypatch):
    """The steer used to be skipped when the pick equalled MUSIC_GENRE — but that value is
    both the dropdown's initial state and the one register measured to survive under
    narration, so choosing the best option was the single case that stopped binding."""
    monkeypatch.setattr(music_bed, "PROJECTS_ROOT", tmp_path)
    root = _project(tmp_path, "p")
    (root / "music.json").write_text(json.dumps({"genre": music_bed.MUSIC_GENRE}))
    seen = {}
    monkeypatch.setattr("stages.stage_3._llm._client", lambda: object())

    def _capture(_c, _m, _s, user, _mx):
        seen["user"] = user
        return json.dumps({"genre": "something else", "bpm": 100,
                           "prompt": "x, instrumental, 100 bpm"})

    monkeypatch.setattr("stages.stage_3._llm._call_with_deadline", _capture)
    doc = music_bed.build_brief("p", log=lambda _m: None)
    assert f"REQUIRED GENRE: {music_bed.MUSIC_GENRE}" in seen["user"]
    assert doc["genre"] == music_bed.MUSIC_GENRE, "Master's pick must not be renamed by the model"


def test_brief_before_stage_3_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(music_bed, "PROJECTS_ROOT", tmp_path)
    (tmp_path / "p").mkdir()
    assert music_bed.build_brief("p", log=lambda _m: None) is None


def test_changing_only_the_genre_invalidates_the_bed(tmp_path, monkeypatch):
    """The Review Beats dropdown writes ONLY `genre` and leaves the old `prompt` in place, so
    a hash over the prompt alone never noticed. Combined with a failed brief call that meant
    picking a new genre silently shipped the previous genre's music."""
    monkeypatch.setattr(music_bed, "PROJECTS_ROOT", tmp_path)
    root = _project(tmp_path, "p")
    (root / "music.json").write_text(json.dumps({"genre": "epic", "prompt": "x, instrumental",
                                                 "length_seconds": 30}))
    (root / "bgm.wav").write_bytes(b"OLD")
    monkeypatch.setattr(music_bed, "_venv_python", lambda *_a: tmp_path / "nope" / "python")

    import hashlib

    def stamp_for(genre, prompt):
        return hashlib.sha1(f"{genre}|{prompt}|{music_bed.ACESTEP_STEPS}|"
                            f"{music_bed.ACESTEP_GUIDANCE}|{music_bed.ACESTEP_SEED}"
                            .encode("utf-8")).hexdigest()

    (root / "bgm.prompt.sha1").write_text(stamp_for("epic", "x, instrumental"))
    assert music_bed.generate_bed("p", log=lambda _m: None) == root / "bgm.wav"

    # Only the genre changes — the prompt is untouched, exactly as the UI leaves it.
    (root / "music.json").write_text(json.dumps({"genre": "ambient", "prompt": "x, instrumental",
                                                 "length_seconds": 30}))
    msgs = []
    assert music_bed.generate_bed("p", log=msgs.append) is None
    assert any("brief changed" in m for m in msgs)


def test_a_silent_worker_crash_does_not_bless_the_previous_bed(tmp_path, monkeypatch):
    """A worker killed before it can print (OOM, a DLL that will not load) leaves no JSON. The
    old wav was then read as success and stamped with the NEW brief — the wrong music, locked
    in, and reported forever after as "reusing bgm.wav (same brief)"."""
    monkeypatch.setattr(music_bed, "PROJECTS_ROOT", tmp_path)
    root = _project(tmp_path, "p")
    (root / "music.json").write_text(json.dumps({"genre": "g", "prompt": "x, instrumental",
                                                 "length_seconds": 30}))
    (root / "bgm.wav").write_bytes(b"PREVIOUS-GENRE")
    py = tmp_path / "venv" / "Scripts" / "python.exe"
    py.parent.mkdir(parents=True)
    py.write_text("")
    monkeypatch.setattr(music_bed, "_venv_python", lambda *_a: py)

    class _Killed:
        stdout, returncode = "", -9        # no output at all

    monkeypatch.setattr(music_bed.subprocess, "run", lambda *_a, **_k: _Killed())
    assert music_bed.generate_bed("p", force=True, log=lambda _m: None) is None
    assert not (root / "bgm.wav").exists(), "the stale bed must not survive a failed run"
    assert not (root / "bgm.prompt.sha1").exists()
