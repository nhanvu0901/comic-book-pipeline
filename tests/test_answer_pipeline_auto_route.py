"""answer_pipeline --auto-route (m8): when the router says screen_qa the COMIC research
must not run at all (it would spend an SDK call and fail on a question with no comic)."""
import json

import pytest

import config
import stages.answer_pipeline as ap
import stages.research_scout.router_rules as rr
from stages.stage_1.batcave_verifier import BatcaveCheck


@pytest.fixture
def project_root(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    return tmp_path / "endgame_q"


def _decision(route):
    return rr.RouteDecision(
        route, None,
        batcave_checks=[BatcaveCheck(False, "series", "Some Series", None, 2006, "series not found")],
        clips_found=True, reasons=["test reason"])


@pytest.fixture
def research_spy(monkeypatch):
    calls = []

    def fake_research(question, **kw):
        calls.append(question)
        return {"items": [{"rank": 1}]}

    monkeypatch.setattr("stages.stage_1.answer_research.research_answer", fake_research)
    monkeypatch.setattr("stages.stage_1.answer_research.build_contexts",
                        lambda q, research, project, **kw: (config.PROJECTS_ROOT / project / "answer_context.json",
                                                            config.PROJECTS_ROOT / project / "comic_context.json"))
    monkeypatch.setattr(ap, "_attach_money_target", lambda *a, **k: None)
    return calls


def _args(*extra):
    return ap._parse_args(["--question", "How did the Avengers travel back in time in Endgame?",
                           "--project", "endgame_q", *extra])


def test_screen_qa_route_skips_comic_research_and_records_the_decision(monkeypatch, project_root, research_spy):
    monkeypatch.setattr(rr, "decide_route", lambda q, **k: _decision("screen_qa"))
    args = _args("--auto-route")

    detail = ap._step_research(args, lambda m: None)

    assert research_spy == []                      # comic research never ran
    assert "screen_qa" in detail and "skipped" in detail
    assert args.routed_to == "screen_qa"
    saved = json.loads((project_root / "media_route.json").read_text())
    assert saved["route"] == "screen_qa"
    assert saved["question"] == args.question
    assert saved["decision"]["batcave_checks"][0]["level"] == "series"
    assert saved["decision"]["reasons"] == ["test reason"]


def test_comic_qa_route_still_runs_the_comic_research(monkeypatch, project_root, research_spy):
    monkeypatch.setattr(rr, "decide_route", lambda q, **k: _decision("comic_qa"))
    args = _args("--auto-route")

    ap._step_research(args, lambda m: None)

    assert research_spy == [args.question]
    assert not getattr(args, "routed_to", None)
    assert json.loads((project_root / "media_route.json").read_text())["route"] == "comic_qa"


def test_without_auto_route_the_router_is_never_called(monkeypatch, project_root, research_spy):
    def boom(*a, **k):
        raise AssertionError("router must not run without --auto-route")

    monkeypatch.setattr(rr, "decide_route", boom)
    ap._step_research(_args(), lambda m: None)
    assert research_spy == [_args().question]
    assert not (project_root / "media_route.json").exists()


def test_main_stops_cleanly_after_a_screen_qa_route_and_prints_the_next_command(monkeypatch, capsys):
    calls = []
    for step in ap.STEPS:
        monkeypatch.setattr(ap, f"_step_{step}", (lambda name: lambda a, log: calls.append(name) or f"{name} ok")(step))

    def research(args, log):
        calls.append("research")
        args.routed_to = "screen_qa"
        return "routed to screen_qa; comic research skipped"

    monkeypatch.setattr(ap, "_step_research", research)

    rc = ap.main(["--question", "Q?", "--project", "endgame_q", "--auto-route"])

    out = capsys.readouterr().out
    assert rc == 0
    assert calls == ["research"]                   # download/preprocess/... never reached
    assert "step=research status=ok" in out
    assert "python -m stages.screen_pipeline" in out and "--project endgame_q" in out
