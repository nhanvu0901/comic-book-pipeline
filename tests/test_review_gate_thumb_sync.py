"""Thumbnail cache invalidation — stages.review_gate._sync_thumbs.

Master 2026-07-26: a mid-story issue swap renumbers every LATER page, but build_candidates only
ever checked "does review/thumbs/pNNN_M.jpg exist" — never "is this still THAT page's art" — so a
stale thumb from the OLD numbering silently got reused under the new page's filename (a DIFFERENT
comic's panel showing up in the UI, plus orphan thumbs for pages that no longer exist at all).
_sync_thumbs invalidates (by source-image basename, recorded in thumbs/_src.json) + prunes
orphans BEFORE build_candidates (re)generates anything.
"""
import json

import stages.review_gate as rg


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")


def test_src_changed_deletes_stale_thumb_and_logs(tmp_path, capsys):
    thumbs = tmp_path / "thumbs"
    _touch(thumbs / "p041_0.jpg")
    (thumbs / "_src.json").write_text(json.dumps({"41": "ch03_page_09.jpg"}))

    pages = {41: {"source_image": "ch02_page_12.jpg"}}  # page 41 now points at DIFFERENT art
    rg._sync_thumbs(thumbs, pages, log=print)

    assert not (thumbs / "p041_0.jpg").exists()  # stale crop gone → regenerated lazily downstream
    out = capsys.readouterr().out
    assert "regenerated 1 page(s), removed 0 orphan(s)" in out
    assert json.loads((thumbs / "_src.json").read_text()) == {"41": "ch02_page_12.jpg"}


def test_src_unchanged_keeps_thumb_and_stays_silent(tmp_path, capsys):
    thumbs = tmp_path / "thumbs"
    _touch(thumbs / "p041_0.jpg")
    (thumbs / "_src.json").write_text(json.dumps({"41": "ch02_page_12.jpg"}))

    pages = {41: {"source_image": "ch02_page_12.jpg"}}  # same basename → cache hit
    rg._sync_thumbs(thumbs, pages, log=print)

    assert (thumbs / "p041_0.jpg").exists()
    assert capsys.readouterr().out == ""  # nothing changed → no log line


def test_orphan_page_removed_when_out_of_pool(tmp_path, capsys):
    thumbs = tmp_path / "thumbs"
    _touch(thumbs / "p097_0.jpg")
    (thumbs / "_src.json").write_text(json.dumps({"97": "ch05_page_02.jpg"}))

    pages = {}  # project shrank (30 pages -> 22) — page 97 no longer exists at all
    rg._sync_thumbs(thumbs, pages, log=print)

    assert not (thumbs / "p097_0.jpg").exists()
    out = capsys.readouterr().out
    assert "regenerated 0 page(s), removed 1 orphan(s)" in out
    assert json.loads((thumbs / "_src.json").read_text()) == {}


def test_missing_src_json_regenerates_all_without_raising(tmp_path, capsys):
    thumbs = tmp_path / "thumbs"
    _touch(thumbs / "p010_0.jpg")
    _touch(thumbs / "p010_1.jpg")
    # no _src.json on disk at all (first run after this fix ships, or file lost)

    pages = {10: {"source_image": "p10.png"}}
    rg._sync_thumbs(thumbs, pages, log=print)  # must not raise

    assert not (thumbs / "p010_0.jpg").exists()
    assert not (thumbs / "p010_1.jpg").exists()
    out = capsys.readouterr().out
    assert "regenerated 1 page(s), removed 0 orphan(s)" in out


def test_build_candidates_writes_src_json(tmp_path, monkeypatch):
    """End-to-end: build_candidates leaves review/thumbs/_src.json mapping each page to its
    CURRENT source-image basename. No PIL/real images needed — _write_thumb is monkeypatched,
    same pattern as the existing build_candidates tests in test_review_gate.py."""
    import stages.stage_5.shots as shots

    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    proj = tmp_path / "srcwrite"
    proj.mkdir()
    (proj / "narration.json").write_text(json.dumps({"scenes": [
        {"scene_id": 2, "text": "a beat", "page_ref": 10, "panel_ref": 0},
    ]}))
    prep = proj / "preprocessed"
    prep.mkdir()
    (prep / "page_010.json").write_text(json.dumps({
        "page_number": 10, "source_image": "ch01_page_10.jpg", "panels": [], "text_blocks": [],
    }))

    def fake_match(units, pages, cluster, *, project=None, candidates_out=None, candidates_k=12):
        for _ in units:
            candidates_out.append([])
        return []

    monkeypatch.setattr(shots, "_match_panels", fake_match)
    monkeypatch.setattr(rg, "_write_thumb", lambda *a, **k: True)

    rg.build_candidates("srcwrite", k=5)

    src = json.loads((proj / "review" / "thumbs" / "_src.json").read_text())
    assert src == {"10": "ch01_page_10.jpg"}
