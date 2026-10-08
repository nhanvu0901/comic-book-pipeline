"""Background TTS: the cache, the one-job-per-batch worker protocol, the single status file, and the
Stage-4 reuse of the chunks it produced.

The Chatterbox worker is replaced by tests/fixtures/fake_chatterbox_worker.py — same job-file /
stdout-lines protocol, no torch — so these run the REAL subprocess path (Popen, line reading,
atempo through ffmpeg, the cache), not a mock of it."""
import hashlib
import json
import shutil
import sys
import threading
import time
import wave
from pathlib import Path

import pytest

import config
from stages.stage_4 import background_tts as bt
from stages.stage_4 import chatterbox_tts, tts_status
from stages.stage_4.pipeline import _normalize_for_tts

FAKE_WORKER = Path(__file__).parent / "fixtures" / "fake_chatterbox_worker.py"
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required for atempo")


@pytest.fixture
def tts_env(tmp_path, monkeypatch):
    """A projects root, the fake worker wired in as THE worker, flag ON (seeded), atempo 1.0 unless a
    test changes it. Returns an object with the log path of worker launches."""
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(config, "ENABLE_VIDEO_CLIPS", True)
    monkeypatch.setattr(config, "CHATTERBOX_SEED", 42)
    import stages.stage_4.pipeline as p4
    monkeypatch.setattr(p4, "POST_ATEMPO", 1.0)
    monkeypatch.setattr(chatterbox_tts, "_venv_python", lambda venv_dir=None: Path(sys.executable))
    monkeypatch.setattr(chatterbox_tts, "_WORKER", FAKE_WORKER)
    log = tmp_path / "worker_launches.jsonl"
    monkeypatch.setenv("FAKE_WORKER_LOG", str(log))
    monkeypatch.delenv("FAKE_WORKER_DELAY", raising=False)
    monkeypatch.setattr(bt, "_RUNNERS", {})

    class Env:
        root = tmp_path
        launches_log = log

        def launches(self):
            return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []

        def project(self, name, scenes, **extra):
            d = tmp_path / name
            d.mkdir(parents=True, exist_ok=True)
            (d / "narration.json").write_text(json.dumps({"mode": "explore_answer", "scenes": scenes, **extra}))
            return d
    return Env()


def _scene(sid, text, beats=None, **kw):
    return {"scene_id": sid, "text": text, "visual_beats": beats if beats is not None else [text], **kw}


QA_SCENES = [
    _scene(1, "Hook line here.", is_intro=True),
    _scene(2, "Peter fell, then he rose again.", ["Peter fell,", "then he rose again."]),
    _scene(3, "The end of it all.", []),          # no visual_beats: ONE review row keyed "3"
    _scene(4, "Follow for more.", is_outro=True),
]


# ─── the cache key ────────────────────────────────────────────────────────────

def test_cache_key_formula():
    text, voice, ex, cfg, seed, atempo = "Hello world", "voice_ref.wav", 0.5, 0.5, 42, 1.15
    expected = hashlib.sha256(f"{text}|{voice}|{ex}|{cfg}|{seed}|{atempo}".encode()).hexdigest()
    assert bt.get_cache_key(text, voice, ex, cfg, seed, atempo) == expected
    # temperature, when given, is part of the key too
    assert bt.get_cache_key(text, voice, ex, cfg, seed, atempo, 0.8) == hashlib.sha256(
        f"{text}|{voice}|{ex}|{cfg}|{seed}|{atempo}|0.8".encode()).hexdigest()


def test_every_knob_changes_the_key():
    base = dict(text="Hi there.", voice="v.wav", exaggeration=0.5, cfg_weight=0.5, seed=42, post_atempo=1.15)
    k0 = bt.get_cache_key(**base)
    for field, other in [("text", "Hi there!"), ("voice", "w.wav"), ("exaggeration", 0.6), ("cfg_weight", 0.4),
                         ("seed", 43), ("post_atempo", 1.3)]:
        assert bt.get_cache_key(**{**base, field: other}) != k0, field
    assert bt.get_cache_key(**{**base, "seed": None}) != k0          # unseeded audio is another audio


# ─── Stage-4 chunking ─────────────────────────────────────────────────────────

def test_chunks_equal_stage4s_on_a_narration_with_dashes_and_ellipses():
    """M2: the chunk list must be exactly what Stage 4's Chatterbox path synthesizes — the whole
    narration normalised (em-dash, ellipsis, curly quotes) THEN split into sentences."""
    scenes = [
        _scene(1, "Peter Parker—the hero… he never gave up—not even once."),
        _scene(2, "“Great power,” said Ben. It was a lesson."),
        _scene(3, "He remembered it—always."),
    ]
    full = " ".join(s["text"] for s in scenes)
    stage4 = chatterbox_tts._chunks(_normalize_for_tts(full))       # synthesize_project's own two calls
    assert bt.plan_chunks(scenes) == stage4
    assert all("—" not in c and "…" not in c and "“" not in c and "”" not in c for c in stage4)
    # ... and it is NOT what chunking the raw scene text would give
    assert [c for s in scenes for c in chatterbox_tts._chunks(s["text"])] != stage4


def test_post_atempo_is_stage4s_for_the_mode(monkeypatch):
    import stages.stage_4.pipeline as p4
    monkeypatch.setattr(p4, "POST_ATEMPO", 1.15)
    monkeypatch.setattr(p4, "POST_ATEMPO_LONGFORM", 1.05)
    assert bt.resolve_post_atempo({"mode": "explore_answer"}) == 1.15
    assert bt.resolve_post_atempo({"mode": "panel_walk"}) == 1.05
    assert p4.post_atempo_for_mode("explore_answer") == 1.15


# ─── the runner ───────────────────────────────────────────────────────────────

def test_runner_makes_one_worker_job_per_batch_not_one_per_chunk(tts_env):
    tts_env.project("qa", QA_SCENES)
    chunks = bt.plan_chunks(QA_SCENES)
    assert len(chunks) == 4
    status = bt.BackgroundTTSRunner("qa").run_sync()
    launches = tts_env.launches()
    assert len(launches) == 1, "M3: all missing chunks go to the worker as ONE job"
    assert [c["text"] for c in launches[0]["job"]["chunks"]] == chunks
    assert status["completed"] is True and status["chunks_done"] == 4


def test_every_chunk_is_seeded_on_its_own_and_unseeded_with_the_flag_off(tts_env, monkeypatch):
    tts_env.project("qa", QA_SCENES)
    bt.BackgroundTTSRunner("qa").run_sync()
    seeds = [c.get("seed") for c in tts_env.launches()[0]["job"]["chunks"]]
    assert seeds == [42] * 4, "the worker re-seeds before each chunk: audio depends on the chunk key only"
    assert "seed" not in tts_env.launches()[0]["job"]       # no job-level seed that would depend on the index

    shutil.rmtree(tts_env.root / "qa" / "cache")
    tts_env.launches_log.unlink()
    monkeypatch.setattr(config, "ENABLE_VIDEO_CLIPS", False)
    bt.BackgroundTTSRunner("qa").run_sync()
    assert all("seed" not in c for c in tts_env.launches()[0]["job"]["chunks"])


def test_status_uses_review_beat_keys_and_one_path(tts_env):
    """Real narrations carry visual_beats as plain STRINGS and review keys are "<sid>:<frag>" (0-based),
    "intro"/"outro" for single-fragment bookends — the fixture the old code (b.get('beat_id')) never saw."""
    tts_env.project("qa", QA_SCENES)
    status = bt.BackgroundTTSRunner("qa").run_sync()
    # 2.0 s/…: fake audio is 0.1s per word. scene 2 = 6 words (2 + 4)
    assert set(status["beat_durations"]) == {"intro", "2:0", "2:1", "3", "outro"}
    assert status["beat_durations"]["2:0"] == pytest.approx(0.2)
    assert status["beat_durations"]["2:1"] == pytest.approx(0.4)
    assert status["beat_durations"]["intro"] == pytest.approx(0.3)
    assert status["completed"] is True
    # ONE status file: review/tts_status.json (what the UI and the web routes read) — and no second copy
    p = tts_env.root / "qa"
    assert tts_status.status_path(p) == p / "review" / "tts_status.json" and tts_status.status_path(p).is_file()
    assert not (p / "cache" / "tts" / "status.json").exists()
    assert tts_status.beat_duration(p, "2:1") == pytest.approx(0.4)
    assert tts_status.beat_duration(p, "9:9") is None
    # the windows are the cached audio's absolute beat windows, contiguous from 0
    w = status["beat_windows"]
    assert w["intro"][0] == 0.0 and w["2:0"][0] == pytest.approx(w["intro"][1])
    assert w["outro"][1] == pytest.approx(sum(s for s in status["scene_durations"].values()))


def test_a_second_pass_synthesizes_nothing_and_an_edit_only_the_changed_sentence(tts_env):
    proj = tts_env.project("qa", QA_SCENES)
    r = bt.BackgroundTTSRunner("qa")
    r.run_sync()
    r.run_sync()
    assert len(tts_env.launches()) == 1, "everything cached: no worker at all"

    edited = json.loads((proj / "narration.json").read_text())
    edited["scenes"][2]["text"] = "A different ending now."
    (proj / "narration.json").write_text(json.dumps(edited))
    status = bt.BackgroundTTSRunner("qa").run_sync()
    launches = tts_env.launches()
    assert len(launches) == 2
    assert [c["text"] for c in launches[1]["job"]["chunks"]] == ["A different ending now."]
    assert status["completed"] is True and status["beat_durations"]["3"] == pytest.approx(0.4)


def test_priority_bump_puts_that_scene_first(tts_env):
    tts_env.project("qa", QA_SCENES)
    r = bt.BackgroundTTSRunner("qa")
    r.bump_priority_beat("3")                       # a review beat key, not a scene id
    assert r.priority_scenes == [3]
    r.run_sync()
    order = [c["text"] for c in tts_env.launches()[0]["job"]["chunks"]]
    assert order[0] == "The end of it all.", order
    assert sorted(order) == sorted(bt.plan_chunks(QA_SCENES))


def test_a_bump_while_a_batch_runs_restarts_it_reordered(tts_env, monkeypatch):
    monkeypatch.setenv("FAKE_WORKER_DELAY", "0.4")
    scenes = [_scene(i, f"Sentence number {i} is here.") for i in range(1, 8)]
    tts_env.project("qa", scenes)
    r = bt.start_background_tts("qa")
    deadline = time.time() + 20
    while time.time() < deadline and tts_status.read_status(tts_env.root / "qa").get("chunks_done", 0) < 1:
        time.sleep(0.05)
    r.bump_priority_beat("7:0")
    deadline = time.time() + 40
    while time.time() < deadline and r.is_running():
        time.sleep(0.1)
    assert not r.is_running()
    launches = tts_env.launches()
    assert len(launches) == 2, "the bump ended the first batch; one re-ordered batch finished the rest"
    second = [c["text"] for c in launches[1]["job"]["chunks"]]
    assert second[0] == "Sentence number 7 is here."
    st = tts_status.read_status(tts_env.root / "qa")
    assert st["completed"] is True and st["chunks_done"] == 7


def test_registry_gives_one_runner_per_project_and_bump_reaches_it(tts_env, monkeypatch):
    tts_env.project("p_test", [_scene(1, "Scene one."), _scene(2, "Scene two.")])
    monkeypatch.setattr(bt.BackgroundTTSRunner, "run_sync", lambda self, cb=None: {"completed": True})
    r1 = bt.start_background_tts("p_test")
    r2 = bt.start_background_tts("p_test")
    assert r1 is r2, "one runner per project — a bump must reach the runner that is running"
    assert bt.bump_priority_beat("p_test", "2:0") is r1
    assert 2 in r1.priority_scenes


def test_a_narration_edit_resyncs_a_runner_that_already_finished(tts_env):
    proj = tts_env.project("qa", [_scene(1, "First sentence."), _scene(2, "Second sentence.")])
    r = bt.start_background_tts("qa")
    while r.is_running():
        time.sleep(0.05)
    assert tts_status.read_status(proj)["completed"] is True
    doc = json.loads((proj / "narration.json").read_text())
    doc["scenes"][1]["text"] = "Brand new second sentence now."
    doc["scenes"][1]["visual_beats"] = [doc["scenes"][1]["text"]]
    (proj / "narration.json").write_text(json.dumps(doc))
    bt.request_resync("qa")
    r2 = bt._RUNNERS["qa"]
    while r2.is_running():
        time.sleep(0.05)
    st = tts_status.read_status(proj)
    assert st["completed"] is True and st["beat_durations"]["2:0"] == pytest.approx(0.5)


def test_background_tts_never_calls_ensure_reviewed(tts_env, monkeypatch):
    """The review gate is OPEN while this runs; ensure_reviewed exists to stop Stage 4 until it closes."""
    import stages.review_gate as rg
    import stages.stage_4.pipeline as p4
    called = []

    def boom(*a, **k):
        called.append(a)
        raise AssertionError("ensure_reviewed must not run in the background TTS")

    monkeypatch.setattr(rg, "ensure_reviewed", boom)
    monkeypatch.setattr(p4, "ensure_reviewed", boom)
    tts_env.project("qa", QA_SCENES)
    bt.BackgroundTTSRunner("qa").run_sync()
    bt.load_or_synthesize_cached("qa", QA_SCENES, post_atempo=1.0)
    assert called == []


def test_a_missing_venv_is_reported_in_the_status_not_swallowed(tts_env, monkeypatch):
    monkeypatch.setattr(chatterbox_tts, "_venv_python", lambda venv_dir=None: tts_env.root / "nope" / "python")
    tts_env.project("qa", QA_SCENES)
    status = bt.BackgroundTTSRunner("qa").run_sync()
    assert "venv missing" in status["error"] and status["completed"] is False
    assert tts_status.read_status(tts_env.root / "qa")["error"] == status["error"]


def test_a_chunk_the_worker_cannot_make_is_retried_once_then_reported(tts_env):
    tts_env.project("qa", [_scene(1, "Fine sentence one."), _scene(2, "This will FAIL always.")])
    status = bt.BackgroundTTSRunner("qa").run_sync()
    assert len(tts_env.launches()) == 2                # one retry in a fresh job
    assert "could not be synthesized" in status["error"] and status["completed"] is False
    assert status["chunks_done"] == 1 and "1" in {k.partition(":")[0] for k in status["beat_durations"]}


def test_a_narration_with_no_file_reports_an_error(tts_env):
    (tts_env.root / "ghost").mkdir()
    status = bt.BackgroundTTSRunner("ghost").run_sync()
    assert "narration.json" in status["error"]


# ─── Stage 4 reuses the runner's chunks ────────────────────────────────────────

@needs_ffmpeg
def test_stage4_reuses_the_runners_cache_and_matches_its_beat_windows(tts_env):
    """The audio Stage 4 builds from the cache is the very audio the beat windows describe."""
    proj = tts_env.project("qa", QA_SCENES)
    status = bt.BackgroundTTSRunner("qa").run_sync()
    assert len(tts_env.launches()) == 1
    reused = bt.load_or_synthesize_cached("qa", QA_SCENES, post_atempo=1.0, provider="chatterbox")
    assert len(tts_env.launches()) == 1, "Stage 4 found every chunk in the cache"
    with wave.open(str(reused.audio_path), "rb") as wf:
        audio_s = wf.getnframes() / wf.getframerate()
    assert audio_s == pytest.approx(reused.duration_seconds, abs=1e-3)
    assert reused.duration_seconds == pytest.approx(status["beat_windows"]["outro"][1], abs=1e-3)
    # the word timeline: contiguous, ends where the last beat ends
    assert reused.words[0]["start"] == 0.0
    assert reused.words[-1]["end"] == pytest.approx(reused.duration_seconds, abs=1e-3)
    # scene timings Stage 4 derives from those words give the same scene durations as the status
    from stages.stage_4.chunker import align_scenes_to_words
    for st in align_scenes_to_words(QA_SCENES, reused.words):
        assert st.end - st.start == pytest.approx(status["scene_durations"][str(st.scene_id)], abs=2e-3)


@needs_ffmpeg
def test_stage4_synthesizes_only_missing_chunks_in_one_job(tts_env):
    tts_env.project("qa", QA_SCENES)
    chunks = bt.plan_chunks(QA_SCENES)
    cache = bt.ChunkCache(tts_env.root / "qa" / "cache" / "tts",
                          bt.TTSSettings.current(voice_wav=None, post_atempo=1.0))
    bt.synthesize_missing(chunks[:2], cache)                      # two of the four are cached
    assert len(tts_env.launches()) == 1
    bt.load_or_synthesize_cached("qa", QA_SCENES, post_atempo=1.0)
    launches = tts_env.launches()
    assert len(launches) == 2 and [c["text"] for c in launches[1]["job"]["chunks"]] == chunks[2:]


@needs_ffmpeg
def test_atempo_is_applied_per_chunk_and_is_part_of_the_cache(tts_env):
    tts_env.project("qa", [_scene(1, "One two three four five six seven eight nine ten.")])
    chunks = bt.plan_chunks([_scene(1, "One two three four five six seven eight nine ten.")])
    cache = bt.ChunkCache(tts_env.root / "qa" / "cache" / "tts",
                          bt.TTSSettings.current(voice_wav=None, post_atempo=1.25))
    bt.synthesize_missing(chunks, cache)
    assert cache.duration(chunks[0]) == pytest.approx(1.0 / 1.25, abs=0.02)   # fake audio: 10 words = 1.0 s
    other = bt.ChunkCache(cache.dir, bt.TTSSettings.current(voice_wav=None, post_atempo=1.0))
    assert not other.has(chunks[0]), "another atempo is another cache entry"


def _p4(tts_env, monkeypatch):
    """stage_4.pipeline + review_gate bound PROJECTS_ROOT at import; point them at the temp root and
    approve the (hard) review gate the way Master's Approve button does."""
    import stages.review_gate as rg
    import stages.stage_4.pipeline as p4
    monkeypatch.setattr(p4, "PROJECTS_ROOT", tts_env.root)
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tts_env.root)
    return p4


def _approve(root):
    (root / "review").mkdir(parents=True, exist_ok=True)
    (root / "review" / "locks.json").write_text(json.dumps({"approved": True, "locks": {}}))


@needs_ffmpeg
def test_synthesize_project_flag_on_builds_stage4_artifacts_from_the_cache(tts_env, monkeypatch):
    p4 = _p4(tts_env, monkeypatch)
    proj = tts_env.project("qa", QA_SCENES)
    _approve(proj)
    bt.BackgroundTTSRunner("qa").run_sync()                          # the background pass ran first
    res = p4.synthesize_project("qa", provider="chatterbox", skip_review=True, post_atempo=1.0)
    assert len(tts_env.launches()) == 1, "Stage 4 reused every cached chunk"
    assert (proj / "audio.wav").is_file() and (proj / "word_timestamps.json").is_file()
    assert json.loads((proj / "tts_voice.json").read_text())["provider"] == "chatterbox-cached"
    assert [t.scene_id for t in res.scene_timings] == [1, 2, 3, 4]
    assert res.audio_duration_seconds == pytest.approx(
        tts_status.read_status(proj)["beat_windows"]["outro"][1], abs=2e-3)


def test_synthesize_project_flag_off_never_touches_the_cache(tts_env, monkeypatch):
    """Flag OFF = the stable Chatterbox path, untouched: whole-text synthesize() + one atempo."""
    p4 = _p4(tts_env, monkeypatch)
    monkeypatch.setattr(config, "ENABLE_VIDEO_CLIPS", False)
    _approve(tts_env.project("qa", [_scene(1, "One two three four.")]))

    def no_cache(*a, **k):
        raise AssertionError("flag OFF must not use the clip chunk cache")

    monkeypatch.setattr(bt, "load_or_synthesize_cached", no_cache)
    import io
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(24000); wf.writeframes(b"\x00\x00" * 48000)
    seen = {}

    def fake_synth(text, **kw):
        seen["text"] = text
        return chatterbox_tts.ChatterboxResult(
            wav_bytes=buf.getvalue(), sample_rate=24000,
            word_timestamps=chatterbox_tts._even_words(text, 0.0, 2.0))

    monkeypatch.setattr(chatterbox_tts, "synthesize", fake_synth)
    res = p4.synthesize_project("qa", provider="chatterbox", skip_review=True, post_atempo=1.0)
    assert seen["text"] == "One two three four." and res.audio_duration_seconds == pytest.approx(2.0)
    assert not (tts_env.root / "qa" / "cache" / "tts").exists() or not list((tts_env.root / "qa" / "cache" / "tts").glob("*.wav"))


# ─── regressions the reviewer asked for explicitly (M15, M16) ─────────────────────────────────────

def test_bump_on_a_project_with_no_runner_does_not_deadlock(tts_env):
    """M15: bump_priority_beat used to hold the non-reentrant registry lock and call start_background_tts,
    which takes it again — a hang that then blocked every later start/bump too."""
    tts_env.project("qa", QA_SCENES)
    done = threading.Event()
    box = {}

    def go():
        box["runner"] = bt.bump_priority_beat("qa", "3")
        done.set()

    t = threading.Thread(target=go, daemon=True)
    t.start()
    assert done.wait(10), "bump_priority_beat hung (registry lock held while re-acquiring it)"
    # the lock must be free afterwards: another start/bump from a second thread returns promptly too
    t2 = threading.Thread(target=lambda: bt.start_background_tts("qa"), daemon=True)
    t2.start()
    t2.join(10)
    assert not t2.is_alive()
    r = box["runner"]
    while r.is_running():
        time.sleep(0.05)


def test_start_after_a_finished_run_resynthesizes_an_edited_sentence(tts_env):
    """M16: start_background_tts used to hand back the finished runner and do nothing, so a re-open of the
    review screen after a narration edit never re-voiced the edit."""
    proj = tts_env.project("qa", QA_SCENES)
    r = bt.start_background_tts("qa")
    while r.is_running():
        time.sleep(0.05)
    assert len(tts_env.launches()) == 1 and tts_status.read_status(proj)["completed"] is True
    doc = json.loads((proj / "narration.json").read_text())
    doc["scenes"][2]["text"] = "A brand new ending sentence."
    (proj / "narration.json").write_text(json.dumps(doc))
    again = bt.start_background_tts("qa")                      # what the review screen does on every open
    assert again is r, "one runner per project"
    while again.is_running():
        time.sleep(0.05)
    launches = tts_env.launches()
    assert len(launches) == 2
    assert [c["text"] for c in launches[1]["job"]["chunks"]] == ["A brand new ending sentence."]
    assert tts_status.read_status(proj)["beat_durations"]["3"] == pytest.approx(0.5)
