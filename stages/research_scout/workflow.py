"""Explicit state-machine workflow for Stage 1 research scouting."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable

import config

from . import openrouter_gate
from . import planner as planner_module
from .models import FeedbackNote, ResearchSession, ScoutMode, SessionState
from .planner import ResearchPlan
from .policies import PolicyBundle
from .storage import SessionStore
from .youcom import RawCall, YouComClient


def _intent_with_feedback(session: ResearchSession) -> str:
    """Fold every non-blank feedback note into the intent sent to the next prompt."""
    notes = [f.text for f in session.feedback_log if f.text.strip()]
    if not notes:
        return session.user_intent
    joined = "\n".join(f"- {t}" for t in notes)
    return (
        session.user_intent
        + "\n\nMASTER FEEDBACK from earlier rounds — address ALL of it:\n"
        + joined
    )


# Strict OpenAI-style schema, the only shape You.com's Research API honors: every
# object level needs additionalProperties:false and every property in required
# (youcom_scout pilot 2026-08-05). Without it ({"type":"object"} alone) the API
# falls back to a markdown essay in output.content and candidate parsing gets 0.
# minItems/maxItems are NOT supported (API warning, probed 2026-08-21) — recall is
# driven by the prompt, never by schema keywords.
# Field names line up with evidence.validate_candidate (series_issue_year,
# what_visibly_happens, evidence_urls) and the review UI cards (title, summary).
_GENERAL_ITEM_PROPS: dict[str, Any] = {
    "title": {"type": "string"},
    "summary": {"type": "string"},
    "character_or_thing": {"type": "string"},
    "series_issue_year": {"type": "string"},
    "what_visibly_happens": {"type": "string"},
    "evidence_urls": {"type": "array", "items": {"type": "string"}},
}


def general_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": _GENERAL_ITEM_PROPS,
                    "required": list(_GENERAL_ITEM_PROPS),
                },
            },
            "notes": {"type": "string"},
        },
        "required": ["candidates", "notes"],
    }


ALLOWED = {
    SessionState.GENERAL_DRAFT: {"run_general": SessionState.GENERAL_REVIEW},
    SessionState.GENERAL_REVIEW: {
        "approve_general": SessionState.SPECIFIC_REVIEW,
        "rerun_general": SessionState.GENERAL_DRAFT,
        "archive": SessionState.ARCHIVED,
    },
    SessionState.SPECIFIC_REVIEW: {
        "research_specific": SessionState.SPECIFIC_REVIEW,
        "approve_specific": SessionState.PRODUCTION_GATES,
        "back_general": SessionState.GENERAL_REVIEW,
        "archive": SessionState.ARCHIVED,
    },
}


class InvalidTransition(ValueError):
    """Raised when a workflow action is not allowed from the current state."""


class ScoutWorkflow:
    def __init__(
        self,
        store: SessionStore | None = None,
        client: YouComClient | None = None,
        *,
        digest: str = "",
        planner: Callable[[str, list[str], str], ResearchPlan | None] | None = None,
    ):
        self.store = store or SessionStore(config.RESEARCH_SESSIONS_ROOT)
        self.client = client or YouComClient()
        self.digest = digest
        # Tests inject a stub here; only run_general ever calls it — the real
        # default hits OpenRouter, which config.load_dotenv() means is a live
        # key in this process, so nothing but run_general may reach it.
        self._planner = planner if planner is not None else planner_module.make_plan
        self._policies: dict[ScoutMode, PolicyBundle] = {}

    def start(self, mode: ScoutMode, user_intent: str) -> ResearchSession:
        return self.store.create(ScoutMode(mode), user_intent)

    def run_general(self, session_id: str) -> ResearchSession:
        session = self._load_and_transition(session_id, "run_general")
        bundle = self._bundle(session.mode)
        feedback_notes = [f.text for f in session.feedback_log if f.text.strip()]
        plan = self._planner(session.user_intent, feedback_notes, session.mode.value)
        if plan is None:
            # Fallback path — byte-for-byte today's behavior: the mode's fixed
            # template + the fixed schema. _intent_with_feedback folds feedback
            # in here because the planner never saw it on this path.
            prompt = bundle.render(
                "general",
                user_intent=_intent_with_feedback(session),
                angle=self._angle(bundle, session.mode),
                digest=self.digest,
            )
            prompt_text, prompt_hash = prompt.text, prompt.sha256
            schema = general_output_schema()
            plan_record: dict[str, Any] = {"source": "fallback"}
        else:
            # Planner path — feedback already reached the planner input above,
            # so it must NOT be folded into the prompt a second time here.
            prompt_text = planner_module.assemble_prompt(plan, self.digest)
            prompt_hash = hashlib.sha256(prompt_text.encode()).hexdigest()
            schema = planner_module.compile_schema(plan)
            plan_record = {"source": "planner", **plan.model_dump(mode="json")}
        raw = self.client.research(
            prompt_text,
            schema,
            bundle.source_profiles.get("general_research"),
            effort=config.YOUCOM_RESEARCH_EFFORT,
        )
        payload = _raw_payload(raw)
        candidates = _extract_candidates(payload)
        self.store.write_artifact(session.id, "general/research.v1.json", _raw_record(raw))
        self.store.write_artifact(
            session.id,
            "general/candidates.v1.json",
            {"candidates": candidates},
        )
        # Keep every round's payload under its own name so the chat can still show a
        # superseded round's candidates after a rerun overwrites candidates.v1.json.
        self.store.write_artifact(
            session.id,
            f"general/candidates.rev{session.revision}.v1.json",
            {"revision": session.revision, "candidates": candidates},
        )
        # Plan artifact every round, fallback rounds too — reruns stay reconstructable.
        self.store.write_artifact(
            session.id,
            f"general/plan.rev{session.revision}.v1.json",
            plan_record,
        )
        session.state = SessionState.GENERAL_REVIEW
        detail: dict[str, Any] = {
            "prompt_hash": prompt_hash,
            "source_api": getattr(raw, "api", "research"),
            "effort": config.YOUCOM_RESEARCH_EFFORT,
            "revision": session.revision,
            "plan_source": plan_record["source"],
        }
        if plan is not None:
            detail["plan_summary"] = f"{plan.unit} · {plan.cardinality}" + (
                f" · ranked: {plan.ranking}" if plan.ranking else ""
            )
        return self.store.save(session, event="general_research_completed", detail=detail)

    def approve_general(self, session_id: str, candidate_id: str) -> ResearchSession:
        session = self._load_and_transition(session_id, "approve_general")
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            raise ValueError("candidate_id must be a non-empty string")
        session.selected_general_candidate_id = candidate_id
        session.state = SessionState.SPECIFIC_REVIEW
        return self.store.save(
            session,
            event="general_candidate_approved",
            detail={"candidate_id": candidate_id},
        )

    def research_specific(self, session_id: str, feedback: str = "") -> ResearchSession:
        session = self._load_and_transition(session_id, "research_specific")
        if feedback:
            # Append BEFORE rendering so this round's own feedback is already folded
            # into _intent_with_feedback(session) below, not just future rounds'.
            session.feedback_log.append(
                FeedbackNote(state=SessionState.SPECIFIC_REVIEW.value, text=feedback)
            )
        bundle = self._bundle(session.mode)
        candidate = self._selected_candidate(session)
        query = json.dumps(candidate, ensure_ascii=False) if candidate else session.user_intent
        raw = self.client.search(query, bundle.source_profiles.get("specific_web_search"))
        raw_search_payload = _raw_payload(raw)
        prompt = bundle.render(
            "evidence_gate",
            user_intent=_intent_with_feedback(session),
            angle=self._angle(bundle, session.mode),
            digest=self.digest,
            candidate=json.dumps(candidate, ensure_ascii=False),
            raw_evidence=json.dumps(raw_search_payload, ensure_ascii=False),
        )
        gate = openrouter_gate.review(
            model=config.SCOUT_EVIDENCE_MODEL,
            prompt=prompt.text,
            candidate=candidate,
            raw_search_payload=raw_search_payload,
        )
        self.store.write_artifact(session.id, "specific/search.v1.json", _raw_record(raw))
        self.store.write_artifact(
            session.id,
            "specific/evidence_gate.v1.json",
            gate.model_dump(mode="json"),
        )
        session.state = SessionState.SPECIFIC_REVIEW
        detail = {
            "model": config.SCOUT_EVIDENCE_MODEL,
            "prompt_hash": prompt.sha256,
            "verdict": gate.verdict,
        }
        if feedback:
            detail["feedback"] = feedback
        return self.store.save(session, event="specific_research_completed", detail=detail)

    def decide_specific(
        self,
        session_id: str,
        candidate_ids: Sequence[str],
        feedback: str = "",
    ) -> ResearchSession:
        session = self._load_and_transition(session_id, "approve_specific")
        ids = [candidate_id for candidate_id in candidate_ids if isinstance(candidate_id, str)]
        if len(set(ids)) != len(ids):
            raise ValueError("candidate_ids must not contain duplicates")
        if session.mode is ScoutMode.QA and not 3 <= len(ids) <= 5:
            raise ValueError("QA requires 3 to 5 selected candidates")
        if session.mode is ScoutMode.MICRO and len(ids) != 1:
            raise ValueError("MICRO requires exactly 1 selected candidate")
        session.selected_specific_candidate_ids = ids
        session.state = SessionState.PRODUCTION_GATES
        return self.store.save(
            session,
            event="specific_candidates_decided",
            detail={"candidate_ids": ids, "feedback": feedback},
        )

    def archive(self, session_id: str, reason: str) -> ResearchSession:
        session = self._load_and_transition(session_id, "archive")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("archive reason must be a non-empty string")
        session.state = SessionState.ARCHIVED
        return self.store.save(session, event="session_archived", detail={"reason": reason})

    def rerun_general(self, session_id: str, feedback: str = "") -> ResearchSession:
        session = self._load_and_transition(session_id, "rerun_general")
        if feedback:
            # Literal GENERAL_REVIEW, not session.state: _load_and_transition already
            # advanced session.state to GENERAL_DRAFT above.
            session.feedback_log.append(
                FeedbackNote(state=SessionState.GENERAL_REVIEW.value, text=feedback)
            )
        session.revision += 1
        session.state = SessionState.GENERAL_DRAFT
        detail = {"feedback": feedback} if feedback else None
        return self.store.save(session, event="general_research_rerun", detail=detail)

    def back_general(self, session_id: str) -> ResearchSession:
        session = self._load_and_transition(session_id, "back_general")
        session.state = SessionState.GENERAL_REVIEW
        return self.store.save(session, event="returned_to_general_review")

    def _load_and_transition(self, session_id: str, action: str) -> ResearchSession:
        session = self.store.load(session_id)
        try:
            next_state = ALLOWED[session.state][action]
        except KeyError as exc:
            raise InvalidTransition(
                f"{action} is not allowed from {session.state.name}; "
                f"expected one of {', '.join(sorted(self._allowed_states(action)))}"
            ) from exc
        session.state = next_state
        return session

    @staticmethod
    def _allowed_states(action: str) -> set[str]:
        return {
            state.name
            for state, actions in ALLOWED.items()
            if action in actions
        }

    def _bundle(self, mode: ScoutMode) -> PolicyBundle:
        if mode not in self._policies:
            self._policies[mode] = PolicyBundle.load(mode)
        return self._policies[mode]

    def next_angle(self, mode: ScoutMode) -> str:
        """Public entry point for the Tier B empty-intent fallback (ui/bridge.py):
        the next angle in rotation for `mode`, as plain text usable as a
        research intent on its own. Shares _angle's rotation pointer so the
        angle used to SEED a brand-new session and the angle folded into that
        session's own prompt stay on the same sequence."""
        return self._angle(self._bundle(mode), mode)

    def _mode_session_count(self, mode: ScoutMode) -> int:
        """How many sessions already exist for `mode` — the rotation pointer.

        Counting sessions already on disk (instead of a separate counter file)
        means the pointer can't drift out of sync with what's actually durable:
        every session.start() adds exactly one, and a purged session directory
        naturally removes one instead of leaving a stale counter behind.
        """
        count = 0
        for entry in self.store.root.iterdir():
            if not entry.is_dir() or not (entry / "session.json").exists():
                continue
            try:
                session = self.store.load(entry.name)
            except Exception:
                continue
            if session.mode is mode:
                count += 1
        return count

    def _angle(self, bundle: PolicyBundle, mode: ScoutMode) -> str:
        # Bug fixed 2026-08-22: this used to always return angles[0], so 4 of
        # the 5 angles per mode (research_policies/general_angles.v1.json)
        # were dead code. Rotating by the session count is deterministic (no
        # randomness -> reproducible runs) and advances every time a NEW
        # session for this mode is created.
        angles = bundle.general_angles.get(mode.value, [])
        if not angles:
            return ""
        return str(angles[self._mode_session_count(mode) % len(angles)])

    def _selected_candidate(self, session: ResearchSession) -> dict[str, Any]:
        selected_id = session.selected_general_candidate_id
        if not selected_id:
            return {"id": "", "summary": session.user_intent}
        path = self.store.artifact_path(session.id, "general/candidates.v1.json")
        if not path.exists():
            return {"id": selected_id, "summary": session.user_intent}
        data = json.loads(path.read_text(encoding="utf-8"))
        for candidate in data.get("candidates", []):
            if isinstance(candidate, Mapping) and candidate.get("id") == selected_id:
                return dict(candidate)
        return {"id": selected_id, "summary": session.user_intent}


def _raw_payload(raw: Any) -> Any:
    if isinstance(raw, RawCall):
        return raw.payload
    return getattr(raw, "payload", raw)


def _raw_record(raw: Any) -> dict[str, Any]:
    return {
        "api": getattr(raw, "api", ""),
        "payload": _raw_payload(raw),
        "error": getattr(raw, "error", None),
    }


def _extract_candidates(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, Mapping):
        for key in ("candidates", "output", "content"):
            value = payload.get(key)
            extracted = _extract_candidates(value)
            if extracted:
                return extracted
        return []
    if isinstance(payload, list):
        candidates = [dict(item) for item in payload if isinstance(item, Mapping)]
        for index, candidate in enumerate(candidates, start=1):
            candidate.setdefault("id", f"candidate-{index}")
        return candidates
    if isinstance(payload, str):
        try:
            return _extract_candidates(json.loads(payload))
        except (TypeError, ValueError):
            return []
    return []
