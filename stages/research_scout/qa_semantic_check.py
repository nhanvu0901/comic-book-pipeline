"""Semantic answer check for QA discovery: does the cited synopsis ANSWER the question?

The structural gate (qa_discovery_gate.py) proves a quote is verbatim, sits in a written synopsis of the
right issue and shares words with the question. It cannot tell "the quote mentions the subject" from
"the quote answers the question": every word-counting rule either let wrong-focus answers through or
rejected honest paraphrases. One cheap model call can. It runs only for a candidate whose every other
check passed (or that failed nothing but the lexical grounding), asks the evidence model whether the
synopsis states what the question asks, and the CODE then verifies that the evidence sentence the
model cites is really in the synopsis. Any error fails the candidate closed.

``_post`` is the only door to the network, so tests close it and fake the checker instead.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import config

from . import cited_sources, openrouter_gate
from .openrouter_gate import RequestFailure

logger = logging.getLogger(__name__)

PROMPT_VERSION = "qa_answer_check.v1"
MAX_SYNOPSIS_CHARS = 12_000
_TIMEOUT = 60.0
_ATTEMPTS = 2                        # one call and one retry
_PROMPT_PATH = Path(__file__).resolve().parents[2] / "research_prompts" / f"{PROMPT_VERSION}.md"
_ANSWERS = ("yes", "partial", "no")
_MIN_EVIDENCE_WORDS = 3


@dataclass(frozen=True)
class Example:
    """One cited issue the structural gate verified: its label, URL, quote and synopsis section."""

    issue: str
    url: str
    quote: str
    synopsis: str


@dataclass(frozen=True)
class SemanticRequest:
    question: str
    fmt: str
    payoff: str
    examples: tuple[Example, ...]


@dataclass(frozen=True)
class ExampleVerdict:
    answers_question: str       # yes | partial | no
    evidence_sentence: str


@dataclass(frozen=True)
class Verdict:
    """The model's parsed answer (or ``error`` when there is none)."""

    answers_question: str
    evidence_sentence: str
    payoff_supported: str       # yes | no
    reason: str
    examples: tuple[ExampleVerdict, ...]
    error: str = ""


@dataclass(frozen=True)
class Judgement:
    """What the gate does with a verdict: the examples that answer, or the one reason it rejects."""

    kept: tuple[int, ...]
    reason: str = ""
    details: tuple[str, ...] = ()


# --- the network door -------------------------------------------------------------------------------------

def _post(body: dict[str, Any], *, timeout: float) -> Any:
    """One OpenRouter chat completion: the reply text, or a ``RequestFailure``."""
    return openrouter_gate._request(body, timeout=timeout)


# --- configuration ------------------------------------------------------------------------------------------

def flag_enabled(value: str | None) -> bool:
    """``QA_SEMANTIC_CHECK`` is ON unless it is explicitly turned off."""
    return str(value or "").strip().lower() not in {"0", "false", "no", "off", "n"}


_default: "OpenRouterChecker | None" = None
_default_lock = threading.Lock()


def default_checker():
    """The process-wide checker (its cache is shared by every candidate), or None when switched off."""
    global _default
    if not config.QA_SEMANTIC_CHECK:
        return None
    with _default_lock:
        if _default is None:
            _default = OpenRouterChecker()
        return _default


# --- the request ---------------------------------------------------------------------------------------------

def _issue_key(issue: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", issue.casefold()).strip()


def request_key(request: SemanticRequest) -> str:
    """Identity of what was asked: the question, the format, the payoff and each cited URL and quote.
    The page text is not part of it: the same ask over a trimmed copy of a page is the same ask."""
    asked = {"q": request.question, "f": request.fmt, "p": request.payoff,
             "e": [[e.url, e.quote] for e in request.examples]}
    return hashlib.sha1(json.dumps(asked, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _bounded(synopsis: str, quote: str) -> str:
    """The synopsis as the model should read it (markup removed), at most MAX_SYNOPSIS_CHARS long;
    a longer one is cut around the quote."""
    text = " ".join(cited_sources._visible_markdown_text(synopsis).split())
    if len(text) <= MAX_SYNOPSIS_CHARS:
        return text
    norm_text = text.casefold()
    clean_quote = " ".join(quote.split()).casefold()
    start = -1
    for length in (100, 60, 40, 20):
        if len(clean_quote) >= length:
            pos = norm_text.find(clean_quote[:length])
            if pos >= 0:
                start = pos
                break
    if start < 0 and clean_quote:
        words = [w for w in clean_quote.split() if len(w) >= 4]
        for i in range(len(words) - 2):
            chunk = " ".join(words[i:i + 3])
            pos = norm_text.find(chunk)
            if pos >= 0:
                start = pos
                break
    start = max(start, 0)
    left = max(0, min(start - MAX_SYNOPSIS_CHARS // 2, len(text) - MAX_SYNOPSIS_CHARS))
    cut = text[left:left + MAX_SYNOPSIS_CHARS]
    return ("[...] " if left else "") + cut + (" [...]" if left + MAX_SYNOPSIS_CHARS < len(text) else "")


def _user_message(request: SemanticRequest) -> str:
    parts = [f"QUESTION: {request.question}", f"FORMAT: {request.fmt}",
             f"VIEWER_PAYOFF: {request.payoff.strip() or '(none)'}"]
    for number, example in enumerate(request.examples, 1):
        parts += ["", f"EXAMPLE {number}: {example.issue}", f"QUOTE: {example.quote}",
                  "SYNOPSIS:", _bounded(example.synopsis, example.quote)]
    return "\n".join(parts)


_ANSWER_ENUM = {"type": "string", "enum": list(_ANSWERS)}
_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "answers_question": _ANSWER_ENUM,
        "evidence_sentence": {"type": "string"},
        "payoff_supported": {"type": "string", "enum": ["yes", "no"]},
        "reason": {"type": "string"},
        "examples": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"example": {"type": "integer"}, "answers_question": _ANSWER_ENUM,
                           "evidence_sentence": {"type": "string"}},
            "required": ["example", "answers_question", "evidence_sentence"]}},
    },
    "required": ["answers_question", "evidence_sentence", "payoff_supported", "reason", "examples"],
}


def _system_message() -> str:
    return _PROMPT_PATH.read_text("utf-8").strip()


def _body(request: SemanticRequest, model: str) -> dict[str, Any]:
    return {
        "model": model,
        "temperature": 0,
        "messages": [{"role": "system", "content": _system_message()},
                     {"role": "user", "content": _user_message(request)}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "qa_answer_check", "strict": True, "schema": _SCHEMA}},
        "provider": {"require_parameters": True},
    }


# --- the answer ----------------------------------------------------------------------------------------------------

def _error(message: str) -> Verdict:
    return Verdict("", "", "", "", (), error=message)


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


def parse_verdict(text: Any, examples: int) -> Verdict:
    """The model's JSON as a ``Verdict``; anything that breaks the schema is an error verdict, never a pass."""
    if not isinstance(text, str) or not text.strip():
        return _error("empty answer")
    fenced = _FENCE.match(text)
    try:
        data = json.loads(fenced.group(1) if fenced else text)
    except ValueError:
        return _error("answer is not valid JSON")
    if not isinstance(data, dict):
        return _error("answer is not a JSON object")

    def word(value: Any, allowed: tuple[str, ...]) -> str | None:
        folded = str(value).strip().casefold() if isinstance(value, str) else ""
        return folded if folded in allowed else None

    overall, payoff = word(data.get("answers_question"), _ANSWERS), word(data.get("payoff_supported"), ("yes", "no"))
    sentence, reason, rows = data.get("evidence_sentence"), data.get("reason", ""), data.get("examples")
    if overall is None or payoff is None or not isinstance(sentence, str) or not isinstance(reason, str):
        return _error("answer breaks the schema")
    if not isinstance(rows, list) or len(rows) != examples:
        return _error(f"answer has {len(rows) if isinstance(rows, list) else 'no'} examples, {examples} were asked")
    parsed = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict) or row.get("example") != index:
            return _error("answer has a malformed example")
        verdict = word(row.get("answers_question"), _ANSWERS)
        evidence = row.get("evidence_sentence")
        if verdict is None or not isinstance(evidence, str):
            return _error("answer has a malformed example")
        parsed.append(ExampleVerdict(verdict, evidence))
    return Verdict(overall, sentence, payoff, reason, tuple(parsed))


def _norm_evidence(text: str) -> str:
    t = cited_sources.comparable_text(text)
    return t.replace('"', "'")


def _verbatim(sentence: str, synopsis: str) -> bool:
    """The evidence sentence is in the synopsis, compared the way the gate compares quotes."""
    wanted = _norm_evidence(sentence).strip(" \t\n'\".,;:!?")
    haystack = _norm_evidence(synopsis)
    return len(wanted.split()) >= _MIN_EVIDENCE_WORDS and wanted in haystack


def judge(request: SemanticRequest, verdict: Verdict) -> Judgement:
    """The pass rule, in code: an example counts when the model says ``yes`` AND the sentence it cites is
    verbatim in that example's synopsis; a direct question needs one such example and a list two from
    different issues; the model must also say the question is answered as a whole, and (when the candidate
    promises a payoff) that the synopsis supports it."""
    if verdict.error:
        return Judgement((), "semantic_check_error", (f"semantic check: {verdict.error}",))
    if len(verdict.examples) != len(request.examples):
        return Judgement((), "semantic_check_error", (
            f"semantic check: {len(verdict.examples)} answers for {len(request.examples)} examples",))
    kept, notes = [], []
    for index, (example, said) in enumerate(zip(request.examples, verdict.examples)):
        if said.answers_question != "yes":
            notes.append(f"{example.issue}: semantic {said.answers_question}")
        elif not _verbatim(said.evidence_sentence, example.synopsis):
            notes.append(f"{example.issue}: evidence sentence not verbatim in the synopsis")
        else:
            kept.append(index)
    why = f"semantic: {verdict.reason}".strip()
    required = 2 if request.fmt == "list" else 1
    if verdict.answers_question != "yes" or len({_issue_key(request.examples[i].issue) for i in kept}) < required:
        return Judgement((), "answer_not_supported", tuple(notes) + (why,))
    if request.payoff.strip() and verdict.payoff_supported != "yes":
        return Judgement((), "payoff_not_supported", tuple(notes) + (why,))
    return Judgement(tuple(kept), "", tuple(notes) + (why,))


# --- the checker ----------------------------------------------------------------------------------------------------

class OpenRouterChecker:
    """Asks SCOUT_EVIDENCE_MODEL (through the project's OpenRouter client) at temperature 0. One retry;
    an answer that is still missing or malformed is an error verdict (the gate fails the candidate closed)
    and is logged. A good answer is cached by what was asked and the model."""

    def __init__(self, *, timeout: float = _TIMEOUT) -> None:
        self._timeout = timeout
        self._cache: dict[tuple[str, str], Verdict] = {}
        self._lock = threading.Lock()

    def __call__(self, request: SemanticRequest) -> Verdict:
        model = config.SCOUT_EVIDENCE_MODEL
        key = (request_key(request), model)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        if not config.OPENROUTER_API_KEY:
            logger.warning("semantic answer check cannot run: OPENROUTER_API_KEY is not set")
            return _error("OPENROUTER_API_KEY is not set")
        body, last = _body(request, model), ""
        for attempt in range(1, _ATTEMPTS + 1):
            started = time.monotonic()
            reply = _post(body, timeout=self._timeout)
            if isinstance(reply, RequestFailure):
                last = reply.as_text()
            else:
                verdict = parse_verdict(reply, len(request.examples))
                if not verdict.error:
                    logger.info("semantic answer check (%s, %.1fs, attempt %d): %s", model,
                                time.monotonic() - started, attempt, verdict.answers_question)
                    with self._lock:
                        self._cache[key] = verdict
                    return verdict
                last = verdict.error
            logger.warning("semantic answer check attempt %d/%d failed for %r: %s",
                           attempt, _ATTEMPTS, request.question[:90], last)
        logger.warning("semantic answer check failed closed for %r: %s", request.question[:90], last)
        return _error(last)
