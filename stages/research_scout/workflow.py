"""Explicit state-machine workflow for Stage 1 research scouting."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import config

from . import openrouter_gate
from .models import ResearchSession, ScoutMode, SessionState
from .policies import PolicyBundle
from .storage import SessionStore
from .youcom import RawCall, YouComClient


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
    ):
        self.store = store or SessionStore(config.RESEARCH_SESSIONS_ROOT)
        self.client = client or YouComClient()
        self.digest = digest
        self._policies: dict[ScoutMode, PolicyBundle] = {}

    def start(self, mode: ScoutMode, user_intent: str) -> ResearchSession:
        return self.store.create(ScoutMode(mode), user_intent)

    def run_general(self, session_id: str) -> ResearchSession:
        session = self._load_and_transition(session_id, "run_general")
        bundle = self._bundle(session.mode)
        prompt = bundle.render(
            "general",
            user_intent=session.user_intent,
            angle=self._angle(bundle, session.mode),
            digest=self.digest,
        )
        raw = self.client.research(
            prompt.text,
            {"type": "object"},
            bundle.source_profiles.get("general_research"),
        )
        payload = _raw_payload(raw)
        candidates = _extract_candidates(payload)
        self.store.write_artifact(session.id, "general/research.v1.json", _raw_record(raw))
        self.store.write_artifact(
            session.id,
            "general/candidates.v1.json",
            {"candidates": candidates},
        )
        session.state = SessionState.GENERAL_REVIEW
        return self.store.save(
            session,
            event="general_research_completed",
            detail={"prompt_hash": prompt.sha256, "source_api": getattr(raw, "api", "research")},
        )

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

    def research_specific(self, session_id: str) -> ResearchSession:
        session = self._load_and_transition(session_id, "research_specific")
        bundle = self._bundle(session.mode)
        candidate = self._selected_candidate(session)
        query = json.dumps(candidate, ensure_ascii=False) if candidate else session.user_intent
        raw = self.client.search(query, bundle.source_profiles.get("specific_web_search"))
        raw_search_payload = _raw_payload(raw)
        prompt = bundle.render(
            "evidence_gate",
            user_intent=session.user_intent,
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
        return self.store.save(
            session,
            event="specific_research_completed",
            detail={
                "model": config.SCOUT_EVIDENCE_MODEL,
                "prompt_hash": prompt.sha256,
                "verdict": gate.verdict,
            },
        )

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

    def rerun_general(self, session_id: str) -> ResearchSession:
        session = self._load_and_transition(session_id, "rerun_general")
        session.revision += 1
        session.state = SessionState.GENERAL_DRAFT
        return self.store.save(session, event="general_research_rerun")

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

    @staticmethod
    def _angle(bundle: PolicyBundle, mode: ScoutMode) -> str:
        angles = bundle.general_angles.get(mode.value, [])
        return str(angles[0]) if angles else ""

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
