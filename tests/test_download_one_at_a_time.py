"""One download per project at a time.

The production log of one project shows three downloads (the Stage 1 button and URL-direct, one
of them twice) running at once on the same raw_comic/. Each wrote its own comic into the same
chNN_page_*.jpg names, and a page already on disk counts as cached — so the folder ended up
holding pages of two different comics, and the concurrent fetches slowed each other down."""
from __future__ import annotations

import pytest

import stages.stage_2.url_mode as um
import ui.bridge as bridge

URL = "https://batcave.biz/reader/1/2"


@pytest.fixture
def quiet(monkeypatch, tmp_path):
    import stages.stage_2.download as dl
    monkeypatch.setattr(bridge, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(bridge, "_adopt_item_reader_urls", lambda project, log: None)
    monkeypatch.setattr(dl, "load_manifest", lambda name: [])


def test_a_second_download_of_the_same_project_is_refused_while_one_runs(monkeypatch, quiet):
    refused = []

    def _download(project, urls, *, enrich, progress):
        for attempt in (
            lambda: bridge.run_stage_download_from_url(project, URL, "", False, print),
            lambda: bridge.run_stage_download_saga(project, URL, 3, print),
            lambda: bridge.run_stage_download(project, print),
        ):
            with pytest.raises(bridge.DownloadInProgress) as caught:
                attempt()
            refused.append(str(caught.value))

    monkeypatch.setattr(um, "download_from_readers", _download)

    bridge.run_stage_download_from_url("p", URL, "", False, print)

    assert len(refused) == 3
    assert all("'p'" in message and "already running" in message for message in refused)


def test_another_project_may_download_at_the_same_time(monkeypatch, quiet):
    seen = []

    def _download(project, urls, *, enrich, progress):
        seen.append(project)
        if project == "p":
            bridge.run_stage_download_from_url("q", URL, "", False, print)

    monkeypatch.setattr(um, "download_from_readers", _download)

    bridge.run_stage_download_from_url("p", URL, "", False, print)

    assert seen == ["p", "q"]


def test_the_guard_is_released_when_a_download_fails(monkeypatch, quiet):
    calls = []

    def _download(project, urls, *, enrich, progress):
        calls.append(project)
        raise RuntimeError("Download incomplete")

    monkeypatch.setattr(um, "download_from_readers", _download)

    for _ in range(2):
        with pytest.raises(RuntimeError, match="Download incomplete"):
            bridge.run_stage_download_from_url("p", URL, "", False, print)

    assert calls == ["p", "p"], "a failed run left the project locked for the retry"
