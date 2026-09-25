"""Stage 2 must hand later stages exactly the comics the items cite, in item order.

A chapter that failed, came back empty, or still holds a previous comic's pages used to be
recorded (or skipped) silently, so a later paragraph landed on the wrong comic with no
error anywhere. These tests pin the fail-loud behaviour. No network."""
import json
from pathlib import Path

import pytest

import stages.stage_2.cache as cache
import stages.stage_2.pipeline as pipe
import stages.stage_2.url_mode as um
import utils.comic_scraper.readcomiconline as rco
from stages._arc import qa_item_chapters
from stages.user_errors import UserFacingError

U1, U2, U3 = ("https://batcave.biz/reader/1/11", "https://batcave.biz/reader/2/22",
              "https://batcave.biz/reader/3/33")


# ── item → chapter: one rule, shared by download and narration ───────────────


def test_item_chapters_follow_item_order():
    assert qa_item_chapters([U1, U2, U3]) == [1, 2, 3]


def test_items_citing_the_same_url_share_the_first_items_chapter():
    assert qa_item_chapters([U1, U2, U1, U3]) == [1, 2, 1, 4]


def test_download_readers_only_labels_chapters_by_that_rule(tmp_path, monkeypatch):
    monkeypatch.setattr(um, "_ensure_project_root", lambda name: tmp_path)
    monkeypatch.setattr(um, "scrape_issue_pages",
                        lambda url, project_root, chapter_index: [f"ch{chapter_index:02d}_page_01.jpg"])
    um.download_readers_only("p", [U1, U2, U1, U3], progress=lambda m: None)
    manifest = json.loads((tmp_path / "raw_comic" / "manifest.json").read_text())
    assert [(m["chapter_index"], m["reader_url"]) for m in manifest] == [(1, U1), (2, U2), (4, U3)]


# ── a failed or empty chapter stops the download loudly ─────────────────────


def _fake_scrape_factory(fail=(), empty=()):
    def _scrape(url, project_root, chapter_index):
        if url in fail:
            raise RuntimeError("reader blocked")
        if url in empty:
            return []
        return [f"{project_root}/raw_comic/ch{chapter_index:02d}_page_01.jpg"]
    return _scrape


def test_failed_chapter_raises_after_keeping_the_others(tmp_path, monkeypatch):
    monkeypatch.setattr(um, "_ensure_project_root", lambda name: tmp_path)
    monkeypatch.setattr(um, "scrape_issue_pages", _fake_scrape_factory(fail={U2}))
    with pytest.raises(UserFacingError, match="#2"):
        um.download_readers_only("p", [U1, U2, U3], progress=lambda m: None)
    manifest = json.loads((tmp_path / "raw_comic" / "manifest.json").read_text())
    assert [m["chapter_index"] for m in manifest] == [1, 3]   # progress kept for the retry


def test_empty_chapter_counts_as_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(um, "_ensure_project_root", lambda name: tmp_path)
    monkeypatch.setattr(um, "scrape_issue_pages", _fake_scrape_factory(empty={U3}))
    with pytest.raises(UserFacingError, match="#3"):
        um.download_readers_only("p", [U1, U2, U3], progress=lambda m: None)


def test_stage1_series_download_uses_the_same_fail_loud_loop(tmp_path, monkeypatch):
    import stages.stage_2.download as dl
    monkeypatch.setattr(dl, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(dl, "get_project_dirs", lambda name: {"root": tmp_path / name})
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "comic_context.json").write_text(
        json.dumps({"batcave_url": "https://batcave.biz/1-some-series.html", "issues": ""}))
    monkeypatch.setattr(dl, "resolve_chapters", lambda url, issues: [
        {"label": "#1", "reader_url": U1}, {"label": "#2", "reader_url": U2}])
    monkeypatch.setattr(um, "scrape_issue_pages", _fake_scrape_factory(fail={U2}))
    with pytest.raises(UserFacingError, match="#2"):
        dl.download_comic("p", progress=lambda m: None)
    manifest = json.loads((tmp_path / "p" / "raw_comic" / "manifest.json").read_text())
    assert [m["chapter_index"] for m in manifest] == [1]


def test_chapter_now_pointing_at_another_comic_drops_its_cached_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(um, "_ensure_project_root", lambda name: tmp_path)
    raw = tmp_path / "raw_comic"
    raw.mkdir()
    for ch in (1, 2):
        for k in (1, 2, 3):
            (raw / f"ch{ch:02d}_page_{k:02d}.jpg").write_bytes(b"old")
    (raw / "manifest.json").write_text(json.dumps([
        {"chapter_index": 1, "label": "#1", "reader_url": U1, "pages": []},
        {"chapter_index": 2, "label": "#2", "reader_url": "https://batcave.biz/reader/9/99",
         "pages": []},
    ]))
    seen_on_disk = {}

    def _scrape(url, project_root, chapter_index):
        seen_on_disk[chapter_index] = sorted(p.name for p in raw.glob(f"ch{chapter_index:02d}_*"))
        return [str(raw / f"ch{chapter_index:02d}_page_01.jpg")]

    monkeypatch.setattr(um, "scrape_issue_pages", _scrape)
    um.download_readers_only("p", [U1, U2], progress=lambda m: None)
    assert len(seen_on_disk[1]) == 3        # same comic as before → cache kept
    assert seen_on_disk[2] == []            # different comic → old pages gone before scraping


# ── the scraper never returns a partial or stale chapter as if complete ──────


def _fake_reader(monkeypatch, n_pages, fail_pages=()):
    monkeypatch.setattr(rco, "_fetch_data", lambda url: {"images": [f"/img/{i}.jpg" for i in range(1, n_pages + 1)]})

    def _dl(url, path):
        idx = int(url.rsplit("/", 1)[1].split(".")[0])
        if idx in fail_pages:
            return False
        Path(path).write_bytes(b"img")
        return True

    monkeypatch.setattr(rco, "_download_image", _dl)
    monkeypatch.setattr(rco.time, "sleep", lambda s: None)


def test_scraper_raises_when_a_page_did_not_download(tmp_path, monkeypatch):
    _fake_reader(monkeypatch, 4, fail_pages={3})
    with pytest.raises(RuntimeError, match="3"):
        rco.scrape_issue_pages(U1, project_root=tmp_path, chapter_index=1)
    # the pages that did arrive stay cached, so a retry only fetches page 3
    assert sorted(p.name for p in (tmp_path / "raw_comic").glob("ch01_*")) == [
        "ch01_page_01.jpg", "ch01_page_02.jpg", "ch01_page_04.jpg"]


def test_scraper_cache_returns_exactly_this_chapters_pages_in_reading_order(tmp_path, monkeypatch):
    _fake_reader(monkeypatch, 3)
    raw = tmp_path / "raw_comic"
    raw.mkdir()
    for k in (1, 2, 3, 4, 5):                       # a longer, older chapter's leftovers
        (raw / f"ch01_page_{k:02d}.jpg").write_bytes(b"x")
    pages = rco.scrape_issue_pages(U1, project_root=tmp_path, chapter_index=1)
    assert [p.name for p in pages] == ["ch01_page_01.jpg", "ch01_page_02.jpg", "ch01_page_03.jpg"]


def test_scraper_keeps_numeric_order_past_page_99(tmp_path, monkeypatch):
    _fake_reader(monkeypatch, 101)
    pages = rco.scrape_issue_pages(U1, project_root=tmp_path, chapter_index=1)
    assert pages[98].name == "ch01_page_99.jpg" and pages[99].name == "ch01_page_100.jpg"


# ── preprocess cache: current labels, no leftovers from an earlier download ──


def test_cache_hit_takes_the_pages_current_chapter_label(tmp_path):
    img = tmp_path / "raw_comic" / "ch03_page_01.jpg"
    img.parent.mkdir()
    img.write_bytes(b"x")
    cached = {"page_number": 7, "issue_label": "#2",
              "source_image": str(tmp_path / "raw_comic" / "ch02_page_01.jpg")}
    changed = pipe._refresh_cached_identity(cached, label="#3", image_path=img)
    assert changed is True
    assert cached["issue_label"] == "#3"
    assert cached["source_image"] == str(img.resolve())
    assert pipe._refresh_cached_identity(cached, label="#3", image_path=img) is False


def test_prune_removes_page_json_not_in_the_current_download(tmp_path):
    prep = tmp_path / "preprocessed"
    prep.mkdir()
    keep = cache.cache_path(tmp_path, 1, "a" * 16)
    stale_same_number = prep / f"page_001_{'b' * 16}.json"
    stale_extra = prep / f"page_009_{'c' * 16}.json"
    other = prep / "cluster_notes.json"
    for f in (keep, stale_same_number, stale_extra, other):
        f.write_text("{}")
    removed = pipe._prune_stale_page_cache(tmp_path, [{"pn": 1, "hash": "a" * 16}], log=lambda m: None)
    assert removed == 2
    assert keep.exists() and other.exists()
    assert not stale_same_number.exists() and not stale_extra.exists()


# ─── predictable download errors read as messages, not tracebacks ───────────
# The Download Comic screen prints these to its status/log; plain exceptions come out
# as a traceback with server paths. Each keeps the builtin its callers already catch.

def _dl_project(tmp_path, monkeypatch, context):
    import stages.stage_2.download as dl
    monkeypatch.setattr(dl, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(dl, "get_project_dirs", lambda name: {"root": tmp_path / name})
    (tmp_path / "p").mkdir()
    if context is not None:
        (tmp_path / "p" / "comic_context.json").write_text(json.dumps(context))
    return dl


def test_download_without_a_project_context_names_the_research_step(tmp_path, monkeypatch):
    dl = _dl_project(tmp_path, monkeypatch, None)
    with pytest.raises(FileNotFoundError) as caught:
        dl.download_comic("p", progress=lambda m: None)
    assert isinstance(caught.value, UserFacingError)
    assert "Research Scout" in str(caught.value)


def test_a_project_with_no_comic_url_points_at_download_from_urls(tmp_path, monkeypatch):
    """A project made with Download from URL(s) has no Stage 1 series link, and the
    button right above it — Download (from Stage 1) — is still enabled."""
    dl = _dl_project(tmp_path, monkeypatch, {"issues": ""})
    with pytest.raises(ValueError) as caught:
        dl.download_comic("p", progress=lambda m: None)
    assert isinstance(caught.value, UserFacingError)
    assert "Download from URL(s)" in str(caught.value)


def test_a_series_link_that_matches_no_issues_says_what_to_check(tmp_path, monkeypatch):
    dl = _dl_project(tmp_path, monkeypatch,
                     {"batcave_url": "https://batcave.biz/1-some-series.html", "issues": "40-45"})
    monkeypatch.setattr(dl, "resolve_chapters", lambda url, issues: [])
    with pytest.raises(RuntimeError) as caught:
        dl.download_comic("p", progress=lambda m: None)
    assert isinstance(caught.value, UserFacingError)
    assert "40-45" in str(caught.value) and "issue numbers" in str(caught.value)


def test_url_mode_explains_a_link_that_is_not_a_series_page_and_an_empty_series(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(um, "_ensure_project_root", lambda name: tmp_path)
    with pytest.raises(ValueError) as not_series:
        um.download_from_series("p", "https://example.com/some-page", progress=lambda m: None)
    assert isinstance(not_series.value, UserFacingError)

    monkeypatch.setattr(um, "resolve_chapters", lambda url, issues: [])
    with pytest.raises(RuntimeError) as empty:
        um.download_from_series("p", "https://batcave.biz/1-some-series.html", "7",
                                enrich=False, progress=lambda m: None)
    assert isinstance(empty.value, UserFacingError)
    with pytest.raises(RuntimeError) as empty_saga:
        um.download_saga("p", "https://batcave.biz/1-some-series.html", progress=lambda m: None)
    assert isinstance(empty_saga.value, UserFacingError)


def test_url_direct_download_into_a_qa_project_fills_its_items_missing_urls(tmp_path, monkeypatch):
    """Download from URL(s) into a Q&A project wrote only comic_context.json and the
    manifest, so the items created without a reader URL stayed "missing" and the
    narration step refused the chapters it had just downloaded for them."""
    import stages.stage_2.download as dl
    import ui.bridge as bridge

    monkeypatch.setattr(bridge, "PROJECTS_ROOT", tmp_path)
    root = tmp_path / "p"
    (root / "raw_comic").mkdir(parents=True)
    (root / "comic_context.json").write_text("{}")
    (root / "answer_context.json").write_text(json.dumps({"items": [
        {"entity": "A", "reader_url": U1}, {"entity": "B", "reader_url": ""}]}))
    manifest = root / "raw_comic" / "manifest.json"

    def _download(project_name, urls, *, enrich, progress):
        manifest.write_text(json.dumps([
            {"chapter_index": n, "label": f"#{n}", "reader_url": u, "pages": []}
            for n, u in enumerate(urls, start=1)]))

    monkeypatch.setattr(um, "download_from_readers", _download)
    monkeypatch.setattr(dl, "load_manifest", lambda name: json.loads(manifest.read_text()))

    bridge.run_stage_download_from_url("p", f"{U1}\n{U2}", "", False, lambda m: None)

    answer = json.loads((root / "answer_context.json").read_text())
    assert [it["reader_url"] for it in answer["items"]] == [U1, U2]
    assert answer["unresolved_reader_urls"] == []
