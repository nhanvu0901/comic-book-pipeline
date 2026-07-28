"""Unit test for the r.jina.ai fallback in fetch_fandom._http_get_json.

Direct request raises 403 (Cloudflare) -> should retry once via the jina
reader proxy and parse its (markdown-fenced) JSON body.
"""
import json
import urllib.error

import pytest

from stages.stage_1.tools import fetch_fandom as ff


def test_jina_fallback_parses_json_after_direct_403(monkeypatch):
    monkeypatch.setenv("FANDOM_PROXY", "1")
    payload = {"query": {"search": [{"title": "Harley Quinn Vol 3 25"}]}}
    calls = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        calls.append(url)
        if url.startswith("https://r.jina.ai/"):
            # jina sometimes wraps the body in a markdown fence + prose.
            body = f"Title: page\n\n```json\n{json.dumps(payload)}\n```".encode()
            return _FakeResp(body)
        raise urllib.error.HTTPError(url, 403, "Forbidden", None, None)

    monkeypatch.setattr(ff.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(ff.time, "sleep", lambda _s: None)  # skip the real retry delay

    result = ff._http_get_json("https://dc.fandom.com/api.php?action=query")

    assert result == payload
    assert any(u.startswith("https://r.jina.ai/") for u in calls)


def test_proxy_disabled_returns_none_on_direct_failure(monkeypatch):
    monkeypatch.setenv("FANDOM_PROXY", "0")

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", None, None)

    monkeypatch.setattr(ff.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(ff.time, "sleep", lambda _s: None)

    assert ff._http_get_json("https://dc.fandom.com/api.php?action=query") is None


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
