"""Explicit state-machine workflow for Stage 1 research scouting."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

import config

from . import cited_sources
from . import openrouter_gate
from . import planner as planner_module
from .errors import ScoutUserError
from .models import EvidenceGate, FeedbackNote, ResearchSession, ScoutMode, SessionState
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
    "claim_citation": cited_sources.CLAIM_CITATION_SCHEMA,
}


# What one candidate's verification round must come back with. Shaped after the
# CONFIRM phase stages/youcom_scout.py documents: a verbatim sentence and the URL
# it sits on, plus the traps that quietly invalidate a comic citation — an
# adaptation standing in for the comic, an arc spanning issues, a cameo passing
# as the subject, a reprint magazine masquerading as the original issue.
_VERIFY_ITEM_PROPS: dict[str, Any] = {
    "verdict": {"type": "string"},
    "verbatim_sentence": {"type": "string"},
    "source_url": {"type": "string"},
    "second_source_url": {"type": "string"},
    "volume_and_year": {"type": "string"},
    "comic_or_adaptation": {"type": "string"},
    "single_issue_or_multi": {"type": "string"},
    "subject_main_in_issue": {"type": "string"},
    "reprint_check": {"type": "string"},
    "issue_year_matches_sources": {"type": "string"},
}


def verify_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": _VERIFY_ITEM_PROPS,
                    "required": list(_VERIFY_ITEM_PROPS),
                },
            },
            "notes": {"type": "string"},
        },
        "required": ["candidates", "notes"],
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


# One review step, not two. Verifying is a self-transition rather than a state
# of its own: nothing about "currently gating" is written to disk, so a crash
# mid-verify leaves a session sitting in CANDIDATE_REVIEW instead of stranded.
ALLOWED = {
    SessionState.GENERAL_DRAFT: {"run_general": SessionState.CANDIDATE_REVIEW},
    SessionState.CANDIDATE_REVIEW: {
        "verify_selected": SessionState.CANDIDATE_REVIEW,
        "approve_selected": SessionState.PRODUCTION_GATES,
        "rerun_general": SessionState.GENERAL_DRAFT,
        "rescout_keeping_confirmed": SessionState.GENERAL_DRAFT,
        "archive": SessionState.ARCHIVED,
    },
    SessionState.PRODUCTION_GATES: {
        "back_to_candidates": SessionState.CANDIDATE_REVIEW,
    },
}

# Gating is IO-bound HTTP (one You.com search + one OpenRouter call each), so
# threads are enough and verify_selected stays synchronous for bridge.run_blocking.
_MAX_GATE_WORKERS = 5
_UNSAFE_ARTIFACT_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


class InvalidTransition(ScoutUserError):
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
                count=str(planner_module.DISTINCT_SOURCE_TARGET),
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
            effort=config.YOUCOM_GENERAL_EFFORT,
        )
        payload = _raw_payload(raw)
        candidates = _extract_candidates(payload, prefix=_revision_prefix(session.revision))
        # Candidates held over from the previous round go in FRONT, under the ids
        # their already-paid-for gates are keyed by. Read before the write below,
        # which is what overwrites the list they come from. An id that no longer
        # resolves is dropped rather than raising: a truncated or hand-edited
        # artifact must not be able to take the whole round down.
        kept: list[dict[str, Any]] = []
        if session.kept_candidate_ids:
            previous = self._candidates_by_id(session)
            kept = [previous[cid] for cid in session.kept_candidate_ids if cid in previous]
            session.kept_candidate_ids = []
        returned_sources = _research_source_urls(payload)
        candidates, validation = _validate_new_general_candidates(
            candidates,
            returned_sources,
            protected_fingerprints={
                cited_sources.citation_fingerprint(citation)
                for candidate in kept
                if (citation := cited_sources.claim_citation(candidate)) is not None
            },
        )
        candidates = kept + candidates
        accepted_bound_sources = {
            cited_sources.citation_fingerprint(citation)[0]
            for candidate in candidates
            if (citation := cited_sources.claim_citation(candidate)) is not None
        }
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
        self.store.write_artifact(
            session.id,
            f"general/candidate_validation.rev{session.revision}.v1.json",
            {
                "revision": session.revision,
                "returned_source_count": len(returned_sources),
                "accepted_bound_source_count": len(accepted_bound_sources),
                "input_candidate_count": validation["input_candidate_count"],
                "accepted_candidate_count": len(candidates),
                "rejected": validation["rejected"],
            },
        )
        # Plan artifact every round, fallback rounds too — reruns stay reconstructable.
        self.store.write_artifact(
            session.id,
            f"general/plan.rev{session.revision}.v1.json",
            plan_record,
        )
        session.state = SessionState.CANDIDATE_REVIEW
        detail: dict[str, Any] = {
            "prompt_hash": prompt_hash,
            "source_api": getattr(raw, "api", "research"),
            "effort": config.YOUCOM_RESEARCH_EFFORT,
            "revision": session.revision,
            "plan_source": plan_record["source"],
            "returned_source_count": len(returned_sources),
            "accepted_bound_source_count": len(accepted_bound_sources),
            "candidate_validation_rejections": validation["rejected"],
        }
        if plan is not None:
            detail["plan_summary"] = f"{plan.unit} · {plan.cardinality}" + (
                f" · ranked: {plan.ranking}" if plan.ranking else ""
            )
        return self.store.save(session, event="general_research_completed", detail=detail)

    def verify_selected(
        self,
        session_id: str,
        candidate_ids: Sequence[str],
        *,
        only: Sequence[str] | None = None,
        on_result: Callable[[str, Any], None] | None = None,
    ) -> ResearchSession:
        """Evidence-gate the ticked candidates in parallel and key the results by id.

        ``candidate_ids`` is the whole current selection and becomes
        ``session.selected_specific_candidate_ids``.  Only the ones that do not
        already hold a gate are sent to the model: a verdict is bought once,
        which is what lets a candidate carried over from an earlier round keep
        the research already paid for it.  ``only`` overrides that and names
        exactly which candidates to re-gate, so a single failed — or simply
        doubted — card can be re-checked without paying for the others again.
        Gates for the rest are carried over unchanged, and gates for candidates
        no longer selected are dropped (see ``_write_gates``).

        ``on_result(candidate_id, gate_or_exception)`` fires from a worker
        thread as each branch finishes and exists for per-card progress only —
        it must not write anything.  The artifact is written once, here, on the
        calling thread, after every branch has settled, so a crash mid-verify
        leaves the previous artifact whole rather than half-replaced.
        """
        session = self._load_and_transition(session_id, "verify_selected")
        ids = self._clean_ids(candidate_ids)
        if only is None:
            # A candidate that already holds a gate is not bought again. That is
            # what lets a re-scout keep the research already paid for on the
            # confirmed candidate while the replacements around it get gated.
            # `only` is the way to ask for a fresh verdict on one anyway.
            held = self._existing_gates(session_id)
            targets = [cid for cid in ids if cid not in held]
        else:
            wanted = set(self._clean_ids(only))
            targets = [cid for cid in ids if cid in wanted]

        bundle = self._bundle(session.mode)
        # Both of these walk the store, so resolve them before any worker starts.
        angle = self._angle(bundle, session.mode)
        candidates = self._candidates_by_id(session)
        intent = _intent_with_feedback(session)

        gates: dict[str, Any] = {}
        searches: dict[str, dict[str, Any]] = {}
        prompt_hashes: dict[str, str] = {}
        failures: dict[str, Exception] = {}
        if targets:
            with ThreadPoolExecutor(max_workers=min(_MAX_GATE_WORKERS, len(targets))) as pool:
                futures = {
                    pool.submit(
                        self._gate_one,
                        bundle,
                        angle,
                        intent,
                        candidates.get(candidate_id, {"id": candidate_id, "summary": intent}),
                    ): candidate_id
                    for candidate_id in targets
                }
                for future in as_completed(futures):
                    candidate_id = futures[future]
                    try:
                        gate, raw_record, prompt_hash = future.result()
                    except Exception as exc:  # one branch failing must not abort the rest
                        failures[candidate_id] = exc
                        outcome: Any = exc
                    else:
                        gates[candidate_id] = gate
                        searches[candidate_id] = raw_record
                        prompt_hashes[candidate_id] = prompt_hash
                        outcome = gate
                    if on_result is not None:
                        try:
                            on_result(candidate_id, outcome)
                        except Exception:
                            # A display hook owned by the caller. Research that
                            # has already been paid for must not be lost to it.
                            pass

        for candidate_id, raw_record in searches.items():
            self.store.write_artifact(
                session.id, f"specific/search.{_artifact_key(candidate_id)}.v1.json", raw_record
            )

        self._write_gates(session.id, ids, fresh=gates)

        session.selected_specific_candidate_ids = ids
        session.state = SessionState.CANDIDATE_REVIEW
        return self.store.save(
            session,
            event="candidates_verified",
            detail={
                "model": config.SCOUT_EVIDENCE_MODEL,
                "prompt_hashes": prompt_hashes,
                "candidate_ids": ids,
                "verified": sorted(gates),
                "verdicts": {cid: gate.verdict for cid, gate in gates.items()},
                "failed": {cid: str(exc) for cid, exc in failures.items()},
            },
        )

    def approve_selected(
        self, session_id: str, candidate_ids: Sequence[str] | None = None,
    ) -> ResearchSession:
        """Lock the selection in. Overriding a soft gate is a decision made at
        project-creation time, not here — see project_factory.

        ``candidate_ids`` is the selection as it stands in front of the user.
        Ticking a box changes nothing on disk — only ``verify_selected`` writes
        the list — so approving without it locks in whatever the last verify
        happened to leave behind. Un-ticking two cards before approving kept
        them; ticking three without verifying first approved nothing at all and
        told the user to select three when they had.

        Omitting it keeps the old read-from-disk behaviour for callers that
        have not re-ticked anything.
        """
        session = self._load_and_transition(session_id, "approve_selected")
        ids = (
            self._clean_ids(candidate_ids)
            if candidate_ids is not None
            else list(session.selected_specific_candidate_ids)
        )
        if session.mode is ScoutMode.QA and not 3 <= len(ids) <= 5:
            raise ScoutUserError("QA requires 3 to 5 selected candidates")
        if session.mode is ScoutMode.MICRO and len(ids) != 1:
            raise ScoutUserError("MICRO requires exactly 1 selected candidate")
        session.selected_specific_candidate_ids = ids
        # A card un-ticked before approval must not leave its gate behind: a
        # stale entry makes len(gates) != len(selected) and _gate_assignments
        # gives up. Carried-over gates are reused, never re-bought.
        self._write_gates(session_id, ids)
        session.state = SessionState.PRODUCTION_GATES
        return self.store.save(
            session, event="selection_approved", detail={"candidate_ids": list(ids)}
        )

    def back_to_candidates(self, session_id: str) -> ResearchSession:
        session = self._load_and_transition(session_id, "back_to_candidates")
        session.state = SessionState.CANDIDATE_REVIEW
        return self.store.save(session, event="returned_to_candidate_review")

    def archive(self, session_id: str, reason: str) -> ResearchSession:
        session = self._load_and_transition(session_id, "archive")
        if not isinstance(reason, str) or not reason.strip():
            raise ScoutUserError("archive reason must be a non-empty string")
        session.state = SessionState.ARCHIVED
        return self.store.save(session, event="session_archived", detail={"reason": reason})

    def rerun_general(self, session_id: str, feedback: str = "") -> ResearchSession:
        session = self._load_and_transition(session_id, "rerun_general")
        if feedback:
            # Literal CANDIDATE_REVIEW, not session.state: _load_and_transition
            # already advanced session.state to GENERAL_DRAFT above.
            session.feedback_log.append(
                FeedbackNote(state=SessionState.CANDIDATE_REVIEW.value, text=feedback)
            )
        # A plain re-run starts the round over and keeps nothing. Leaving the
        # selection and the gate artifact behind is what made a confirmed
        # verdict from round 1 reappear against round 2's candidates.
        session.selected_specific_candidate_ids = []
        self._write_gates(session.id, [])
        session.revision += 1
        session.state = SessionState.GENERAL_DRAFT
        detail = {"feedback": feedback} if feedback else None
        return self.store.save(session, event="general_research_rerun", detail=detail)

    def rescout_keeping_confirmed(self, session_id: str) -> ResearchSession:
        """Go looking for replacements for the candidates that did not hold up,
        while keeping the ones that did — and the gating already paid for them.

        The plain feedback re-run is the all-or-nothing version of this: it
        starts the round over and keeps nothing. Here the confirmed candidates
        survive with their own ids, so their gates stay valid rather than being
        rewritten or re-bought, and the next round is prepended to them.
        """
        session = self._load_and_transition(session_id, "rescout_keeping_confirmed")
        gates = self._existing_gates(session_id)
        confirmed = [
            candidate_id
            for candidate_id in session.selected_specific_candidate_ids
            if str(gates.get(candidate_id, {}).get("verdict", "")).strip().lower() == "confirmed"
        ]
        if not confirmed:
            # Nothing saved yet — _load_and_transition only moved the in-memory
            # copy — so the session stays exactly where the user left it.
            raise ScoutUserError(
                "nothing is confirmed, so there is nothing to keep; "
                "re-run the research with feedback instead"
            )

        candidates = self._candidates_by_id(session)
        dropped = [cid for cid in gates if cid not in confirmed]
        session.feedback_log.append(
            FeedbackNote(
                state=SessionState.CANDIDATE_REVIEW.value,
                text=_rescout_note(
                    [_candidate_label(candidates, cid) for cid in confirmed],
                    [_candidate_label(candidates, cid) for cid in dropped],
                ),
            )
        )
        session.kept_candidate_ids = confirmed
        session.selected_specific_candidate_ids = list(confirmed)
        self._write_gates(session_id, confirmed)
        session.revision += 1
        session.state = SessionState.GENERAL_DRAFT
        return self.store.save(
            session,
            event="rescout_keeping_confirmed",
            detail={"kept": confirmed, "dropped": dropped},
        )

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

    def discover_questions(
        self, mode: ScoutMode, *, count: int = 5, exclude: Sequence[str] = ()
    ) -> list[dict[str, Any]]:
        """Tier B of the Stage 1 empty-intent fallback (ui/bridge.py): spend ONE
        research call turning angles into REAL questions/moments before handing
        anything to the mode's normal machinery. general_qa.v2.md /
        general_micro.v1.md ENUMERATE ANSWERS TO a question — a bare angle
        ("times a famous power or rule failed") is not a question, so feeding it
        straight in researched the wrong thing (Master 2026-08-28 bug find).

        Returns up to ``count`` is_burned-filtered entries, each carrying the
        mode's text field (``question`` for QA, ``moment`` for Micro) and the
        ``angle`` it came from, so the chat can offer a labelled CHOICE with a
        re-roll instead of one take-it-or-leave-it question. This used to return
        the first survivor and throw the rest of the batch away, which is why
        one research call only ever bought one question.

        ``exclude`` is the questions already offered in this session: they go
        into the prompt's EXCLUDE section AND into the burn filter, so a re-roll
        cannot hand back the batch the user just turned down.

        Never raises: an empty intent box must always be able to start a
        session, even with You.com down, unauthenticated, or all-burned — every
        one of those falls back to a single entry holding the rotated angle
        itself (today's pre-fix behavior), flagged ``fallback`` so a caller can
        tell "here is a batch" from "we came back with nothing".
        """
        from stages.youcom_scout import _DISCOVER_PROPS, _MICRO_PROPS, _schema, is_burned

        mode = ScoutMode(mode)
        angle = self.next_angle(mode)
        bundle = self._bundle(mode)
        field, props = (
            ("question", _DISCOVER_PROPS) if mode is ScoutMode.QA else ("moment", _MICRO_PROPS)
        )
        angles = [str(a) for a in bundle.general_angles.get(mode.value, [])] or [angle]
        fallback = [{field: angle, "angle": angle, "fallback": True}]

        excluded = [str(item).strip() for item in exclude if str(item).strip()]
        prompt = bundle.render(
            "discover",
            angles="\n".join(f"- {a}" for a in angles),
            count=str(count),
            exclude="\n".join(f"- {item}" for item in excluded) or "- (nothing yet)",
            digest=self.digest,
        )
        try:
            raw = self.client.research(
                prompt.text,
                # The model labels each candidate with the angle it worked, so the
                # chat can show an angle chip without guessing from list position.
                _schema({**props, "angle": {"type": "string"}}),
                bundle.source_profiles.get("general_research"),
                effort=config.YOUCOM_DISCOVER_EFFORT,
            )
        except Exception:
            return fallback

        # One digest for both jobs: the produced/banned lanes we always avoid,
        # plus whatever this session has already shown. is_burned's loose token
        # overlap is what stops a re-roll returning the same lane re-worded.
        burn_digest = "\n".join(
            [line for line in [self.digest] if line]
            + [f"- {item}" for item in excluded]
        )
        picked: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, candidate in enumerate(_extract_candidates(_raw_payload(raw))):
            text = str(candidate.get(field, "")).strip()
            if not text or text.casefold() in seen:
                continue
            if is_burned(text, burn_digest):
                continue
            seen.add(text.casefold())
            labelled = str(candidate.get("angle", "")).strip() or angles[index % len(angles)]
            picked.append({**candidate, field: text, "angle": labelled})
            if len(picked) >= count:
                break
        return picked or fallback

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

    @staticmethod
    def _clean_ids(candidate_ids: Sequence[str]) -> list[str]:
        # Materialise once: a generator would come back empty on a second pass
        # and read as "every id was blank".
        given = list(candidate_ids)
        ids = [cid.strip() for cid in given if isinstance(cid, str) and cid.strip()]
        if len(ids) != len(given):
            raise ScoutUserError("candidate ids must be non-empty strings")
        if len(set(ids)) != len(ids):
            raise ScoutUserError("candidate ids must not contain duplicates")
        return ids

    def _candidates_by_id(self, session: ResearchSession) -> dict[str, dict[str, Any]]:
        path = self.store.artifact_path(session.id, "general/candidates.v1.json")
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            str(candidate["id"]): dict(candidate)
            for candidate in data.get("candidates", [])
            if isinstance(candidate, Mapping) and candidate.get("id")
        }

    def _write_gates(
        self,
        session_id: str,
        keep_ids: Sequence[str],
        *,
        fresh: Mapping[str, Any] | None = None,
    ) -> None:
        """Rewrite the gate artifact as exactly one entry per id in ``keep_ids``.

        A freshly produced gate wins; otherwise the one already on disk is
        carried over unchanged, so re-gating one failed card does not cost the
        others again. An id with neither is simply absent — and so is every id
        NOT in ``keep_ids``. That prune is not housekeeping: a stale entry makes
        ``len(gates) != len(selected_ids)``, which is exactly what makes
        ``project_factory._gate_assignments`` give up and return all-``None``.
        Every path that narrows what a session holds — a deselection, a
        re-scout, a plain re-run that keeps nothing — prunes through here, so
        there is one pruner rather than one per caller.
        """
        path = self.store.artifact_path(session_id, "specific/evidence_gate.v1.json")
        produced = fresh or {}
        if not produced and not path.exists():
            # Nothing was ever gated and nothing is being kept. An empty `gates`
            # list reads as "we gated and found nothing", which is a different
            # claim from "we never gated" — so say neither.
            return
        previous = self._existing_gates(session_id)
        merged: list[dict[str, Any]] = []
        for candidate_id in keep_ids:
            if candidate_id in produced:
                # candidate_id goes on LAST: the model is never trusted to echo
                # back the id of the candidate it was handed.
                merged.append(
                    {**produced[candidate_id].model_dump(mode="json"), "candidate_id": candidate_id}
                )
            elif candidate_id in previous:
                merged.append(previous[candidate_id])
        self.store.write_artifact(
            session_id, "specific/evidence_gate.v1.json", {"gates": merged}
        )

    def _existing_gates(self, session_id: str) -> dict[str, dict[str, Any]]:
        """Already-written gates, keyed by candidate id, for the merge."""
        path = self.store.artifact_path(session_id, "specific/evidence_gate.v1.json")
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return {}
        raw = data.get("gates") if isinstance(data, Mapping) else data
        if not isinstance(raw, list):
            return {}
        return {
            str(gate["candidate_id"]): dict(gate)
            for gate in raw
            if isinstance(gate, Mapping) and gate.get("candidate_id")
        }

    def _gate_one(
        self,
        bundle: PolicyBundle,
        angle: str,
        intent: str,
        candidate: dict[str, Any],
    ) -> tuple[Any, dict[str, Any], str]:
        """One candidate's verification round + evidence gate. Runs on a worker
        thread and touches no session state — the caller owns every write.

        This asks the Research API to go and check one candidate rather than
        running a keyword search over it. A search returned eight results ranked
        for the query, which for a claim about a specific issue meant forum
        chatter; a deep verification round reads the sources and reports what it
        found, including when the issue and year do not match them.

        The verdict it returns is evidence for the gate, not the decision — the
        gate also holds the candidate's own fetched citations and does the quote
        matching.
        """
        verify_prompt = bundle.render(
            "specific",
            user_intent=intent,
            angle=angle,
            digest=self.digest,
            candidate=json.dumps(candidate, ensure_ascii=False),
        )
        raw = self.client.research(
            verify_prompt.text,
            verify_output_schema(),
            bundle.source_profiles.get("specific_web_search"),
            effort=config.YOUCOM_VERIFY_EFFORT,
        )
        raw_search_payload = _raw_payload(raw)
        # Sequentially, inside this one worker: verify_selected already runs the
        # candidates in parallel and a pool nested here would multiply out.
        # BƯỚC 1: Tải văn bản qua Jina Reader (Tạm thời bỏ qua / skipped for now)
        # fetched = cited_sources.fetch_cited_sources(candidate)
        fetched: list[cited_sources.FetchedSource] = []
        # BƯỚC 2: So khớp câu trích dẫn cơ học (Tạm thời bỏ qua / skipped for now)
        # bound = cited_sources.claim_citation(candidate)
        # if "claim_citation" in candidate:
        #     if bound is None:
        #         return (
        #             EvidenceGate(
        #                 verdict="inconclusive",
        #                 reason="claim_citation is missing or malformed",
        #             ),
        #             _raw_record(raw),
        #             "",
        #         )
        #     bound_source = next(
        #         (source for source in fetched if cited_sources.canonical_url(source.url)
        #          == cited_sources.canonical_url(bound.url)),
        #         None,
        #     )
        #     if bound_source is None or not bound_source.ok:
        #         return (
        #             EvidenceGate(
        #                 verdict="inconclusive",
        #                 reason="bound citation could not be retrieved",
        #             ),
        #             _raw_record(raw),
        #             "",
        #         )
        #     if not cited_sources.quote_matches_source(bound, bound_source):
        #         return (
        #             EvidenceGate(
        #                 verdict="inconclusive",
        #                 reason="bound quote does not occur in retrieved source text",
        #             ),
        #             _raw_record(raw),
        #             "",
        #         )
        prompt = bundle.render(
            "evidence_gate",
            user_intent=intent,
            angle=angle,
            digest=self.digest,
            candidate=json.dumps(candidate, ensure_ascii=False),
            raw_evidence=cited_sources.build_raw_evidence(fetched, raw_search_payload),
        )
        gate = openrouter_gate.review(
            model=config.SCOUT_EVIDENCE_MODEL,
            prompt=prompt.text,
            candidate=candidate,
            raw_search_payload=raw_search_payload,
        )
        return gate, _raw_record(raw), prompt.sha256


def _candidate_label(candidates: Mapping[str, Any], candidate_id: str) -> str:
    """How a candidate is named back to the model. The issue and year go along
    with the title because that is the part the next round has to avoid."""
    candidate = candidates.get(candidate_id) or {}
    title = str(candidate.get("title") or candidate.get("entity") or "").strip()
    issue = str(candidate.get("series_issue_year") or "").strip()
    if title and issue:
        return f"{title} ({issue})"
    return title or issue or candidate_id


def _rescout_note(held: Sequence[str], turned_down: Sequence[str]) -> str:
    """The exclusions, as a feedback note rather than a prompt change.

    `_intent_with_feedback` folds notes into the fallback prompt and the planner
    is handed them on the planner path, so one note reaches both routes and no
    policy template has to be reversioned.
    """
    lines = [
        "Re-scouting this question: keep what is already confirmed and replace the rest."
    ]
    if held:
        lines.append(
            "ALREADY HELD, do not propose these again: " + "; ".join(held) + "."
        )
    if turned_down:
        lines.append(
            "ALREADY TURNED DOWN, do not propose these again: "
            + "; ".join(turned_down)
            + "."
        )
    return " ".join(lines)


def _artifact_key(candidate_id: str) -> str:
    """A candidate id is model-supplied text, so keep it to characters that are
    safe in a filename before it becomes one."""
    return _UNSAFE_ARTIFACT_CHARS.sub("_", candidate_id) or "candidate"


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


def _revision_prefix(revision: int) -> str:
    """The id namespace one general-research round writes under.

    Round 1 keeps the bare ``candidate-N`` ids every session already on disk
    uses, so nothing has to be rewritten. Every later round gets its own
    prefix: an id must name a comic, not a list position, or a gate written
    for ``candidate-1`` in round 1 gets displayed against whatever lands at
    position 1 in round 2.
    """
    return "" if revision <= 1 else f"r{revision}-"


def _extract_candidates(payload: Any, *, prefix: str = "") -> list[dict[str, Any]]:
    """Parse a research payload into candidates, giving each one an id.

    ``prefix`` namespaces the generated ids and belongs to the caller, not to
    the parser: only the general-research round has a revision to namespace
    by. ``discover_questions`` parses its Tier B batch through here too, and
    that batch is keyed by its ``question``/``moment`` text rather than by id,
    so it stays on the bare default.
    """
    if isinstance(payload, Mapping):
        for key in ("candidates", "output", "content"):
            value = payload.get(key)
            extracted = _extract_candidates(value, prefix=prefix)
            if extracted:
                return extracted
        return []
    if isinstance(payload, list):
        candidates = [dict(item) for item in payload if isinstance(item, Mapping)]
        for index, candidate in enumerate(candidates, start=1):
            candidate.setdefault("id", f"{prefix}candidate-{index}")
        return candidates
    if isinstance(payload, str):
        try:
            return _extract_candidates(json.loads(payload), prefix=prefix)
        except (TypeError, ValueError):
            return []
    return []


def _research_source_urls(payload: Any) -> set[str]:
    """Canonical URLs returned by this general-research response."""

    if not isinstance(payload, Mapping):
        return set()
    output = payload.get("output")
    container = output if isinstance(output, Mapping) else payload
    sources = container.get("sources")
    if not isinstance(sources, list):
        return set()
    return {
        canonical
        for source in sources
        if isinstance(source, Mapping)
        and isinstance(source.get("url"), str)
        and (canonical := cited_sources.canonical_url(source["url"]))
    }


def _validate_new_general_candidates(
    candidates: Sequence[dict[str, Any]],
    returned_sources: set[str],
    *,
    protected_fingerprints: set[tuple[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Screen only a freshly produced general batch before it reaches review.

    The strict API schema is a request, not a trust boundary. This makes a
    missing/invented binding visible in a durable artifact and rejects only
    exact canonical-source+quote duplicates; different quotes from one real
    page remain legitimate support for different candidates.
    """

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    seen = set(protected_fingerprints)
    for candidate in candidates:
        candidate_id = str(candidate.get("id", ""))
        citation = cited_sources.claim_citation(candidate)
        if citation is None:
            rejected.append({"candidate_id": candidate_id, "reason": "missing_claim_citation"})
            continue
        fingerprint = cited_sources.citation_fingerprint(citation)
        if not fingerprint[0] or fingerprint[0] not in returned_sources:
            rejected.append({"candidate_id": candidate_id, "reason": "claim_citation_url_not_returned"})
            continue
        if fingerprint in seen:
            rejected.append({"candidate_id": candidate_id, "reason": "duplicate_claim_citation"})
            continue
        seen.add(fingerprint)
        accepted.append(candidate)
    return accepted, {
        "input_candidate_count": len(candidates),
        "rejected": rejected,
    }
