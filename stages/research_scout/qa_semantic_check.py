"""Semantic answer check for QA discovery: does the cited synopsis ANSWER the question?

Interface skeleton for the red commit: the data shapes the gate and the tests agree on. The
behaviour lands in the next commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PROMPT_VERSION = "qa_answer_check.v1"
MAX_SYNOPSIS_CHARS = 12_000


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
    kept: tuple[int, ...]
    reason: str = ""
    details: tuple[str, ...] = ()


def _post(body: dict[str, Any], *, timeout: float) -> Any:
    raise NotImplementedError


def request_key(request: SemanticRequest) -> str:
    raise NotImplementedError


def parse_verdict(text: Any, examples: int) -> Verdict:
    raise NotImplementedError


def judge(request: SemanticRequest, verdict: Verdict) -> Judgement:
    raise NotImplementedError


def flag_enabled(value: str | None) -> bool:
    raise NotImplementedError


def default_checker():
    raise NotImplementedError


class OpenRouterChecker:
    def __call__(self, request: SemanticRequest) -> Verdict:
        raise NotImplementedError
