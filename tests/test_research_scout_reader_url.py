"""The evidence gate must not be able to smuggle prose into reader_url.

An observed run returned "Please review the candidate's evidence URLs against
the raw search results provided." as the reader_url. The gate schema accepts
any string and answer_research.build_contexts only tests emptiness, so that
prose passed as a URL and suppressed resolve_reader_url()'s deterministic
lookup — Stage 2 then had nothing to download from. Coercing a non-reader URL
to "" at parse time keeps the artifact clean and lets the lookup run.
"""
import json

import pytest

from stages.research_scout import openrouter_gate


def _gate_json(reader_url):
    return json.dumps({
        "verdict": "confirmed",
        "reason": "supported",
        "evidence_urls": [],
        "reader_url": reader_url,
        "flags": [],
    })


def _review_returning(monkeypatch, content):
    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")

    monkeypatch.setattr(openrouter_gate.config, "OPENROUTER_API_KEY", "fixture-key")
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: _Response())
    return openrouter_gate.review(
        raw_search_payload={"results": {"web": []}},
        candidate={"id": "a"},
        prompt="Review this candidate.",
    )


def test_a_real_batcave_reader_url_survives(monkeypatch):
    gate = _review_returning(monkeypatch, _gate_json("https://batcave.biz/reader/6587/34073"))
    assert gate.reader_url == "https://batcave.biz/reader/6587/34073"


@pytest.mark.parametrize("value", [
    "Please review the candidate's evidence URLs against the raw search results provided.",
    "https://batcave.biz/comics/the-incredible-hulk-1968",   # landing page, not a reader
    "https://batcave.biz/reader/6587",                        # one id, not two
    "https://batcave.biz/reader/abc/34073",                   # not numeric
    "https://readcomiconline.li/reader/1/2",                  # another domain
    "http://batcave.biz/reader/1/2",                          # not https
    "",
    None,
])
def test_anything_that_is_not_a_reader_url_becomes_empty(monkeypatch, value):
    assert _review_returning(monkeypatch, _gate_json(value)).reader_url == ""
