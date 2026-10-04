#!/usr/bin/env python3
"""Run the Stage 1 scout acceptance sample without touching production data.

Live You.com calls are opt-in (--live). Session artifacts are kept in a fresh
temporary directory and only aggregate, non-content metrics go to the report.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stages.research_scout.models import ScoutMode, SessionState
from stages.research_scout.storage import SessionStore
from stages.research_scout.workflow import ScoutWorkflow
from stages.research_scout.youcom import RawCall, YouComClient


BATMAN_SEED_INTENT = "Every time Batman broke his no-kill rule"
SEEDS = (
    ("qa_batman_no_kill_breaks", ScoutMode.QA, BATMAN_SEED_INTENT),
    ("qa_wolverine_healing", ScoutMode.QA, "Which comic issues show Wolverine losing his healing factor?"),
    ("qa_mjolnir_wielders", ScoutMode.QA, "Which heroes have lifted Mjolnir in the comics?"),
    ("qa_deadpool_kills_heroes", ScoutMode.QA, "Which heroes has Deadpool killed in the comics?"),
    ("micro_hulk", ScoutMode.MICRO, "A single Hulk comic scene where his power or control fails."),
    ("micro_invincible", ScoutMode.MICRO, "A single Invincible comic scene with a shocking turning point."),
)
HELD_ISSUES = ("Batman #57 (2018)", "Final Crisis #6 (2009)")
_ISSUE = re.compile(r"^\s*(.*?)\s+#\s*(\d+(?:\.\d+)?[A-Za-z]?)\s*(?:\((\d{4})\))?\s*$")
_NON_COMIC = re.compile(r"\b(movie|film|television|tv|video game|game|season|episode|animated series)\b", re.I)


def _acceptance_runs():
    return list(SEEDS) + [("rescout_batman_held", ScoutMode.QA, BATMAN_SEED_INTENT)]


class _OfflineYouCom:
    """Deterministic empty response used only by --dry-run and unit tests."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def research(self, prompt: str, schema: dict, profile: Any, *, effort: str = "standard") -> RawCall:
        self.calls.append({"effort": effort, "hard_rules_first": prompt.lstrip().startswith("HARD RULES")})
        return RawCall(api="research", payload={"output": {"content": {"candidates": []}, "sources": []}})


class _RecordingYouCom:
    """Keep prompt content in memory only; expose booleans to the report."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: list[dict[str, Any]] = []

    @property
    def api_key(self) -> str:
        return self.inner.api_key

    def research(self, prompt: str, schema: dict, profile: Any, *, effort: str = "standard") -> RawCall:
        self.calls.append({"effort": effort, "hard_rules_first": prompt.lstrip().startswith("HARD RULES")})
        return self.inner.research(prompt, schema, profile, effort=effort)


def _issue_key(label: str) -> str | None:
    match = _ISSUE.match(label)
    if not match:
        return None
    return re.sub(r"\bthe\b", "", match.group(1).casefold()).strip(), match.group(2).casefold()


def _accepted_candidate_violations(candidate: dict[str, Any]) -> set[str]:
    """Return hard comic/year violations from both label and display title."""
    label = str(candidate.get("series_issue_year", ""))
    match = _ISSUE.match(label)
    violations: set[str] = set()
    if not match or not match.group(1).strip() or not match.group(3):
        violations.add("issue_label_unparseable_or_year_missing")
    elif int(match.group(3)) < 2010:
        violations.add("pre_2010")
    if _NON_COMIC.search(label + " " + str(candidate.get("title", ""))):
        violations.add("non_comic")
    return violations


def _prime_rescout(store: SessionStore, session_id: str) -> None:
    """Create an isolated prior review state with two selected held candidates."""
    held = [
        {"id": f"held-{n}", "series_issue_year": label, "title": "held fixture"}
        for n, label in enumerate(HELD_ISSUES)
    ]
    store.write_artifact(session_id, "general/candidates.v1.json", {"candidates": held})
    session = store.load(session_id)
    session.state = SessionState.CANDIDATE_REVIEW
    session.selected_specific_candidate_ids = [row["id"] for row in held]
    store.save(session)


def _read_validation(store: SessionStore, session_id: str, revision: int) -> dict[str, Any]:
    path = store.artifact_path(session_id, f"general/candidate_validation.rev{revision}.v1.json")
    return json.loads(path.read_text(encoding="utf-8"))


def run_acceptance(*, live: bool, report_path: Path | None = None, fallback_only: bool = False) -> dict[str, Any]:
    """Run seven isolated sessions and return the safe aggregate report."""
    inner_client: Any = YouComClient() if live else _OfflineYouCom()
    if live and not inner_client.api_key:
        raise RuntimeError("--live requires YDC_API_KEY")
    client = _RecordingYouCom(inner_client)
    calls: list[dict[str, Any]] = []
    call_cursor = 0
    per_seed: list[dict[str, Any]] = []
    hard_failures: list[str] = []
    duplicate_rejections = input_candidates = accepted_total = held_duplicate_accepted = 0
    pre2010_accepted = noncomic_accepted = invalid_year_labels = topup_count = 0
    held_keys = {_issue_key(issue) for issue in HELD_ISSUES}

    with tempfile.TemporaryDirectory(prefix="scout-acceptance-") as temporary_root:
        store = SessionStore(Path(temporary_root) / "sessions")
        planner = (lambda *_args: None) if (not live or fallback_only) else None
        workflow = ScoutWorkflow(store=store, client=client, planner=planner)
        runs = _acceptance_runs()
        for name, mode, intent in runs:
            session = workflow.start(mode, intent)
            if name == "rescout_batman_held":
                _prime_rescout(store, session.id)
                workflow.rescout_keeping_selected(session.id)
            workflow.run_general(session.id)
            final = store.load(session.id)
            validation = _read_validation(store, session.id, final.revision)
            audit_path = store.artifact_path(session.id, "audit.jsonl")
            audits = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
            completed = next((x for x in reversed(audits) if x.get("event") == "general_research_completed"), {})
            detail = completed.get("detail", {})
            plan_source = str(detail.get("plan_source", "unknown"))
            report_candidates = json.loads(store.artifact_path(session.id, f"general/candidates.rev{final.revision}.v1.json").read_text(encoding="utf-8")).get("candidates", [])
            accepted = [c for c in report_candidates if c.get("id", "").startswith((f"r{final.revision}-", f"topup-r{final.revision}-"))]
            counts = Counter(str(item.get("reason", "")) for item in validation.get("rejected", []) if isinstance(item, dict))
            duplicate_rejections += counts["duplicate_issue_key"]
            input_candidates += int(validation.get("input_candidate_count", 0))
            valid_accepted = 0
            for candidate in accepted:
                label = str(candidate.get("series_issue_year", ""))
                violations = _accepted_candidate_violations(candidate)
                valid_accepted += int(not violations)
                if "issue_label_unparseable_or_year_missing" in violations:
                    invalid_year_labels += 1
                if "pre_2010" in violations:
                    pre2010_accepted += 1
                if "non_comic" in violations:
                    noncomic_accepted += 1
                if name == "rescout_batman_held" and _issue_key(label) in held_keys:
                    held_duplicate_accepted += 1
            accepted_total += valid_accepted
            topup_used = bool(detail.get("topup_used"))
            topup_count += int(topup_used)
            recent_calls = client.calls[call_cursor:]
            call_cursor += len(recent_calls)
            prompt_hard = bool(recent_calls) and all(x.get("hard_rules_first", False) for x in recent_calls)
            efforts_standard = bool(recent_calls) and all(x.get("effort") == "standard" for x in recent_calls)
            if not prompt_hard:
                hard_failures.append(name)
            if not efforts_standard:
                hard_failures.append(f"{name}:effort")
            per_seed.append({
                "case": name,
                "seed_fidelity": (
                    "verbatim_source_wording"
                    if name in {"qa_batman_no_kill_breaks", "rescout_batman_held"}
                    else "reconstructed_from_topic_summary"
                ),
                "plan_source": plan_source,
                "research_calls": len(recent_calls),
                "accepted_new_valid": valid_accepted,
                "duplicate_rejected": counts["duplicate_issue_key"],
                "input_candidates": int(validation.get("input_candidate_count", 0)),
                "hard_rules_first": prompt_hard,
                "standard_effort": efforts_standard,
                "topup_used": topup_used,
                "held_duplicate_accepted": sum(1 for c in accepted if name == "rescout_batman_held" and _issue_key(str(c.get("series_issue_year", ""))) in held_keys),
            })

    total_calls = sum(row["research_calls"] for row in per_seed)
    duplicate_rate = duplicate_rejections / input_candidates if input_candidates else None
    no_candidates = input_candidates == 0
    new_per_call = accepted_total / total_calls if total_calls and not no_candidates else None
    acceptance_evaluated = bool(live and not no_candidates)
    evaluation_status = (
        "dry_run_smoke_only" if not live else
        "no_candidates" if no_candidates else
        "evaluated"
    )
    hard_invariants = {
        "hard_rules_first_all_calls": not hard_failures,
        "pre_2010_accepted_zero": pre2010_accepted == 0,
        "non_comic_accepted_zero": noncomic_accepted == 0,
        "qa_issue_labels_parseable_and_year_known": invalid_year_labels == 0,
        "held_duplicate_accepted_zero": held_duplicate_accepted == 0,
        "all_effort_standard": not any(item.endswith(":effort") for item in hard_failures),
    }
    report = {
        "schema_version": 1,
        "mode": "live" if live else "dry_run",
        "planner_mode": "fallback_only" if fallback_only or not live else "production_planner",
        "accepted_candidate_scope": "new candidates only; carried held cards are excluded",
        "seed_fidelity_note": (
            "Batman wording is copied from todo/qa-scout/2026-10-03-youcom-large-prompt-test.md; "
            "the other five question texts reconstruct the topic summaries in "
            "todo/qa-scout/2026-10-03-scout-strategy-ab-test.md, so per-seed and aggregate "
            "statistical metrics are indicative only."
        ),
        "acceptance_evaluated": acceptance_evaluated,
        "evaluation_status": evaluation_status,
        "no_candidates": no_candidates,
        "cases": per_seed,
        "metrics": {
            "hard_rules_first": hard_invariants["hard_rules_first_all_calls"],
            "pre_2010_accepted": pre2010_accepted,
            "non_comic_accepted": noncomic_accepted,
            "qa_issue_label_unparseable_or_year_missing": invalid_year_labels,
            "held_duplicate_accepted": held_duplicate_accepted,
            "new_valid_per_standard_call": round(new_per_call, 4) if new_per_call is not None else None,
            "duplicate_rate": round(duplicate_rate, 4) if duplicate_rate is not None else None,
            "effort_standard": hard_invariants["all_effort_standard"],
            "topup_count": topup_count,
        },
        "hard_invariants": hard_invariants,
        "hard_failures": hard_failures,
        "statistical_warnings": [],
    }
    # Small sample thresholds are informational only, as §5 says these are indicators.
    if duplicate_rate is not None and duplicate_rate > 0.15:
        report["statistical_warnings"].append("duplicate_rate_above_approx_15_percent")
    if new_per_call is not None and new_per_call < 2:
        report["statistical_warnings"].append("new_valid_per_call_below_approx_2")
    if no_candidates:
        report["statistical_warnings"].append("no_candidates_observed")
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="offline fake API; no network or cost")
    group.add_argument("--live", action="store_true", help="explicitly run seven live You.com cases")
    parser.add_argument("--report", type=Path, default=Path("scout_acceptance_report.json"))
    parser.add_argument("--fallback-only", action="store_true", help="use fixed prompt fallback instead of the planner")
    args = parser.parse_args(argv)
    try:
        report = run_acceptance(live=args.live, report_path=args.report, fallback_only=args.fallback_only)
    except Exception as exc:
        # Upstream exceptions can contain request/response material; keep stderr
        # safe even when the report could not be completed.
        print(json.dumps({"error": exc.__class__.__name__}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if all(report["hard_invariants"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
