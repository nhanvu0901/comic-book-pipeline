# tests/test_longform_stitch.py — command-construction tests, no real ffmpeg run.
from pathlib import Path

import pytest

from stages.longform import stitch as S


def test_zero_segments_raises():
    with pytest.raises(ValueError):
        S.stitch_segments([], Path("/tmp/out.mp4"))


def test_single_segment_copies_file(tmp_path):
    src = tmp_path / "seg00.mp4"
    src.write_bytes(b"fake mp4 bytes")
    out = tmp_path / "final.mp4"
    result = S.stitch_segments([src], out)
    assert result == out
    assert out.read_bytes() == b"fake mp4 bytes"


def test_dissolve_zero_uses_concat_demuxer(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(S, "_require_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(S, "_run", lambda cmd: calls.append(cmd))

    segs = [tmp_path / f"seg{i:02d}.mp4" for i in range(3)]
    out = tmp_path / "final.mp4"
    result = S.stitch_segments(segs, out, dissolve=0)

    assert result == out
    assert len(calls) == 1
    cmd = calls[0]
    assert "-f" in cmd and cmd[cmd.index("-f") + 1] == "concat"
    assert "-c" in cmd and cmd[cmd.index("-c") + 1] == "copy"
    list_file = tmp_path / "_longform_concat_list.txt"
    assert list_file.exists()
    text = list_file.read_text()
    for s in segs:
        assert f"file '{s.resolve()}'" in text


def test_dissolve_positive_builds_xfade_acrossfade_chain(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(S, "_require_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(S, "_run", lambda cmd: calls.append(cmd))
    durs = {"seg00.mp4": 10.0, "seg01.mp4": 12.0, "seg02.mp4": 8.0}
    monkeypatch.setattr(S, "_probe_duration", lambda p: durs[Path(p).name])

    segs = [tmp_path / name for name in durs]
    out = tmp_path / "final.mp4"
    result = S.stitch_segments(segs, out, dissolve=0.4)

    assert result == out
    assert len(calls) == 1
    cmd = calls[0]
    # 3 inputs
    assert cmd.count("-i") == 3
    fc = cmd[cmd.index("-filter_complex") + 1]
    # video: non-last clips tail-padded, last clip is not
    assert "tpad=stop_mode=clone:stop_duration=0.4[v0]" in fc
    assert "tpad=stop_mode=clone:stop_duration=0.4[v1]" in fc
    assert "[2:v]settb=AVTB,fps=30[v2]" in fc
    # audio: non-last clips silence-padded, last clip passthrough
    assert "apad=pad_dur=0.4[a0]" in fc
    assert "apad=pad_dur=0.4[a1]" in fc
    assert "[2:a]anull[a2]" in fc
    # cumulative offsets: 10.0, then 10.0+12.0=22.0
    assert "xfade=transition=dissolve:duration=0.4:offset=10.0[x1]" in fc
    assert "xfade=transition=dissolve:duration=0.4:offset=22.0[x2]" in fc
    # acrossfade chained twice, no offset needed
    assert fc.count("acrossfade=d=0.4") == 2
    # final maps point at the last chain outputs
    assert cmd[cmd.index("-map") + 1] == "[x2]"
    assert "[y2]" in cmd


def test_xfade_offsets_cumulative_and_sum_preserving():
    durs = [10.0, 12.0, 8.0]
    offs = S._xfade_offsets(durs)
    assert offs == [10.0, 22.0]
    assert offs[-1] + durs[-1] == sum(durs)


if __name__ == "__main__":
    # ponytail: smallest runnable self-check without pytest, per non-trivial-logic rule.
    test_zero_segments_raises()
    print("ok: zero-segment fail-loud")
    test_xfade_offsets_cumulative_and_sum_preserving()
    print("ok: cumulative offsets preserve total duration")
