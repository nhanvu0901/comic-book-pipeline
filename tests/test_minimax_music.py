"""Unit tests for the private Hugging Face MiniMax Music 3 adapter."""
from pathlib import Path

from stages import minimax_music


def _state():
    return {
        "mode": "studio",
        "description": "A cinematic instrumental score.",
        "instrumental": True,
        "title": "Comic score",
        "lyrics": "[intro]\n[instrumental]",
        "global_meta": "120 BPM, D minor.",
        "vocals": "Instrumental only. No vocals.",
        "arrangement": "Strings and percussion.",
    }


def test_generate_music_calls_studio_endpoint_and_returns_validated_mp3(tmp_path, monkeypatch):
    source = tmp_path / "returned.wav"
    source.write_bytes(b"WAV")
    out = tmp_path / "bgm.mp3"
    seen = {}

    class Client:
        def __init__(self, space, **kwargs):
            seen["space"] = space
            seen["kwargs"] = kwargs

        def predict(self, **kwargs):
            seen["request"] = kwargs
            return (None, None, str(source), 137)

    monkeypatch.setattr(minimax_music, "Client", Client)

    def _convert(src, target, seconds, *, log):
        assert src == source and seconds == 59.0
        target.write_bytes(b"MP3")
        return target

    monkeypatch.setattr(minimax_music, "_transcode_and_validate", _convert)

    assert minimax_music.generate_music(_state(), 59.0, out, log=lambda _: None) == out
    assert seen["space"] == "Neopet2001/MiniMax-Music3"
    assert seen["request"]["state"] == _state()
    assert seen["request"]["api_name"] == "/studio_generate"


def test_generate_music_removes_stale_output_when_remote_call_fails(tmp_path, monkeypatch):
    out = tmp_path / "bgm.mp3"
    out.write_bytes(b"STALE")

    class Client:
        def __init__(self, *_a, **_k):
            pass

        def predict(self, **_kwargs):
            raise RuntimeError("ZeroGPU quota exceeded")

    monkeypatch.setattr(minimax_music, "Client", Client)

    assert minimax_music.generate_music(_state(), 59.0, out, log=lambda _: None) is None
    assert not out.exists()


def test_transcode_rejects_audio_shorter_than_final_video(tmp_path, monkeypatch):
    source = tmp_path / "short.wav"
    source.write_bytes(b"WAV")
    out = tmp_path / "bgm.mp3"
    out.write_bytes(b"STALE")
    monkeypatch.setattr(minimax_music, "_probe_duration", lambda _path: 20.0)

    assert minimax_music._transcode_and_validate(source, out, 59.0, log=lambda _: None) is None
    assert not out.exists()


def test_transcode_rejects_even_a_fractionally_short_music_track(tmp_path, monkeypatch):
    source = tmp_path / "short.wav"
    source.write_bytes(b"WAV")
    out = tmp_path / "bgm.mp3"
    monkeypatch.setattr(minimax_music, "_probe_duration", lambda path: 58.90 if path == source else 59.0)
    monkeypatch.setattr(minimax_music.shutil, "which", lambda name: "ffmpeg" if name == "ffmpeg" else None)

    class Result:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(minimax_music.subprocess, "run", lambda *_a, **_k: Result())

    assert minimax_music._transcode_and_validate(source, out, 59.0, log=lambda _: None) is None
    assert not out.exists()
