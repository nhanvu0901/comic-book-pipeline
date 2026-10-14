"""Comprehensive end-to-end tests for the Q&A dual format design (list and direct).

Covers:
1. Discovery proposes provisional format ('list' or 'direct') with source evidence.
2. Format persistence and selection: research confirms and persists the format,
   and editing a question prevents keeping a stale format label.
3. Direct research & production gates: permits 2-5 story beats (instead of forcing >=3),
   including beats within the same comic/story.
4. Project creation & answer_summary thesis: direct project creation persists real sourced thesis
   (never setting answer_summary = session.user_intent).
5. Stage 3 writer behavior on a What-is concept question: handles direct archetype,
   avoids thesis leak in hook, and requires plain causal answer landing.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import stages.research_scout.project_factory as factory
from stages.question_archetype import (
    QA_FORMAT_DIRECT,
    QA_FORMAT_LIST,
    is_direct_format,
    question_archetype,
)
from stages.research_scout.models import ResearchSession, ScoutMode, SessionState
from stages.research_scout.planner import ResearchPlan
from stages.research_scout.storage import SessionStore
from stages.research_scout.workflow import ScoutWorkflow
from stages.stage_3.explore_answer import (
    _CAUSAL_MARKER_RE,
    _build_hook,
    _validate_explore_scenes,
)
from stages.stage_3.schema import Beat


def _wire_roots(tmp_path, monkeypatch):
    sessions_root = tmp_path / "research-sessions"
    projects_root = tmp_path / "projects"

    def get_project_dirs(slug):
        root = projects_root / slug
        root.mkdir(parents=True, exist_ok=True)
        return {"root": root}

    monkeypatch.setattr(factory.config, "RESEARCH_SESSIONS_ROOT", sessions_root)
    monkeypatch.setattr(factory, "get_project_dirs", get_project_dirs)
    monkeypatch.setattr(factory.answer_research, "get_project_dirs", get_project_dirs)
    return sessions_root, projects_root


# ─────────────────────────────────────────────────────────────────────────────
# 1. Discovery proposes format with source evidence
# ─────────────────────────────────────────────────────────────────────────────

def test_discovery_proposes_formats_with_evidence(tmp_path, monkeypatch):
    sessions_root, _ = _wire_roots(tmp_path, monkeypatch)
    store = SessionStore(sessions_root)

    raw_candidates = [
        {
            "question": "Why did Doctor Strange lose the Sorcerer Supreme title?",
            "proposed_format": "direct",
            "source_evidence": "Doctor Strange (2015) #381 shows Loki winning the title through Vishanti deception.",
            "character": "Doctor Strange",
            "series_issue_year": "Doctor Strange #381 (2017)",
            "angle": "mythology_lore",
        },
        {
            "question": "Which heroes have lifted Thor's hammer Mjolnir?",
            "proposed_format": "list",
            "source_evidence": "Beta Ray Bill, Captain America, and Jane Foster in various issues.",
            "character": "Thor",
            "series_issue_year": "Thor #337 (1983)",
            "angle": "rule_breaking_feats",
        },
    ]

    class FakeYouCom:
        def research(self, prompt, schema, profile, *, effort="standard"):
            return {
                "output": {
                    "content": json.dumps({"candidates": raw_candidates})
                }
            }

    workflow = ScoutWorkflow(
        store=store, client=FakeYouCom(),
        qa_issue_verifier=lambda candidate, fmt: True,
    )
    discovered = workflow.discover_questions(ScoutMode.QA, count=2)
    assert len(discovered) == 2

    # Check direct question
    assert discovered[0]["proposed_format"] == "direct"
    assert "Doctor Strange" in discovered[0]["character"]
    assert "Doctor Strange #381" in discovered[0]["series_issue_year"]
    assert "Loki" in discovered[0]["source_evidence"]

    # Check list question
    assert discovered[1]["proposed_format"] == "list"
    assert "Beta Ray Bill" in discovered[1]["source_evidence"]


# ─────────────────────────────────────────────────────────────────────────────
# 2. Format persistence & avoiding stale format label on edit
# ─────────────────────────────────────────────────────────────────────────────

def test_format_persistence_and_no_stale_label_on_edit(tmp_path, monkeypatch):
    sessions_root, _ = _wire_roots(tmp_path, monkeypatch)
    store = SessionStore(sessions_root)

    # 1. Start a direct question session
    direct_q = "Why did Wolverine lose his healing factor?"
    mock_plan = ResearchPlan(
        format="direct",
        unit="event",
        cardinality="options",
        research_prompt="Find why Wolverine lost his healing factor",
    )
    fake_client1 = MagicMock()
    fake_client1.research.return_value = {
        "output": {
            "content": json.dumps({
                "answer_thesis": "A sentient alien virus shut down his healing factor.",
                "candidates": [
                    {
                        "entity": "Microverse Virus",
                        "series_issue_year": "Wolverine #1 (2014)",
                        "summary": "Virus burns out regeneration.",
                        "what_visibly_happens": "Wolverine bleeding.",
                        "evidence_urls": ["https://source.test/1"],
                    },
                    {
                        "entity": "Doctor Cornelius",
                        "series_issue_year": "Death of Wolverine #4 (2014)",
                        "summary": "Logan dies coated in molten metal.",
                        "what_visibly_happens": "Logan encased in metal.",
                        "evidence_urls": ["https://source.test/2"],
                    },
                ],
            })
        }
    }
    workflow = ScoutWorkflow(store=store, client=fake_client1, planner=lambda intent, fb, mode: mock_plan)
    sess = workflow.start(ScoutMode.QA, direct_q)

    updated = workflow.run_general(sess.id)
    assert updated.format == "direct"
    assert store.load(sess.id).format == "direct"

    # 2. If user edits the question text into a list question,
    # the stale direct label cannot be kept: new session independently confirms list.
    edited_q = "Which mutants have survived without their powers?"
    mock_plan2 = ResearchPlan(
        format="list",
        unit="character",
        cardinality="options",
        research_prompt="Find mutants who survived without powers",
    )
    fake_client2 = MagicMock()
    fake_client2.research.return_value = {
        "output": {
            "content": json.dumps({
                "candidates": [
                    {"entity": "Storm", "series_issue_year": "Uncanny X-Men #201 (1986)", "summary": "Beats Cyclops.", "what_visibly_happens": "Storm fights.", "evidence_urls": ["https://s.test/1"]},
                    {"entity": "Magneto", "series_issue_year": "X-Men #1 (1991)", "summary": "Survives.", "what_visibly_happens": "Magneto lives.", "evidence_urls": ["https://s.test/2"]},
                    {"entity": "Rogue", "series_issue_year": "X-Men #100 (2000)", "summary": "Fights on.", "what_visibly_happens": "Rogue attacks.", "evidence_urls": ["https://s.test/3"]},
                ],
            })
        }
    }
    workflow2 = ScoutWorkflow(store=store, client=fake_client2, planner=lambda intent, fb, mode: mock_plan2)
    sess2 = workflow2.start(ScoutMode.QA, edited_q)

    updated2 = workflow2.run_general(sess2.id)
    assert updated2.format == "list"
    assert store.load(sess2.id).format == "list"


# ─────────────────────────────────────────────────────────────────────────────
# 3. Direct research & production gates (2-5 beats, same comic allowed)
# ─────────────────────────────────────────────────────────────────────────────

def test_direct_production_gates_allows_two_beats_in_same_arc(tmp_path, monkeypatch):
    _wire_roots(tmp_path, monkeypatch)

    beats = [
        {
            "id": "beat-1",
            "entity": "Loki",
            "title": "Loki",
            "series_issue_year": "Doctor Strange #381 (2017)",
            "source_comic": "Doctor Strange #381",
            "source_year": "2017",
            "how_or_why": "Loki wins the Vishanti tournament through illusion and claims the title.",
            "drawable_moment": "Loki wearing the Cloak of Levitation with the Eye of Agamotto",
            "verification_note": "marvel.fandom.com + issue page",
            "surprise_level": "medium",
            "reader_url": "https://batcave.biz/reader/101/201",
            "evidence_urls": ["https://marvel.fandom.com/wiki/Doctor_Strange_Vol_1_381"],
        },
        {
            "id": "beat-2",
            "entity": "Doctor Strange",
            "title": "Doctor Strange",
            "series_issue_year": "Doctor Strange #385 (2018)",
            "source_comic": "Doctor Strange #385",
            "source_year": "2018",
            "how_or_why": "Strange bargains with the World Tree and uses Sentry's power to reclaim the mantle.",
            "drawable_moment": "Strange confronting Loki in a clash of godlike magic",
            "verification_note": "marvel.fandom.com + issue page",
            "surprise_level": "high",
            "reader_url": "https://batcave.biz/reader/101/205",
            "evidence_urls": ["https://marvel.fandom.com/wiki/Doctor_Strange_Vol_1_385"],
        },
    ]

    gates = [
        {
            "candidate_id": "beat-1",
            "verdict": "confirmed",
            "reason": "Doctor Strange #381 verifies Loki claiming the Sorcerer Supreme title.",
            "evidence_urls": ["https://marvel.fandom.com/wiki/Doctor_Strange_Vol_1_381"],
        },
        {
            "candidate_id": "beat-2",
            "verdict": "confirmed",
            "reason": "Doctor Strange #385 verifies Strange reclaiming the mantle.",
            "evidence_urls": ["https://marvel.fandom.com/wiki/Doctor_Strange_Vol_1_385"],
        },
    ]

    store = SessionStore(tmp_path / "research-sessions")
    session = ResearchSession(
        id="strange-sorcerer-session",
        mode=ScoutMode.QA,
        user_intent="Why did Doctor Strange lose the Sorcerer Supreme title?",
        state=SessionState.PRODUCTION_GATES,
        format="direct",
        selected_specific_candidate_ids=["beat-1", "beat-2"],
    )
    store.save(session)
    store.write_artifact(
        session.id,
        "general/candidates.v1.json",
        {
            "answer_thesis": "Loki tricked the Vishanti to claim the title until Strange used godlike magic to win it back.",
            "candidates": beats,
        },
    )
    store.write_artifact(session.id, "specific/evidence_gate.v1.json", {"gates": gates})

    # create_project_from_session should succeed with 2 beats
    slug = factory.create_project_from_session(session.id, "strange-sorcerer")

    # Verify answer_context.json
    answer_path = tmp_path / "projects" / slug / "answer_context.json"
    assert answer_path.exists()
    answer_ctx = json.loads(answer_path.read_text())

    assert answer_ctx["format"] == "direct"
    # Ensure answer_summary is the real thesis, NOT session.user_intent!
    assert answer_ctx["answer_summary"] == "Loki tricked the Vishanti to claim the title until Strange used godlike magic to win it back."
    assert answer_ctx["answer_summary"] != session.user_intent
    assert len(answer_ctx["items"]) == 2
    assert answer_ctx["items"][0]["entity"] == "Loki"
    assert answer_ctx["items"][1]["entity"] == "Doctor Strange"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Stage 3 writer on What-is concept question
# ─────────────────────────────────────────────────────────────────────────────

def test_writer_what_is_concept_question():
    question = "What is the Anti-Life Equation?"
    archetype = question_archetype(question)
    assert archetype == "explain"
    assert is_direct_format(archetype)

    answer_context = {
        "question": question,
        "format": "direct",
        "answer_summary": "The Anti-Life Equation is a mathematical proof that despair and submission are absolute.",
        "items": [
            {
                "rank": 1,
                "entity": "Mister Miracle",
                "how_or_why": "Scott Free resists the equation, proving it strips hope from living minds.",
                "source_comic": "Forever People #5",
            },
            {
                "rank": 2,
                "entity": "Darkseid",
                "how_or_why": "Darkseid broadcasts it across the universe, turning all thought into obedience.",
                "source_comic": "Final Crisis #4",
            },
        ],
    }

    # Hook should end with ? and NOT leak the thesis or add "{entity} is one answer."
    hook = _build_hook(question, answer_context, archetype="explain")
    assert hook == "What is the Anti-Life Equation?"
    assert "one answer" not in hook
    assert "mathematical proof" not in hook

    # Test scene validation with causal marker for concept questions
    beats = [
        Beat(id=1, function="COLD_OPEN", name="Mister Miracle", page_refs=[1], summary="Resists equation", cause="Resists equation"),
        Beat(id=2, function="LANDING", name="Darkseid", page_refs=[5], summary="Broadcasts equation", cause="Broadcasts equation"),
    ]

    # Compliant scenes with 2 per beat (4 total)
    scenes = [
        {"text": "Mister Miracle faces the cosmic equation.", "visual_beats": ["Mister Miracle faces the cosmic equation."]},
        {"text": "He feels every shred of hope drain away.", "visual_beats": ["He feels every shred of hope drain away."]},
        {"text": "Darkseid unleashes the formula across reality.", "visual_beats": ["Darkseid unleashes the formula across reality."]},
        {"text": "Which is what strips all free will from every living mind.", "visual_beats": ["Which is what strips all free will from every living mind."]},
    ]

    issues = _validate_explore_scenes(scenes, beats, archetype="explain")
    # Should not complain about missing answer because "Which is what" is a valid causal marker
    assert not any("final scene never states the ANSWER" in issue for issue in issues)

    # Check causal marker regex directly
    assert _CAUSAL_MARKER_RE.search("Which is what strips free will.")
    assert _CAUSAL_MARKER_RE.search("That's what Darkseid sought.")
    assert _CAUSAL_MARKER_RE.search("The truth is, free will becomes mathematically impossible.")
