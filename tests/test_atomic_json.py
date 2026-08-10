"""Crash-safe writes for the files that hold Master's hand-made work (2026-08-10).

The old `path.write_text(json.dumps(...))` truncated the target on open, so an
interrupted save left a 0-byte file — measured on a real locks.json: 40 locked beats
became 0 bytes. Worse, `load_review_locks` swallowed the resulting JSONDecodeError and
returned an empty doc, so the UI showed "nothing picked" and the next click saved that
emptiness over the wreckage.
"""
import json
import os
import subprocess
import sys

import pytest

from utils.atomic_json import write_json_atomic


def test_writes_and_reads_back(tmp_path):
    p = tmp_path / "locks.json"
    doc = {"locks": {"2:0": {"panels": [{"page": 5, "panel": 1}]}}}
    assert write_json_atomic(p, doc) == p
    assert json.loads(p.read_text()) == doc


def test_creates_missing_parent_dirs(tmp_path):
    p = tmp_path / "review" / "deep" / "locks.json"
    write_json_atomic(p, {"a": 1})
    assert json.loads(p.read_text()) == {"a": 1}


def test_overwrite_keeps_the_old_file_until_the_new_one_is_whole(tmp_path):
    """The point of the exercise: a killed process must not destroy what was there."""
    p = tmp_path / "locks.json"
    original = {"locks": {str(i): [{"page": i}] for i in range(40)}}
    write_json_atomic(p, original)
    before = p.read_bytes()

    # Kill a real process at the exact moment the old code would have truncated.
    code = (
        "import os,sys;"
        "sys.path.insert(0, %r);" % os.getcwd() +
        "from utils.atomic_json import write_json_atomic;"
        "import utils.atomic_json as m;"
        "_r = os.replace;"
        "m.os.replace = lambda *a: os._exit(1);"   # die after the temp is fully written
        "write_json_atomic(%r, {'locks': {}})" % str(p)
    )
    subprocess.run([sys.executable, "-c", code], capture_output=True)

    assert p.read_bytes() == before, "the original file must survive an interrupted save"
    assert len(json.loads(p.read_text())["locks"]) == 40


def test_no_tmp_litter_left_behind(tmp_path):
    p = tmp_path / "locks.json"
    write_json_atomic(p, {"a": 1})
    assert list(tmp_path.iterdir()) == [p]


def test_unserialisable_payload_leaves_target_untouched(tmp_path):
    p = tmp_path / "locks.json"
    write_json_atomic(p, {"good": 1})
    with pytest.raises(TypeError):
        write_json_atomic(p, {"bad": object()})
    assert json.loads(p.read_text()) == {"good": 1}
    assert not (tmp_path / "locks.json.tmp").exists()


# ── the read side: corruption must not be laundered into "nothing was picked" ──

def test_corrupt_locks_are_quarantined_not_silently_emptied(tmp_path, monkeypatch):
    import ui.bridge as bridge
    monkeypatch.setattr(bridge, "PROJECTS_ROOT", tmp_path)
    review = tmp_path / "proj" / "review"
    review.mkdir(parents=True)
    (review / "locks.json").write_text('{"locks": {"2:0": ')   # truncated mid-write

    doc = bridge.load_review_locks("proj")

    assert doc["locks"] == {}, "caller still gets a usable empty doc"
    assert not (review / "locks.json").exists(), "the damaged file must be moved aside"
    assert (review / "locks.json.corrupt").exists(), "…and kept, not deleted"


def test_healthy_locks_load_normally(tmp_path, monkeypatch):
    import ui.bridge as bridge
    monkeypatch.setattr(bridge, "PROJECTS_ROOT", tmp_path)
    review = tmp_path / "proj" / "review"
    review.mkdir(parents=True)
    doc = {"approved": True, "locks": {"1:0": {"panels": [{"page": 3, "panel": 0}]}}}
    (review / "locks.json").write_text(json.dumps(doc))

    assert bridge.load_review_locks("proj") == doc
    assert not (review / "locks.json.corrupt").exists()


def test_missing_locks_file_is_not_an_error(tmp_path, monkeypatch):
    import ui.bridge as bridge
    monkeypatch.setattr(bridge, "PROJECTS_ROOT", tmp_path)
    assert bridge.load_review_locks("nope")["locks"] == {}
