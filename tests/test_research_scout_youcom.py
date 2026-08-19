from stages.research_scout.evidence import GateFlag, validate_candidate
from stages.research_scout.models import ScoutMode
import json
import io
import urllib.error
from urllib.parse import parse_qs, urlparse

from stages.research_scout.youcom import RawCall, YouComClient, compact_search_query


def test_candidate_url_not_in_raw_sources_is_blocked():
    candidate = {
        "series_issue_year": "Thor #14 (2024)",
        "evidence_urls": ["https://invented.example/x"],
    }
    raw = RawCall(
        api="research",
        payload={"output": {"sources": [{"url": "https://aiptcomics.com/x"}]}},
    )
    flags = validate_candidate(candidate, raw, ScoutMode.MICRO)
    assert GateFlag.URL_NOT_RETURNED in flags


def test_micro_without_exact_issue_is_blocked_even_with_llm_claim():
    flags = validate_candidate(
        {"series_issue_year": "2024", "visible_event": "Diana kneels"},
        RawCall.empty(),
        ScoutMode.MICRO,
    )
    assert GateFlag.EXACT_ISSUE_REQUIRED in flags


def test_compact_search_query_respects_both_search_limits():
    query = compact_search_query("word " * 100)
    assert len(query.split()) <= 45
    assert len(query) <= 360


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_research_uses_standard_effort_and_keeps_full_response(monkeypatch):
    seen = {}
    fixture = {"output": {"sources": [{"url": "https://aiptcomics.com/x"}], "content": {"candidates": []}}}

    def fake_urlopen(request, timeout):
        seen["request"] = request
        seen["timeout"] = timeout
        return _Response(fixture)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    raw = YouComClient(api_key="fixture-key").research(
        "find a moment", {"type": "object"}, {"domains": ["aiptcomics.com"]}
    )

    body = json.loads(seen["request"].data)
    assert raw.payload == fixture
    assert body["research_effort"] == "standard"
    assert body["source_control"] == {"include_domains": ["aiptcomics.com"]}
    assert body["output_schema"] == {"type": "object"}
    assert raw.response_text == json.dumps(fixture)
    assert seen["request"].get_header("X-api-key") == "fixture-key"


def test_search_uses_compact_web_search_settings(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["request"] = request
        return _Response({
            "results": {
                "web": [{"url": "https://aiptcomics.com/x"}],
                "news": [{"url": "https://cbr.com/y"}],
            }
        })

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    raw = YouComClient(api_key="fixture-key").search(
        "word " * 100, {"domains": ["aiptcomics.com", "cbr.com"]}
    )

    params = parse_qs(urlparse(seen["request"].full_url).query)
    assert raw.api == "search"
    assert params["count"] == ["8"]
    assert params["livecrawl"] == ["all"]
    assert params["livecrawl_formats"] == ["markdown"]
    assert len(params["query"][0].split()) <= 45
    assert params["include_domains"] == ["aiptcomics.com", "cbr.com"]


def test_validate_candidate_collects_nested_search_urls_and_duplicate_flag():
    candidate = {
        "series_issue_year": "Thor #14 (2024)",
        "visible_event": "Thor drops the hammer",
        "evidence_urls": ["https://cbr.com/x"],
        "duplicate_of": "candidate-1",
    }
    raw = RawCall(
        api="search",
        payload={
            "results": {
                "web": [{"pages": [{"url": "https://cbr.com/x"}]}],
                "news": [],
            }
        },
    )
    flags = validate_candidate(candidate, raw, ScoutMode.MICRO)
    assert GateFlag.DUPLICATE in flags
    assert GateFlag.URL_NOT_RETURNED not in flags


def test_qa_candidate_does_not_require_a_visual_event():
    candidate = {
        "series_issue_year": "Thor #14 (2024)",
        "evidence_urls": ["https://aiptcomics.com/x"],
    }
    raw = RawCall(
        api="research",
        payload={"output": {"sources": [{"url": "https://aiptcomics.com/x"}]}},
    )
    flags = validate_candidate(candidate, raw, ScoutMode.QA)
    assert GateFlag.NO_VISUAL_EVENT not in flags


def test_http_error_retains_error_body_and_response_text(monkeypatch):
    error = urllib.error.HTTPError(
        "https://api.you.com/v1/search", 429, "rate limited", {}, io.BytesIO(b'{"retry":true}')
    )
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: (_ for _ in ()).throw(error))

    raw = YouComClient(api_key="fixture-key").search("Thor #14", {"domains": []})

    assert raw.error == "HTTP 429"
    assert raw.payload == {"retry": True}
    assert raw.response_text == '{"retry":true}'


def test_missing_key_returns_error_without_opening_url(monkeypatch):
    def unexpected_urlopen(*args, **kwargs):
        raise AssertionError("network must not be opened without an API key")

    monkeypatch.setattr("urllib.request.urlopen", unexpected_urlopen)
    raw = YouComClient(api_key="").search("Thor", None)
    assert raw.error == "YDC_API_KEY is not set"


def test_url_error_returns_safe_error_bearing_raw_call(monkeypatch):
    def fail_urlopen(*args, **kwargs):
        raise urllib.error.URLError("transport-secret")

    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen)
    raw = YouComClient(api_key="fixture-key").search("Thor", None)

    assert raw.error == "request failed: URLError"
    assert raw.payload == {}
    assert raw.response_text is None
    assert "transport-secret" not in repr(raw)
    assert "fixture-key" not in repr(raw)


def test_timeout_returns_safe_error_bearing_raw_call(monkeypatch):
    def fail_urlopen(*args, **kwargs):
        raise TimeoutError("timeout-secret")

    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen)
    raw = YouComClient(api_key="fixture-key").research(
        "find a moment", {"type": "object"}, None
    )

    assert raw.error == "request failed: TimeoutError"
    assert raw.payload == {}
    assert raw.response_text is None
    assert "timeout-secret" not in repr(raw)
    assert "fixture-key" not in repr(raw)
