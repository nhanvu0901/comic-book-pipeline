"""Panel-walk narrator: panel↔sentence is 1:1 by construction, and the VLM guard bites.

The whole point of this mode is that no matcher runs, so the invariant worth locking is that
every narrated scene carries the exact (page, panel) it was written from, in reading order.
"""
import json

import pytest

import stages.panel_walk.narrate as pw


def _panel(idx: int, desc: str, dialog=(), chars=()) -> dict:
    return {
        "index": idx,
        "bbox": {"x": 0, "y": idx * 10, "w": 100, "h": 100},
        "description": desc,
        "characters": list(chars),
        "dialog": [{"text": d} for d in dialog],
    }


def _page(n: int, panels: list[dict], story: bool = True) -> dict:
    return {
        "page_number": n, "is_story_page": story, "issue_label": "#1",
        "source_image": f"/tmp/p{n}.jpg",
        "image_dimensions": {"width": 100, "height": 200},
        "panels": panels, "text_blocks": [], "page_summary": "",
    }


def _project(tmp_path, monkeypatch, pages: list[dict], slug: str = "pw") -> str:
    monkeypatch.setattr(pw, "PROJECTS_ROOT", tmp_path)
    root = tmp_path / slug
    (root / "preprocessed").mkdir(parents=True)
    for pg in pages:
        (root / "preprocessed" / f"page_{pg['page_number']:03d}_x.json").write_text(
            json.dumps(pg))
    (root / "comic_context.json").write_text(json.dumps({"title": "Test Comic"}))
    return slug


def _fake_llm(monkeypatch, *, fail_pages=()):
    """One sentence per panel, tagged so the test can prove which panel it came from."""
    calls = []

    def fake(*, system, user, max_tokens, progress, label, validator=None, models=None):
        page = int(label.split("p")[-1])
        if page in fail_pages:
            raise RuntimeError("model down")
        n = user.count("PANEL ")
        calls.append((page, n, "LAST SENTENCES" in user))
        out = json.dumps({"sentences": [f"p{page} panel {i} narrated." for i in range(n)]})
        assert validator is None or validator(out), "fake output must satisfy the validator"
        return out, "fake-model"

    monkeypatch.setattr(pw, "call_with_chain", fake)
    return calls


def test_every_scene_maps_to_its_own_panel_in_reading_order(tmp_path, monkeypatch):
    pages = [
        _page(1, [_panel(0, "a hero stands"), _panel(1, "he turns")]),
        _page(2, [_panel(0, "a door opens"), _panel(1, "smoke"), _panel(2, "he runs")]),
    ]
    slug = _project(tmp_path, monkeypatch, pages)
    _fake_llm(monkeypatch)

    nar = pw.build_narration(slug)
    body = [s for s in nar.scenes if not s.is_intro]

    assert len(body) == 5, "one scene per panel, no merging or dropping"
    assert [(s.page_ref, s.panel_ref) for s in body] == [(1, 0), (1, 1), (2, 0), (2, 1), (2, 2)]
    # the sentence a scene carries must be the one written for THAT panel
    for s in body:
        assert s.text == f"p{s.page_ref} panel {s.panel_ref} narrated."
    assert all(s.word_count == len(s.text.split()) for s in body)
    assert not any(s.is_outro for s in nar.scenes), "this mode has no outro by design"


def test_one_llm_call_per_page_and_context_is_threaded(tmp_path, monkeypatch):
    pages = [_page(1, [_panel(0, "x")]), _page(2, [_panel(0, "y")]), _page(3, [_panel(0, "z")])]
    slug = _project(tmp_path, monkeypatch, pages)
    calls = _fake_llm(monkeypatch)

    pw.build_narration(slug)

    assert [c[0] for c in calls] == [1, 2, 3], "one call per page, in page order"
    assert calls[0][2] is False, "first page has no prior sentences"
    assert all(c[2] for c in calls[1:]), "later pages must receive the running context"


def test_non_story_pages_are_skipped(tmp_path, monkeypatch):
    pages = [_page(1, [_panel(0, "cover art")], story=False), _page(2, [_panel(0, "story")])]
    slug = _project(tmp_path, monkeypatch, pages)
    _fake_llm(monkeypatch)

    body = [s for s in pw.build_narration(slug).scenes if not s.is_intro]
    assert [s.page_ref for s in body] == [2]


def test_a_failed_page_is_skipped_not_fatal(tmp_path, monkeypatch):
    pages = [_page(1, [_panel(0, "a")]), _page(2, [_panel(0, "b")]), _page(3, [_panel(0, "c")])]
    slug = _project(tmp_path, monkeypatch, pages)
    _fake_llm(monkeypatch, fail_pages=(2,))

    body = [s for s in pw.build_narration(slug).scenes if not s.is_intro]
    assert [s.page_ref for s in body] == [1, 3], "one dead page must not kill a 90-page walk"


def test_magi_only_descriptions_are_refused(tmp_path, monkeypatch):
    """VLM_EXTRACT=0 leaves a placeholder description — narrating it would ship nonsense."""
    pages = [_page(1, [_panel(0, "a hero stands"),
                       _panel(1, "Wordless transition/SFX panel")])]
    slug = _project(tmp_path, monkeypatch, pages)
    _fake_llm(monkeypatch)

    with pytest.raises(RuntimeError, match="VLM_EXTRACT=1"):
        pw.build_narration(slug)


def test_all_pages_failing_raises(tmp_path, monkeypatch):
    pages = [_page(1, [_panel(0, "a")]), _page(2, [_panel(0, "b")])]
    slug = _project(tmp_path, monkeypatch, pages)
    _fake_llm(monkeypatch, fail_pages=(1, 2))

    with pytest.raises(RuntimeError, match="no narration"):
        pw.build_narration(slug)
