"""OpenRouter evidence-gate boundary for the Stage 1 research scout."""

from __future__ import annotations

import json
import socket
from collections.abc import Mapping
from typing import Any
import urllib.error
import urllib.request

import config

from .models import EvidenceGate


_TIMEOUT = 180.0
_REQUEST_FAILED = object()
_EVIDENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["confirmed", "rejected", "inconclusive"]},
        "reason": {"type": "string"},
        "evidence_urls": {"type": "array", "items": {"type": "string"}},
        "reader_url": {"type": ["string", "null"]},
        "flags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "reason", "evidence_urls", "reader_url", "flags"],
    "additionalProperties": False,
}
_VALID_VERDICTS = frozenset(_EVIDENCE_SCHEMA["properties"]["verdict"]["enum"])
_REQUIRED_GATE_FIELDS = frozenset(_EVIDENCE_SCHEMA["required"])


def review(
    *,
    raw_search_payload: Any,
    candidate: Any,
    prompt: str,
    model: str | None = None,
    timeout: float = _TIMEOUT,
) -> EvidenceGate:
    """Review one candidate using only the raw Search payload as evidence input.

    A malformed or schema-invalid response gets exactly one JSON repair request.
    The gate never retries transport failures and never embeds a general Research
    response in its request payload.
    """

    selected_model = model or config.SCOUT_EVIDENCE_MODEL
    if not config.OPENROUTER_API_KEY:
        return EvidenceGate(verdict="inconclusive", reason="OPENROUTER_API_KEY is not set")

    first_content = _request(
        _request_body(
            model=selected_model,
            prompt=prompt,
            candidate=candidate,
            raw_search_payload=raw_search_payload,
        ),
        timeout=timeout,
    )
    if first_content is _REQUEST_FAILED:
        return EvidenceGate(verdict="inconclusive", reason="OpenRouter request failed")
    first_gate = _parse_gate(first_content)
    if first_gate is not None:
        return first_gate

    repair_prompt = (
        f"{prompt}\n\nYour previous response was not valid EvidenceGate JSON. "
        "Return only valid JSON matching the requested schema."
    )
    repaired_content = _request(
        _request_body(
            model=selected_model,
            prompt=repair_prompt,
            candidate=candidate,
            raw_search_payload=raw_search_payload,
        ),
        timeout=timeout,
    )
    if repaired_content is _REQUEST_FAILED:
        return EvidenceGate(verdict="inconclusive", reason="OpenRouter repair request failed")
    repaired_gate = _parse_gate(repaired_content)
    if repaired_gate is not None:
        return repaired_gate
    return EvidenceGate(
        verdict="inconclusive",
        reason="evidence gate returned invalid JSON after one repair retry",
    )


def _request_body(
    *, model: str, prompt: str, candidate: Any, raw_search_payload: Any
) -> dict[str, Any]:
    context = prompt
    candidate_json = json.dumps(candidate, ensure_ascii=False)
    raw_search_json = json.dumps(raw_search_payload, ensure_ascii=False)
    if candidate_json not in context:
        context += f"\n\nCANDIDATE JSON:\n{candidate_json}"
    if raw_search_json not in context:
        context += f"\n\nRAW SEARCH PAYLOAD JSON:\n{raw_search_json}"
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": "Return only the requested evidence-gate JSON."},
            {"role": "user", "content": context},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "evidence_gate",
                "strict": True,
                "schema": _EVIDENCE_SCHEMA,
            },
        },
        "provider": {"require_parameters": True},
    }


def _request(body: dict[str, Any], *, timeout: float) -> Any:
    url = config.OPENROUTER_BASE_URL.rstrip("/") + "/chat/completions"
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
        socket.timeout,
        OSError,
        ValueError,
    ):
        return _REQUEST_FAILED
    return _message_content(payload)


def _message_content(payload: Any) -> Any:
    if not isinstance(payload, Mapping):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    message = choices[0].get("message") if isinstance(choices[0], Mapping) else None
    if not isinstance(message, Mapping):
        return None
    content = message.get("content")
    if isinstance(content, list):
        return "".join(
            str(part.get("text", "")) for part in content if isinstance(part, Mapping)
        )
    return content


def _parse_gate(content: Any) -> EvidenceGate | None:
    if isinstance(content, Mapping):
        parsed = content
    elif isinstance(content, str):
        try:
            parsed = json.loads(content)
        except (TypeError, ValueError):
            return None
    else:
        return None
    if not isinstance(parsed, Mapping):
        return None
    if set(parsed) != _REQUIRED_GATE_FIELDS:
        return None
    if parsed["verdict"] not in _VALID_VERDICTS:
        return None
    if not isinstance(parsed["reason"], str):
        return None
    if not isinstance(parsed["evidence_urls"], list) or not all(
        isinstance(url, str) for url in parsed["evidence_urls"]
    ):
        return None
    if parsed["reader_url"] is not None and not isinstance(parsed["reader_url"], str):
        return None
    if not isinstance(parsed["flags"], list) or not all(
        isinstance(flag, str) for flag in parsed["flags"]
    ):
        return None
    try:
        return EvidenceGate.model_validate(parsed)
    except (TypeError, ValueError):
        return None
