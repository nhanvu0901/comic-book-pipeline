"""Research-plan boundary for the Stage 1 research scout.

Fills the 4-knob ResearchPlan contract (unit / cardinality / ranking /
extra_fields — see RESEARCH_PLANNER_DESIGN.md) so ONE research flow can serve
any comic story/character question — Q&A, micro, or ad-hoc — with no question
taxonomy in code: `make_plan` is the only LLM call, `validate_plan` is the
safety boundary, `compile_schema` and `assemble_prompt` are pure code. A plan
that fails validation is treated as invalid and never reaches the caller —
`workflow.run_general` falls back to today's fixed template/schema instead.
"""

from __future__ import annotations

import copy
import json
import re
import socket
from collections.abc import Mapping
from typing import Any
import urllib.error
import urllib.request

from pydantic import BaseModel, Field

import config

from . import cited_sources


_TIMEOUT = 180.0
_REQUEST_FAILED = object()

_CARDINALITIES = ("exhaustive", "options", "pinpoint")
_FIELD_TYPES = ("string", "string_array")
_FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_MAX_EXTRA_FIELDS = 6
_MAX_PROMPT_CHARS = 4000

# Names the compiler already owns (core item fields) or that collide with the
# wrapper's own keys — a plan can never shadow these with an extra_field.
RESERVED_FIELD_NAMES = frozenset({
    "title", "summary", "character_or_thing", "series_issue_year",
    "what_visibly_happens", "evidence_urls", "rank_reason", "id", "flags",
    "claim_citation", "notes", "candidates",
    # micro-only fields the compiler adds (MICRO_DETAIL_PROPS)
    "aftermath", "context_behind", "unrevealed", "detail_citations",
})


class PlanField(BaseModel):
    name: str = ""
    type: str = "string"          # "string" | "string_array"
    description: str = ""


class ResearchPlan(BaseModel):
    unit: str = ""                # what ONE candidate represents
    cardinality: str = ""         # "exhaustive" | "options" | "pinpoint"
    ranking: str = ""             # "" = unordered, else the criterion
    extra_fields: list[PlanField] = Field(default_factory=list)
    research_prompt: str = ""


def validate_plan(plan: ResearchPlan) -> str | None:
    """Return a reason the plan is unsafe to compile, or None when it is fine."""

    if plan.cardinality not in _CARDINALITIES:
        return f"cardinality must be one of {_CARDINALITIES}, got {plan.cardinality!r}"
    if len(plan.extra_fields) > _MAX_EXTRA_FIELDS:
        return f"extra_fields must have at most {_MAX_EXTRA_FIELDS} entries"

    seen: set[str] = set()
    for field in plan.extra_fields:
        if not _FIELD_NAME_RE.match(field.name):
            return f"extra field name {field.name!r} does not match ^[a-z][a-z0-9_]{{0,39}}$"
        if field.name in RESERVED_FIELD_NAMES:
            return f"extra field name {field.name!r} is reserved"
        if field.name in seen:
            return f"duplicate extra field name {field.name!r}"
        seen.add(field.name)
        if field.type not in _FIELD_TYPES:
            return f"extra field type must be one of {_FIELD_TYPES}, got {field.type!r}"

    if not plan.research_prompt.strip():
        return "research_prompt must not be blank"
    if len(plan.research_prompt) > _MAX_PROMPT_CHARS:
        return f"research_prompt must be at most {_MAX_PROMPT_CHARS} characters"
    if not plan.unit.strip():
        return "unit must not be blank"
    return None


# Core candidate fields the compiler always adds, never overridable by a plan.
# Mirrors workflow._GENERAL_ITEM_PROPS minus character_or_thing — "unit" now
# carries what a candidate represents, so that field moved to RESERVED instead
# of staying a hard-coded core prop.
_CORE_ITEM_PROPS: dict[str, Any] = {
    "title": {"type": "string"},
    "summary": {"type": "string"},
    "series_issue_year": {"type": "string"},
    "what_visibly_happens": {"type": "string"},
    "evidence_urls": {"type": "array", "items": {"type": "string"}},
    "claim_citation": cited_sources.CLAIM_CITATION_SCHEMA,
}


# Micro only. A micro candidate is one scene, and the scene alone left a writer
# ending on a teaser it could not resolve: what happens next, and what set the
# moment up, are asked for separately, each tied to a quoted source. Every
# property is required (strict schema), so a source that states nothing comes
# back "" / [] rather than a missing key. Q&A candidates never carry these.
MICRO_DETAIL_PROPS: dict[str, Any] = {
    "aftermath": {"type": "string"},
    "context_behind": {"type": "string"},
    "unrevealed": {"type": "string"},
    "detail_citations": {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "supports": {"type": "string"},
                "url": {"type": "string"},
                "quote": {"type": "string"},
            },
            "required": ["supports", "url", "quote"],
        },
    },
}


def compile_schema(plan: ResearchPlan, mode: str = "") -> dict[str, Any]:
    """You.com-strict output schema for one validated plan.

    Same wrapper shape as workflow.general_output_schema: additionalProperties
    false at every object level, every property required, and NO
    minItems/maxItems (the Research API rejects them — probed 2026-08-21).
    ``mode="micro"`` adds MICRO_DETAIL_PROPS; any other mode is the Q&A shape.
    """

    item_props: dict[str, Any] = dict(_CORE_ITEM_PROPS)
    for field in plan.extra_fields:
        item_props[field.name] = (
            {"type": "array", "items": {"type": "string"}}
            if field.type == "string_array"
            else {"type": "string"}
        )
    if plan.ranking.strip():
        item_props["rank_reason"] = {"type": "string"}
    if mode == "micro":
        # Last, so a plan that skipped validate_plan still cannot replace one of
        # these names with an extra_field of the wrong type.
        item_props.update(copy.deepcopy(MICRO_DETAIL_PROPS))

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": item_props,
                    "required": list(item_props),
                },
            },
            "notes": {"type": "string"},
        },
        "required": ["candidates", "notes"],
    }


# Invariant rules every research round relies on, regardless of plan — adapted
# from research_prompts/general_qa.v2.md. The planner's own research_prompt
# must NOT restate these; folding them in twice would just pad the prompt.
_INVARIANT_RULES = (
    "Rules:\n"
    "- Cite only URLs you actually retrieved — never invent a source.\n"
    "- Bind every candidate to exactly one claim_citation URL and a verbatim "
    "sentence from that retrieved page. That sentence must support this "
    "candidate's exact issue/year and visible claim; do not invent missing "
    "details.\n"
    "- Name the exact series + issue number + year whenever a retrieved "
    "source names them; never invent missing issue details.\n"
    "- Describe what VISIBLY happens on the page, not implied or inferred "
    "meaning.\n"
    "- Real published events only.\n"
    "- Widely-debated hypothetical picks go LAST and must say \"hypothetical\" "
    "in the summary."
)

_MICRO_INVARIANT_RULES = (
    "Rules:\n"
    "- Cite only URLs you actually retrieved — never invent a source.\n"
    "- Bind every candidate to one claim_citation URL and a verbatim sentence "
    "from that retrieved page supporting the decisive action or admission. "
    "Establish exact series, issue, and year from that page's metadata or "
    "another retrieved source; do not pretend the quote states missing details.\n"
    "- Name the exact series, volume when needed, issue number, and year; "
    "never guess missing identity details.\n"
    "- Describe only sourced page actions or spoken revelations, not inferred "
    "motives or invented choreography.\n"
    "- Real published comic events only."
)

# Applies only to micro research. The planner's free-form research_prompt can
# omit a useful story turn; this contract must still reach the research call.
# QA assembly remains byte-for-byte the previous path.
_MICRO_SCOUT_RULES = (
    "Micro-moment selection:\n"
    "- One candidate is one scene or tightly connected sequence in one "
    "published issue, suitable for a 35–50 second Short. A long recap may "
    "suggest a lead; extract only one self-contained turn.\n"
    "- Give the setup, the specific action or reveal that changes the "
    "situation, and its direct consequence. Name who acts or speaks. Reject "
    "abstract claims such as tactical patience without a sourced action.\n"
    "- For an open-ended scout, prefer published issues from the current year "
    "and then the previous year; the workflow appends the live year window. "
    "Honor an explicitly requested older issue, year, or era. A broken "
    "character rule, loud spectacle, second famous name, and low YouTube "
    "coverage are ranking bonuses, not mandatory gates.\n"
    "- Keep summary and what_visibly_happens focused on the same scene. Do not "
    "invent motives, panel order, choreography, or consequences from another issue."
)

# Micro only, and it travels with _MICRO_SCOUT_RULES for the same reason: the
# planner's research_prompt cannot be trusted to supply it. Three rules, in this
# order — ask for the aftermath and the context, never treat a reviewer's
# reaction as an event, never treat the request's wording as evidence. A real
# session built a candidate from a review that withheld its twist ("a shocking
# twist I never saw coming") and the scout restated it as an event that echoed
# the request. general_micro.v1.md and stages/youcom_scout.py carry the same
# three; Q&A never sees this block.
_MICRO_DETAIL_RULES = (
    "Aftermath, context and what sources withhold:\n"
    "- Also return what happens next (`aftermath`) and what set the moment up "
    "(`context_behind`), each backed by `detail_citations` with a verbatim "
    "quote, and list those URLs in `evidence_urls` too. `aftermath` is what "
    "happens after the turning point in the same issue, how the confrontation "
    "or scene ends, and what any announced twist actually is. `context_behind` "
    "is what set the moment up: why these characters are here and at odds, "
    "what each wants, where a key object or power came from, as sources state "
    "it. `unrevealed` is any outcome a source hints at but never states. Each "
    "`detail_citations` entry is {\"supports\": \"aftermath\" or "
    "\"context_behind\", \"url\": the page, \"quote\": a verbatim sentence from "
    "that page}. Use \"\" when no source states it; never infer or invent an "
    "outcome or backstory.\n"
    "- A reviewer's reaction or a teaser (\"a shocking twist I never saw "
    "coming\", \"who's at the center of that twist\", \"everything changes\") is "
    "not an event. Never restate it as one, and never build "
    "`what_visibly_happens`, `turning_point` or `aftermath` from it. When a "
    "source withholds a reveal, keep `aftermath` to what is actually stated and "
    "put the withheld point in `unrevealed`.\n"
    "- The request's own wording (e.g. \"final twist\", \"shocking reveal\") says "
    "what the user hopes to find; it is never evidence. Do not echo it into a "
    "candidate unless a source states it."
)

# Research breadth is driven by source coverage, never a candidate floor.  The
# API cannot enforce array minima, and a candidate minimum made the model split
# one article into invented entries.  This is deliberately modest: it tells the
# model to seek independent evidence without asking it to fabricate volume.
DISTINCT_SOURCE_TARGET = 6

_CARDINALITY_BLOCKS: dict[str, str] = {
    "exhaustive": (
        "Sweep EVERY retrieved source. Full, partial, assisted, and temporary "
        "cases all count — state the difference in the summary. Seek coverage "
        f"from about {DISTINCT_SOURCE_TARGET} distinct source pages when the "
        "research supports it, but return only genuinely supported candidates: "
        "there is no candidate minimum and never pad the list. State the "
        "actual distinct-source and candidate counts in notes."
    ),
    "options": (
        "Candidates are alternatives for the Master to choose from — propose "
        "distinct, genuinely supported options, favoring variety. Seek coverage "
        f"from about {DISTINCT_SOURCE_TARGET} distinct source pages when the "
        "research supports it; there is no candidate minimum and never pad the "
        "list. State the actual distinct-source and candidate counts in notes."
    ),
    "pinpoint": (
        "The set is closed and named in the task — cover exactly that range, "
        "one candidate per member, and say plainly when a member has no "
        "supporting source."
    ),
}


def assemble_prompt(
    plan: ResearchPlan, digest: str, *, user_intent: str = "", mode: str = ""
) -> str:
    """Deterministic prompt assembly — pure code, no LLM call.

    Order: invariant rules, the user's unchanged request, the unit sentence,
    the cardinality block, the ranking block (only when plan.ranking is set),
    the plan's own research_prompt, then the digest.
    """

    sections = [
        _MICRO_INVARIANT_RULES if mode == "micro" else _INVARIANT_RULES,
    ]
    if user_intent:
        sections.append(
            f"USER INTENT: {user_intent}\n"
            "The user intent is authoritative. Keep the requested comic, issue, "
            "and scope even if the planner instruction or digest suggests another."
        )
    sections.extend([
        f"One candidate per {plan.unit} — never merge entries.",
        _CARDINALITY_BLOCKS[plan.cardinality],
    ])
    if plan.ranking.strip():
        sections.append(
            f"Order candidates by {plan.ranking}, best first, and justify "
            "each position in rank_reason."
        )
    if mode == "micro":
        sections.append(_MICRO_SCOUT_RULES)
        sections.append(_MICRO_DETAIL_RULES)
    sections.append(plan.research_prompt)
    sections.append(f"SCOUTED DIGEST:\n{digest}")
    return "\n\n".join(sections)


_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "unit": {"type": "string"},
        "cardinality": {"type": "string", "enum": list(_CARDINALITIES)},
        "ranking": {"type": "string"},
        "extra_fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string", "enum": list(_FIELD_TYPES)},
                    "description": {"type": "string"},
                },
                "required": ["name", "type", "description"],
                "additionalProperties": False,
            },
        },
        "research_prompt": {"type": "string"},
    },
    "required": ["unit", "cardinality", "ranking", "extra_fields", "research_prompt"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = """You are the Stage 1 research planner for a comic-book Q&A/story channel.

Your ONLY job: read one Master research question (plus a mode hint and any \
feedback) and fill a small ResearchPlan JSON object — four knobs the calling \
code compiles into a research prompt and a strict output schema. You never \
write raw JSON Schema and you never restate citation/URL/"what visibly \
happens" rules inside research_prompt — the calling code adds those \
automatically for every plan. Keep research_prompt focused on what makes \
THIS question specific.

Fields you fill:
- unit: free text naming what ONE candidate represents ("one character", \
"one issue", "one single drawn scene", "one verdict", "one alternate \
version", ...).
- cardinality: exactly one of "exhaustive" (the list IS the answer — sweep \
every source, never stop at the famous names), "options" (candidates are \
alternatives for the Master to pick from), or "pinpoint" (a closed, \
pre-bounded set named in the question — cover exactly that range).
- ranking: "" for unordered, else the ranking criterion as free text \
("fan-consensus best", "most brutal", "strongest shown on page").
- extra_fields: 0-6 additional per-item fields beyond the core ones the \
code always adds (title, summary, series_issue_year, what_visibly_happens, \
evidence_urls, claim_citation). Each is {name: lowercase_snake_case, type: "string" or \
"string_array", description}.
- research_prompt: the research instruction specific to this question.

NEVER narrow a scope the Master did not narrow. Do not add qualifiers such as \
one continuity only, a date range, a publisher line, "main canon", or \
"explicitly shown on-panel" unless the question itself states them — silent \
narrowing throws away real answers (measured: adding "main continuity" and \
"explicitly on-panel" to an open resistance question cut a 12-answer result \
down to 5). An open question keeps an open research_prompt; borderline cases \
belong in the results labeled as borderline, not excluded.

Question families (few-shots to calibrate the knobs — NOT a taxonomy to \
branch on; a question that matches none of these still gets a plan):
- feats & power ("best Hulk strength feats") -> unit: one feat; \
cardinality: exhaustive; ranking: yes (e.g. "most impressive")
- broken constants ("times Batman used a gun") -> unit: one violation; \
cardinality: exhaustive; ranking: none
- wins/losses/deaths ("who beat Thanos 1v1") -> unit: one fight/death; \
cardinality: exhaustive; ranking: none
- artifacts & mantles ("everyone who lifted Mjolnir") -> unit: one \
holder/lift; cardinality: exhaustive; ranking: none
- resistances ("who resisted the Anti-Life Equation") -> unit: one \
character; cardinality: exhaustive; ranking: none
- issue lookups ("exact issues of Deathstroke in Unkillables #1-3") -> \
unit: one issue/beat; cardinality: pinpoint; ranking: none
- single moments ("most brutal Punisher page") -> unit: one scene; \
cardinality: options; ranking: yes
- versions & debates ("every evil Superman across universes") -> unit: one \
version/question; cardinality: exhaustive or options; ranking: optional

Return ONLY the ResearchPlan JSON — no prose."""


def make_plan(user_intent: str, feedback_notes: list[str], mode: str) -> ResearchPlan | None:
    """Ask config.SCOUT_PLANNER_MODEL to fill a ResearchPlan for one question.

    One repair retry on invalid/unvalidatable JSON (same shape as
    openrouter_gate.review). Transport failures are never retried. Returns
    None on any failure — the caller (workflow.run_general) falls back to
    today's fixed template/schema instead of ever raising.
    """

    if not config.OPENROUTER_API_KEY:
        return None

    model = config.SCOUT_PLANNER_MODEL
    user_message = _user_message(user_intent, feedback_notes, mode)

    first_content = _request(_request_body(model, user_message), timeout=_TIMEOUT)
    if first_content is _REQUEST_FAILED:
        return None
    plan, reason = _parse_plan(first_content)
    if plan is not None:
        return plan

    repair_message = (
        f"{user_message}\n\nYour previous response was rejected because "
        f"{reason}. Return only valid JSON matching the requested schema, "
        "fixing exactly that problem."
    )
    repaired_content = _request(_request_body(model, repair_message), timeout=_TIMEOUT)
    if repaired_content is _REQUEST_FAILED:
        return None
    repaired_plan, _ = _parse_plan(repaired_content)
    return repaired_plan


def _user_message(user_intent: str, feedback_notes: list[str], mode: str) -> str:
    parts = [str(user_intent), f"MODE HINT: {mode}"]
    if mode == "micro":
        from .micro_recency import recent_micro_instruction
        parts.append(recent_micro_instruction())
    notes = [str(note) for note in feedback_notes if str(note).strip()]
    if notes:
        parts.append(
            "MASTER FEEDBACK from earlier rounds — this may change "
            "unit/fields/ranking, not just the wording of research_prompt:\n"
            + "\n".join(f"- {note}" for note in notes)
        )
    return "\n\n".join(parts)


def _request_body(model: str, user_message: str) -> dict[str, Any]:
    system_prompt = _SYSTEM_PROMPT
    if "MODE HINT: micro" in user_message:
        system_prompt += (
            "\nFor micro mode only, the channel's default scope is recently "
            "published issues. Honor the live year window in the user message; "
            "an explicitly requested older issue, year, or era overrides it."
            "\nFor micro mode the code also adds aftermath, context_behind, "
            "unrevealed and detail_citations to every candidate and writes the "
            "research rules for them; never choose those names as extra_fields."
        )
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "research_plan",
                "strict": True,
                "schema": _PLAN_SCHEMA,
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


def _parse_plan(content: Any) -> tuple[ResearchPlan | None, str]:
    """Return (plan, rejection reason). The reason is fed back to the model on the
    repair attempt: a generic "that was invalid" makes it guess which knob to
    change, while the concrete rule it broke is directly fixable."""

    if isinstance(content, Mapping):
        parsed = content
    elif isinstance(content, str):
        try:
            parsed = json.loads(content)
        except (TypeError, ValueError):
            return None, "the response was not valid JSON"
    else:
        return None, "the response carried no text content"
    if not isinstance(parsed, Mapping):
        return None, "the response JSON was not an object"
    try:
        plan = ResearchPlan.model_validate(parsed)
    except (TypeError, ValueError):
        return None, "the response did not match the ResearchPlan shape"
    reason = validate_plan(plan)
    if reason is not None:
        return None, reason
    return plan, ""
