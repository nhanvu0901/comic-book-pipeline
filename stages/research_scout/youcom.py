"""Small, auditable You.com HTTP boundary for the Stage 1 research scout."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import socket
from collections.abc import Mapping
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


RESEARCH_API = "https://api.you.com/v1/research"
SEARCH_API = "https://api.you.com/v1/search"
_DEFAULT_TIMEOUT = 1800


@dataclass(frozen=True)
class RawCall:
    """The complete result of one upstream call, before candidate parsing."""

    api: str = ""
    payload: Any = None
    error: str | None = None
    status_code: int | None = None
    response_text: str | None = None

    @classmethod
    def empty(cls) -> "RawCall":
        return cls(payload={})

    @property
    def ok(self) -> bool:
        return self.error is None


class YouComClient:
    """Call Research for general scouting and Web Search for specific evidence."""

    def __init__(self, api_key: str | None = None, *, timeout: float = _DEFAULT_TIMEOUT):
        self.api_key = api_key if api_key is not None else os.environ.get("YDC_API_KEY", "").strip()
        self.timeout = timeout

    def research(
        self,
        prompt: str,
        schema: dict[str, Any],
        profile: Mapping[str, Any] | None,
        *,
        effort: str = "standard",
    ) -> RawCall:
        body = {
            "input": prompt,
            "research_effort": effort,
            "source_control": {"include_domains": _domains(profile)},
            "output_schema": schema,
        }
        return self._post_json(RESEARCH_API, "research", body)

    def search(self, query: str, profile: Mapping[str, Any] | None) -> RawCall:
        params: list[tuple[str, str]] = [
            ("query", compact_search_query(query)),
            ("count", "8"),
            ("livecrawl", "all"),
            ("livecrawl_formats", "markdown"),
        ]
        domains = _domains(profile)
        params.extend(("include_domains", domain) for domain in domains)
        url = f"{SEARCH_API}?{urllib.parse.urlencode(params)}"
        return self._get_json(url, "search")

    def _post_json(self, url: str, api: str, body: dict[str, Any]) -> RawCall:
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers=self._headers(content_type=True),
        )
        return self._call(request, api)

    def _get_json(self, url: str, api: str) -> RawCall:
        request = urllib.request.Request(url, method="GET", headers=self._headers())
        return self._call(request, api)

    def _call(self, request: urllib.request.Request, api: str) -> RawCall:
        if not self.api_key:
            return RawCall(api=api, payload={}, error="YDC_API_KEY is not set")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return _decode_response(api, response)
        except urllib.error.HTTPError as exc:
            return _error_response(api, exc, status_code=exc.code)
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            return RawCall(api=api, payload={}, error=f"request failed: {exc.__class__.__name__}")

    def _headers(self, *, content_type: bool = False) -> dict[str, str]:
        headers = {
            "X-API-Key": self.api_key,
            "User-Agent": "comic-scout/1.0",
            "Accept": "application/json",
        }
        if content_type:
            headers["Content-Type"] = "application/json"
        return headers


def compact_search_query(text: str) -> str:
    """Normalize a query to the policy's 45-word and 360-character limits."""

    words = str(text).split()
    selected: list[str] = []
    for word in words:
        if len(selected) >= 45:
            break
        candidate = " ".join((*selected, word))
        if len(candidate) > 360:
            break
        selected.append(word)
    if not selected and words:
        return words[0][:360]
    return " ".join(selected)


def _domains(profile: Mapping[str, Any] | None) -> list[str]:
    if not profile:
        return []
    domains = profile.get("domains", profile.get("include_domains", []))
    if isinstance(domains, str):
        return [domains]
    if not isinstance(domains, (list, tuple)):
        return []
    return [str(domain) for domain in domains if str(domain)]


def _decode_response(api: str, response: Any) -> RawCall:
    body = response.read()
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return RawCall(api=api, payload=text, error="response was not valid JSON", response_text=text)
    return RawCall(
        api=api,
        payload=payload,
        status_code=getattr(response, "status", None),
        response_text=text,
    )


def _error_response(api: str, error: urllib.error.HTTPError, *, status_code: int) -> RawCall:
    try:
        body = error.read()
        text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
    except OSError:
        text = ""
    try:
        payload: Any = json.loads(text) if text else {}
    except (TypeError, ValueError):
        payload = text
    return RawCall(
        api=api,
        payload=payload,
        error=f"HTTP {status_code}",
        status_code=status_code,
        response_text=text,
    )
