"""The batcave scraper must ride out a flaky network instead of failing the chapter.

On the production box DNS answered only about half of its lookups ("Could not resolve host:
img.batcave.biz"). Every page got ONE attempt, so a chapter of 24 images almost always came
back with holes and the whole download read "incomplete" — and the screen only listed page
numbers, never the reason."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from curl_cffi import requests as cf_req
from curl_cffi.requests import exceptions as cf_exc

import utils.comic_scraper.readcomiconline as rco

READER = "https://batcave.biz/reader/1/2"
IMG = "https://img.batcave.biz/img/1/1/2/1-abc.jpg"
DNS_MSG = ("Failed to perform, curl: (6) Could not resolve host: img.batcave.biz. "
           "See https://curl.se/libcurl/c/libcurl-errors.html first for more details.")


def _dns_error():
    return cf_exc.DNSError(DNS_MSG, 6)


def _http_error(status):
    return cf_exc.HTTPError(f"HTTP Error {status}", 0, SimpleNamespace(status_code=status))


class _Resp:
    def __init__(self, status=200, content=b"jpeg", text=""):
        self.status_code, self.content, self.text = status, content, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _http_error(self.status_code)


class _Session:
    """Plays back a script: an Exception is raised, anything else is returned."""

    def __init__(self, *script):
        self.script, self.calls = list(script), 0

    def get(self, url, **kwargs):
        self.calls += 1
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    monkeypatch.setattr(rco.time, "sleep", lambda _s: None)


def _use(monkeypatch, session):
    monkeypatch.setattr(rco, "_get_session", lambda: session)
    return session


def test_a_dns_failure_is_retried_and_the_page_still_arrives(tmp_path, monkeypatch):
    sess = _use(monkeypatch, _Session(_dns_error(), _dns_error(), _Resp(content=b"page")))
    target = tmp_path / "ch01_page_01.jpg"

    assert rco._download_image(IMG, target) is True
    assert sess.calls == 3
    assert target.read_bytes() == b"page"


def test_a_timeout_is_retried_too(tmp_path, monkeypatch):
    sess = _use(monkeypatch, _Session(cf_exc.Timeout("Operation timed out", 28), _Resp()))

    assert rco._download_image(IMG, tmp_path / "p.jpg") is True
    assert sess.calls == 2


def test_a_missing_image_is_not_retried(tmp_path, monkeypatch):
    sess = _use(monkeypatch, _Session(_Resp(status=404)))

    assert rco._download_image(IMG, tmp_path / "p.jpg") is False
    assert sess.calls == 1
    assert "404" in rco.last_download_error()


def test_a_server_error_is_retried(tmp_path, monkeypatch):
    sess = _use(monkeypatch, _Session(_Resp(status=503), _Resp()))

    assert rco._download_image(IMG, tmp_path / "p.jpg") is True
    assert sess.calls == 2


def test_retrying_gives_up_after_a_few_attempts_and_keeps_the_reason(tmp_path, monkeypatch):
    sess = _use(monkeypatch, _Session(*[_dns_error() for _ in range(10)]))

    assert rco._download_image(IMG, tmp_path / "p.jpg") is False
    assert sess.calls == rco._ATTEMPTS
    reason = rco.last_download_error()
    assert "Could not resolve host" in reason
    assert "curl.se" not in reason, "the libcurl docs link is noise on a status line"


def test_success_clears_the_previous_failure_reason(tmp_path, monkeypatch):
    _use(monkeypatch, _Session(_Resp(status=404), _Resp()))
    rco._download_image(IMG, tmp_path / "a.jpg")
    assert rco.last_download_error()

    rco._download_image(IMG, tmp_path / "b.jpg")
    assert rco.last_download_error() == ""


def test_an_incomplete_chapter_says_why_its_pages_did_not_arrive(tmp_path, monkeypatch):
    monkeypatch.setattr(rco, "_fetch_data", lambda url: {"images": ["/img/1.jpg", "/img/2.jpg"]})
    _use(monkeypatch, _Session(_Resp(), *[_dns_error() for _ in range(rco._ATTEMPTS)]))

    with pytest.raises(RuntimeError) as caught:
        rco.scrape_issue_pages(READER, project_root=tmp_path, chapter_index=1)

    message = str(caught.value)
    assert "1 of 2 page(s) did not download" in message and "page(s) 2" in message
    assert "Could not resolve host" in message


def test_a_chapter_that_recovers_mid_download_completes(tmp_path, monkeypatch):
    monkeypatch.setattr(rco, "_fetch_data", lambda url: {"images": ["/img/1.jpg", "/img/2.jpg"]})
    _use(monkeypatch, _Session(_Resp(), _dns_error(), _Resp()))

    pages = rco.scrape_issue_pages(READER, project_root=tmp_path, chapter_index=1)

    assert [p.name for p in pages] == ["ch01_page_01.jpg", "ch01_page_02.jpg"]


def test_the_reader_page_fetch_is_retried(monkeypatch):
    html = 'x window.__DATA__ = {"images": ["/a.jpg"]}; y'
    sess = _use(monkeypatch, _Session(_dns_error(), _Resp(text=html)))

    assert rco._fetch_data(READER) == {"images": ["/a.jpg"]}
    assert sess.calls == 2


def test_a_permanent_reader_error_is_still_raised_to_the_caller(monkeypatch):
    sess = _use(monkeypatch, _Session(*[_dns_error() for _ in range(10)]))

    with pytest.raises(cf_req.RequestsError):
        rco._fetch_data(READER)
    assert sess.calls == rco._ATTEMPTS


# ── a network that is down must not make the download hang for an hour ───────


def test_a_dead_network_stops_the_chapter_after_a_few_pages(tmp_path, monkeypatch):
    n_pages = 12
    monkeypatch.setattr(rco, "_fetch_data",
                        lambda url: {"images": [f"/img/{i}.jpg" for i in range(1, n_pages + 1)]})
    sess = _use(monkeypatch, _Session(*[_dns_error() for _ in range(500)]))

    with pytest.raises(RuntimeError) as caught:
        rco.scrape_issue_pages(READER, project_root=tmp_path, chapter_index=1)

    assert sess.calls == rco._GIVE_UP_AFTER * rco._ATTEMPTS, "kept hammering a dead network"
    message = str(caught.value)
    assert f"{n_pages} of {n_pages} page(s) did not download" in message
    assert "Could not resolve host" in message and "network looks down" in message


def test_pages_that_are_permanently_missing_do_not_stop_the_chapter(tmp_path, monkeypatch):
    """Five 404s in a row are holes in the comic, not an outage: the pages after them must
    still be fetched, or they would never arrive on any retry."""
    monkeypatch.setattr(rco, "_fetch_data",
                        lambda url: {"images": [f"/img/{i}.jpg" for i in range(1, 9)]})
    sess = _use(monkeypatch, _Session(*[_Resp(status=404)] * 5, *[_Resp()] * 3))

    with pytest.raises(RuntimeError) as caught:
        rco.scrape_issue_pages(READER, project_root=tmp_path, chapter_index=1)

    assert sess.calls == 8
    assert "5 of 8 page(s) did not download" in str(caught.value)
    assert "network looks down" not in str(caught.value)
    assert len(list((tmp_path / "raw_comic").glob("ch01_*"))) == 3
