import json

import pytest
from PIL import Image

import stages.stage_2.pipeline as pipeline
from stages.stage_2.cache import image_hash, save_cached


def _page(pn, label, image_path, *, page_type="story"):
    return {
        "page_number": pn,
        "issue_label": label,
        "source_image": str(image_path.resolve()),
        "content_hash": image_hash(image_path),
        "page_type": page_type,
        "is_story_page": page_type == "story",
        "skip_reason": "",
        "panels": [],
        "text_blocks": [],
        "page_summary": f"cached page {pn}",
    }


def test_start_page_skips_earlier_uncached_magi_and_preserves_global_classification(
        tmp_path, monkeypatch):
    import stages.stage_2.panel_detect as panel_detect

    root = tmp_path / "project"
    raw = root / "raw_comic"
    raw.mkdir(parents=True)
    image_paths = []
    for pn in range(1, 21):
        path = tmp_path / f"p{pn:03d}.png"
        Image.new("RGB", (12, 18), color=(pn, 0, 0)).save(path)
        image_paths.append(path)
    manifest = [
        {"label": "Issue #1", "pages": [str(p) for p in image_paths[:12]]},
        {"label": "Issue #2", "pages": [str(p) for p in image_paths[12:]]},
    ]
    (raw / "manifest.json").write_text(json.dumps(manifest))

    earlier = _page(4, "Issue #1", image_paths[3], page_type="cover")
    cached_path = save_cached(root, 4, earlier["content_hash"], earlier)
    original_cache_bytes = cached_path.read_bytes()
    monkeypatch.setattr(pipeline, "get_project_dirs", lambda _name: {"root": root})
    monkeypatch.setattr(pipeline, "_run_identity_precheck", lambda *_a: None)
    monkeypatch.setattr(pipeline, "_load_story_context", lambda *_a: "")
    monkeypatch.setattr(pipeline, "_resolve_clusters_after_preprocess", lambda *_a: None)
    monkeypatch.setattr(pipeline, "_write_panel_viz", lambda *_a: None)
    monkeypatch.setattr(pipeline, "_run_identity_repair", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "_verify_pending_concurrently", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "VLM_EXTRACT", True)
    monkeypatch.setattr(pipeline, "VLM_BATCH_SIZE", 8)
    monkeypatch.setattr(pipeline, "CLUSTER_NAMER", False)
    monkeypatch.setattr("config.MAGI_BATCH_SIZE", 4)
    monkeypatch.setattr(panel_detect, "release_model", lambda: None)

    magi_calls = []
    def fake_detect_batch(paths, *, batch_size, log):
        magi_calls.extend(paths)
        return [{"panels": [{"bbox": {"x": 0, "y": 0, "w": 12, "h": 18}}],
                 "characters": [{"bbox": {"x": 1, "y": 1, "w": 3, "h": 3}}], "texts": []}
                for _ in paths]
    monkeypatch.setattr(panel_detect, "detect_full_batch", fake_detect_batch)

    vlm_calls = []
    def fake_extract_batch(paths, _panels, **_kwargs):
        vlm_calls.extend(paths)
        return ([{"page_summary": "selected page"} for _ in paths], "state", "test-vlm")
    monkeypatch.setattr(pipeline, "extract_pages_batch", fake_extract_batch)

    assembled = []
    singles = []
    def page_dict(*, page_number, issue_label, image_path, content_hash, **_kwargs):
        assembled.append(page_number)
        return _page(page_number, issue_label, image_path)
    monkeypatch.setattr(pipeline, "_assemble_page_dict", page_dict)
    def single_page(*, page_number, issue_label, image_path, content_hash, **_kwargs):
        singles.append(page_number)
        return _page(page_number, issue_label, image_path)
    monkeypatch.setattr(pipeline, "_build_page_from_single", single_page)

    results = pipeline.preprocess_project("p", progress=lambda _line: None, start_page=9)

    assert {p.name for p in magi_calls} == {p.name for p in image_paths[8:]}
    assert image_paths[8] in vlm_calls
    assert all(path in image_paths[8:] for path in vlm_calls)
    preserved = next(p for p in results if p["page_number"] == 4)
    assert preserved["page_type"] == "cover"
    assert 9 in assembled and 9 not in singles  # page 9 is middle-document, not a new page 1
    assert cached_path.read_bytes() == original_cache_bytes


@pytest.mark.parametrize("start_page", [0, -1, True, "2", 1.5])
def test_preprocess_rejects_non_positive_or_non_integer_start_page(tmp_path, monkeypatch, start_page):
    root = tmp_path / "project"
    raw = root / "raw_comic"
    raw.mkdir(parents=True)
    (raw / "manifest.json").write_text("[]")
    monkeypatch.setattr(pipeline, "get_project_dirs", lambda _name: {"root": root})
    with pytest.raises(ValueError, match="positive integer"):
        pipeline.preprocess_project("p", start_page=start_page)


def test_preprocess_rejects_start_page_beyond_last_downloaded_page(tmp_path, monkeypatch):
    root = tmp_path / "project"
    raw = root / "raw_comic"
    raw.mkdir(parents=True)
    page = tmp_path / "one.png"
    Image.new("RGB", (12, 18)).save(page)
    (raw / "manifest.json").write_text(json.dumps([{"label": "Issue #1", "pages": [str(page)]}]))
    monkeypatch.setattr(pipeline, "get_project_dirs", lambda _name: {"root": root})
    with pytest.raises(ValueError, match="exceeds last downloaded page 1"):
        pipeline.preprocess_project("p", start_page=2)


def test_run_stage_2_forwards_start_page(monkeypatch):
    from ui import bridge

    calls = []
    monkeypatch.setattr("stages.stage_2.preprocess_project",
                        lambda *args, **kwargs: calls.append((args, kwargs)) or [])
    bridge.run_stage_2("p", lambda _line: None, start_page=7)
    assert calls[0][1]["start_page"] == 7


def test_preprocess_screen_exposes_global_start_page_and_validates_before_running(monkeypatch):
    import flet as ft
    import ui
    import ui.screens.s2_preprocess as screen
    from tests.ui_test_doubles import StrictFakePage
    from ui.state import AppState

    monkeypatch.setattr(screen, "load_preprocessed", lambda _project: [])
    page = StrictFakePage()
    root = screen.build(page, AppState(project_name="p"), on_go=lambda _stage: None,
                        on_state_change=lambda: None)

    def walk(control):
        if control is None:
            return
        yield control
        for child in getattr(control, "controls", []) or []:
            yield from walk(child)
        content = getattr(control, "content", None)
        if isinstance(content, ft.Control):
            yield from walk(content)

    field = next(c for c in walk(root) if isinstance(c, ft.TextField))
    assert field.label == "Start at page" and field.value == "1"
    field.value = "0"
    run_button = next(c for c in walk(root) if isinstance(c, ft.ElevatedButton)
                      and c.content == "Run Preprocessing")
    run_button.on_click(None)
    assert page.tasks == []
    assert "positive whole page number" in field.error_text
