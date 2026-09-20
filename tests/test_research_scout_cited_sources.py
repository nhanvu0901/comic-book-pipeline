"""Fetching the pages a candidate cited, for the evidence gate to read.

The gate is told to check "every cited URL". Nothing ever retrieved them, so
any candidate citing CBR or Fandom failed by construction. These are the rules
the retrieval has to keep: a hard cap on how much it costs, and — above all —
a failure that comes back as DATA. A gate worker that dies takes the whole
candidate's verdict with it.
"""

import json
import threading
import urllib.error

import pytest

from stages.research_scout import cited_sources as cs


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ─── Which URLs, and how many ───────────────────────────────────────────────

def test_cited_urls_reads_both_citation_fields_in_order():
    urls = cs.cited_urls(
        {
            "evidence_urls": ["https://cbr.com/a", "https://cbr.com/b"],
            "source_urls": ["https://marvel.fandom.com/c"],
        }
    )
    assert urls == ["https://cbr.com/a", "https://cbr.com/b", "https://marvel.fandom.com/c"]


def test_cited_urls_drops_duplicates_blanks_and_non_http():
    urls = cs.cited_urls(
        {
            "evidence_urls": ["https://cbr.com/a", "  ", "https://cbr.com/a", 7, "ftp://x/y"],
            "source_urls": "https://cbr.com/a",
        }
    )
    assert urls == ["https://cbr.com/a"]


def test_at_most_three_cited_urls_are_ever_fetched():
    urls = cs.cited_urls({"evidence_urls": [f"https://cbr.com/{i}" for i in range(9)]})
    assert urls == [
        "https://cbr.com/0",
        "https://cbr.com/1",
        "https://cbr.com/2",
    ]
    assert cs.MAX_CITED_URLS == 3


def test_a_candidate_that_cited_nothing_asks_for_nothing():
    assert cs.cited_urls({"title": "no citations here"}) == []
    assert cs.cited_urls("not a mapping at all") == []


def test_bound_claim_citation_is_fetched_before_supplementary_urls():
    candidate = {
        "claim_citation": {
            "url": "https://cbr.com/the-claim",
            "quote": "The exact published sentence.",
        },
        "evidence_urls": ["https://cbr.com/secondary"],
    }

    assert cs.cited_urls(candidate) == [
        "https://cbr.com/the-claim",
        "https://cbr.com/secondary",
    ]


def test_quote_match_normalizes_whitespace_and_typographic_quotes():
    citation = cs.claim_citation({
        "claim_citation": {
            "url": "https://cbr.com/the-claim",
            "quote": "Shuri’s  scan\nshows  necrotic cells.",
        }
    })
    source = cs.FetchedSource(
        url="https://cbr.com/the-claim",
        text="Intro. Shuri's scan shows necrotic cells. Outro.",
    )

    assert citation is not None
    assert cs.quote_matches_source(citation, source)


def test_quote_match_requires_the_bound_url_and_the_actual_quote():
    citation = cs.claim_citation({
        "claim_citation": {
            "url": "https://cbr.com/the-claim",
            "quote": "A sentence the source never says.",
        }
    })
    wrong_url = cs.FetchedSource(
        url="https://cbr.com/other",
        text="A sentence the source never says.",
    )
    silent_source = cs.FetchedSource(
        url="https://cbr.com/the-claim",
        text="A different sentence.",
    )

    assert citation is not None
    assert not cs.quote_matches_source(citation, wrong_url)
    assert not cs.quote_matches_source(citation, silent_source)


def test_quote_match_compares_visible_markdown_text_not_link_markup():
    citation = cs.claim_citation({
        "claim_citation": {
            "url": "https://cbr.com/the-claim",
            "quote": "Thor defeats Loki in Thor #1 (2024).",
        }
    })
    source = cs.FetchedSource(
        url="https://cbr.com/the-claim",
        text=(
            "**Thor** defeats [Loki](https://example.test/issue_(2024)) in "
            "[Thor #1](https://example.test/thor/1) (2024)."
        ),
    )
    fabricated = cs.ClaimCitation(
        url="https://cbr.com/the-claim",
        quote="example.test/issue (2024)",
    )

    assert citation is not None
    assert cs.quote_matches_source(citation, source)
    assert not cs.quote_matches_source(fabricated, source)


def test_citation_fingerprint_ignores_tracking_and_quote_presentation():
    first = cs.claim_citation({
        "claim_citation": {
            "url": "HTTPS://CBR.COM/the-claim/?utm_source=scout#section",
            "quote": "Shuri’s scan  shows necrotic cells.",
        }
    })
    second = cs.claim_citation({
        "claim_citation": {
            "url": "https://cbr.com/the-claim",
            "quote": "Shuri's scan shows necrotic cells.",
        }
    })

    assert first is not None and second is not None
    assert cs.citation_fingerprint(first) == cs.citation_fingerprint(second)


# ─── The fetch itself ───────────────────────────────────────────────────────

def test_fetch_goes_through_the_jina_reader_with_a_ten_second_timeout(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return _FakeResp(b"Black Panther vs. Deadpool #2 opens on Shuri's scan.")

    monkeypatch.setattr(cs.urllib.request, "urlopen", fake_urlopen)
    fetched = cs.fetch_source("https://www.cbr.com/deadpools-healing-factor-redefined/")

    assert seen["url"] == (
        "https://r.jina.ai/https://www.cbr.com/deadpools-healing-factor-redefined/"
    )
    assert seen["timeout"] == 10
    assert cs.FETCH_TIMEOUT == 10
    assert fetched.ok
    assert fetched.url == "https://www.cbr.com/deadpools-healing-factor-redefined/"
    assert "Shuri's scan" in fetched.text
    assert fetched.error == ""


def test_a_fetched_page_is_truncated_to_six_thousand_characters(monkeypatch):
    monkeypatch.setattr(
        cs.urllib.request, "urlopen", lambda request, timeout=None: _FakeResp(b"x" * 20000)
    )
    fetched = cs.fetch_source("https://cbr.com/long")

    assert cs.MAX_SOURCE_CHARS == 6000
    assert len(fetched.text) == 6000


@pytest.mark.parametrize(
    "boom, expected",
    [
        (lambda: (_ for _ in ()).throw(TimeoutError("slow")), "timeout"),
        (
            lambda: (_ for _ in ()).throw(
                urllib.error.HTTPError("https://cbr.com/x", 403, "Forbidden", None, None)
            ),
            "HTTP 403",
        ),
        (lambda: (_ for _ in ()).throw(urllib.error.URLError("dns")), "URLError"),
        (lambda: (_ for _ in ()).throw(RuntimeError("something nobody predicted")), "RuntimeError"),
    ],
)
def test_a_failing_fetch_is_data_and_never_an_exception(monkeypatch, boom, expected):
    """A gate worker that raises loses the candidate's whole verdict, and the
    surrounding code reads an exception as "this branch failed". Not being able
    to read a page is a finding, not a defect."""
    monkeypatch.setattr(cs.urllib.request, "urlopen", lambda request, timeout=None: boom())
    fetched = cs.fetch_source("https://cbr.com/x")

    assert not fetched.ok
    assert fetched.text == ""
    assert expected in fetched.error


def test_a_failing_fetch_never_leaks_the_transport_detail(monkeypatch):
    monkeypatch.setattr(
        cs.urllib.request,
        "urlopen",
        lambda request, timeout=None: (_ for _ in ()).throw(urllib.error.URLError("secret-host")),
    )
    assert "secret-host" not in cs.fetch_source("https://cbr.com/x").error


def test_an_empty_body_counts_as_a_failed_fetch(monkeypatch):
    monkeypatch.setattr(
        cs.urllib.request, "urlopen", lambda request, timeout=None: _FakeResp(b"   \n  ")
    )
    fetched = cs.fetch_source("https://cbr.com/x")
    assert not fetched.ok
    assert "empty" in fetched.error


# ─── Sequential, on the caller's thread ─────────────────────────────────────

def test_fetches_run_in_order_on_the_calling_thread(monkeypatch):
    """verify_selected already fans candidates out across a ThreadPoolExecutor.
    A second pool nested inside each worker multiplies out."""
    order: list[str] = []
    threads: set[int] = set()

    def fake_urlopen(request, timeout=None):
        order.append(request.full_url)
        threads.add(threading.get_ident())
        return _FakeResp(b"body")

    monkeypatch.setattr(cs.urllib.request, "urlopen", fake_urlopen)
    fetched = cs.fetch_cited_sources(
        {"evidence_urls": ["https://cbr.com/a", "https://cbr.com/b", "https://cbr.com/c"]}
    )

    assert [f.url for f in fetched] == [
        "https://cbr.com/a",
        "https://cbr.com/b",
        "https://cbr.com/c",
    ]
    assert order == [f"https://r.jina.ai/https://cbr.com/{x}" for x in "abc"]
    assert threads == {threading.get_ident()}


def test_fetch_cited_sources_of_an_uncited_candidate_is_empty(monkeypatch):
    monkeypatch.setattr(
        cs.urllib.request,
        "urlopen",
        lambda *a, **k: pytest.fail("nothing to fetch, so nothing may be opened"),
    )
    assert cs.fetch_cited_sources({"title": "no citations"}) == []


# ─── How the two kinds of evidence are labelled for the gate ────────────────

_SEARCH = {"results": {"web": [{"url": "https://reddit.com/r/comics/1"}]}}


def test_raw_evidence_separates_what_we_read_from_what_we_searched():
    evidence = cs.build_raw_evidence(
        [
            cs.FetchedSource(url="https://cbr.com/a", text="Shuri's scan shows necrosis."),
            cs.FetchedSource(url="https://cbr.com/b", error="timeout"),
        ],
        _SEARCH,
    )

    assert "CITED SOURCES (fetched from the candidate's own citations)" in evidence
    assert "SEARCH RESULTS" in evidence
    assert evidence.index("CITED SOURCES") < evidence.index("SEARCH RESULTS")
    assert json.dumps(_SEARCH, ensure_ascii=False) in evidence


def test_a_fetched_source_is_labelled_with_its_url_and_length():
    evidence = cs.build_raw_evidence(
        [cs.FetchedSource(url="https://cbr.com/a", text="x" * 5800)], _SEARCH
    )
    assert "[1] https://cbr.com/a — fetched, 5,800 chars" in evidence
    assert "x" * 5800 in evidence


def test_a_source_we_could_not_read_says_so_and_carries_no_text():
    evidence = cs.build_raw_evidence(
        [
            cs.FetchedSource(url="https://cbr.com/a", text="read this"),
            cs.FetchedSource(url="https://cbr.com/b", error="timeout"),
        ],
        _SEARCH,
    )

    assert "[2] https://cbr.com/b — COULD NOT FETCH (timeout)" in evidence
    # The distinction is the point of the change: a page we never opened must
    # not read as a page that failed to support the claim.
    assert "COULD NOT FETCH" not in evidence.split("[2]")[0]


def test_a_candidate_that_cited_nothing_says_that_too():
    evidence = cs.build_raw_evidence([], _SEARCH)
    assert "CITED SOURCES" in evidence
    assert "cited no URLs" in evidence
    assert "COULD NOT FETCH" not in evidence
    assert json.dumps(_SEARCH, ensure_ascii=False) in evidence
