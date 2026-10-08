import hashlib
import json
import shutil
import wave
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from stages.stage_4 import background_tts, chatterbox_tts


def _make_dummy_wav(path: Path, duration: float = 1.0, sr: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    nframes = int(duration * sr)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(b"\x00\x00" * nframes)


def test_cache_key_formula():
    text = "Hello world"
    voice = "voice_ref.wav"
    ex = 0.5
    cfg = 0.5
    seed = 42
    atempo = 1.15

    expected_payload = f"{text}|{voice}|{ex}|{cfg}|{seed}|{atempo}".encode("utf-8")
    expected_key = hashlib.sha256(expected_payload).hexdigest()

    computed_key = background_tts.get_cache_key(
        text=text, voice=voice, exaggeration=ex, cfg_weight=cfg, seed=seed, post_atempo=atempo
    )
    assert computed_key == expected_key


def test_background_tts_uses_cache_and_priority_queue(tmp_path, monkeypatch):
    import config

    proj_dir = tmp_path / "test_project"
    proj_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)

    narration = {
        "mode": "explore_answer",
        "scenes": [
            {
                "scene_id": 1,
                "text": "Sentence one is here.",
                "visual_beats": [{"beat_id": "1:1", "text": "Sentence one is here."}],
            },
            {
                "scene_id": 2,
                "text": "Sentence two is also here.",
                "visual_beats": [{"beat_id": "2:1", "text": "Sentence two is also here."}],
            },
        ],
    }
    (proj_dir / "narration.json").write_text(json.dumps(narration))

    runner = background_tts.BackgroundTTSRunner("test_project")

    # Mock synthesize_single_chunk to avoid calling real torch/model in unit test
    synthesized_chunks = []

    def fake_synth(text, **kw):
        synthesized_chunks.append(text)
        out_wav = tmp_path / "mock.wav"
        _make_dummy_wav(out_wav, 1.0)
        return out_wav.read_bytes(), 1.0, 24000

    monkeypatch.setattr(background_tts, "_synthesize_chunk_raw", fake_synth)

    # Bump scene 2 to front of queue
    runner.bump_priority_beat("2:1")

    # Run processing
    runner.run_sync()

    # Verify scene 2 text was synthesized before scene 1 text
    assert synthesized_chunks[0] == "Sentence two is also here."
    assert synthesized_chunks[1] == "Sentence one is here."

    # Verify cache files were created
    cache_dir = proj_dir / "cache" / "tts"
    assert cache_dir.exists()
    wav_files = list(cache_dir.glob("*.wav"))
    assert len(wav_files) == 2

    # Status file updated
    status_file = cache_dir / "status.json"
    assert status_file.exists()
    status = json.loads(status_file.read_text())
    assert status["completed"] is True
    assert "2:1" in status["beat_durations"]
    assert "1:1" in status["beat_durations"]


def test_stage4_reuses_wav_cache(tmp_path, monkeypatch):
    import config
    from stages.stage_4 import pipeline

    proj_dir = tmp_path / "test_cache_reuse"
    proj_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(config, "ENABLE_VIDEO_CLIPS", True)
    monkeypatch.setattr(config, "CHATTERBOX_SEED", 42)

    narration = {
        "mode": "explore_answer",
        "scenes": [
            {
                "scene_id": 1,
                "text": "Sentence one is here.",
                "visual_beats": [{"beat_id": "1:1", "text": "Sentence one is here."}],
            }
        ],
    }
    (proj_dir / "narration.json").write_text(json.dumps(narration))

    # Pre-populate cache
    cache_dir = proj_dir / "cache" / "tts"
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = background_tts.get_cache_key(
        text="Sentence one is here.",
        voice="built-in",
        exaggeration=0.5,
        cfg_weight=0.5,
        seed=42,
        post_atempo=1.30,
    )
    _make_dummy_wav(cache_dir / f"{key}.wav", 2.0)
    (cache_dir / f"{key}.json").write_text(
        json.dumps({
            "duration": 2.0,
            "words": [
                {"word": "Sentence", "start": 0.0, "end": 0.5},
                {"word": "one", "start": 0.5, "end": 1.0},
                {"word": "is", "start": 1.0, "end": 1.5},
                {"word": "here.", "start": 1.5, "end": 2.0},
            ]
        })
    )

    # Synthesize must NOT be called because cache is 100% complete
    synth_mock = MagicMock()
    monkeypatch.setattr(chatterbox_tts, "synthesize", synth_mock)

    # Call helper that checks/reuses cache
    reused = background_tts.load_or_synthesize_cached(
        project_name="test_cache_reuse",
        scenes=narration["scenes"],
        post_atempo=1.30,
        provider="chatterbox",
    )
    assert reused is not None
    assert synth_mock.call_count == 0
    assert reused.duration_seconds == pytest.approx(2.0, rel=1e-2)
