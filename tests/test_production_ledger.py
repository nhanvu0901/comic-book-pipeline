import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from stages.research_scout.ledger import Ledger
from stages.research_scout.production_ledger import project_event, record_milestone


def windows_ledger(path: Path) -> Ledger:
    return Ledger(path, _writer_guard=lambda: True)


def _project(root: Path, name: str, *, mode: str, narration: str = "Approved words") -> Path:
    project = root / name
    project.mkdir(parents=True)
    if mode == "qa":
        (project / "answer_context.json").write_text(json.dumps({
            "question": "Who helped Spider-Man?",
            "items": [{"source_comic": "Amazing Spider-Man #1 (2022)"}, {"source_comic": "Miles Morales #2 (2023)"}],
        }))
    elif mode == "micro":
        (project / "scout_candidate.json").write_text(json.dumps({"series_issue_year": "Batman #77 (2019)"}))
        (project / "comic_context.json").write_text(json.dumps({"series_start_year": 2016, "pipeline_mode": "micro_moment", "title": "Batman #77 (2019)"}))
    else:
        (project / "comic_context.json").write_text(json.dumps({"series": "Batman", "issue": 77, "year": 2019, "series_start_year": 2016}))
    (project / "narration.json").write_text(json.dumps({"script": narration}))
    return project


def _write_valid_mp4(path: Path) -> None:
    import shutil
    import subprocess
    import pytest
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe unavailable")
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=16x16:d=0.04",
                    "-c:v", "mpeg4", "-y", str(path)], check=True, timeout=15)


def test_project_event_extracts_each_mode(tmp_path):
    root = tmp_path / "projects"
    qa = _project(root, "qa-one", mode="qa")
    micro = _project(root, "micro-one", mode="micro")
    recap = _project(root, "recap-one", mode="recap")
    qa_event = project_event("qa-one", "in_progress", projects_root=root)
    micro_event = project_event("micro-one", "produced", projects_root=root)
    recap_event = project_event("recap-one", "produced", projects_root=root)
    assert qa_event["mode"] == "qa"
    assert len(qa_event["keys"]) == 2
    assert qa_event["refs"]["project"] == "qa-one"
    assert qa_event["refs"]["narration_sha256"] == hashlib.sha256((qa / "narration.json").read_bytes()).hexdigest()
    assert "script" not in qa_event and "Approved words" not in json.dumps(qa_event)
    assert micro_event["mode"] == "micro" and micro_event["key"] == "batman|2016|77"
    assert recap_event["mode"] == "recap" and recap_event["key"] == "batman|2016|77"



def test_malformed_qa_item_makes_identity_unresolved(tmp_path):
    root = tmp_path / "projects"
    project = _project(root, "qa-bad", mode="qa")
    context = json.loads((project / "answer_context.json").read_text())
    context["items"].append("not an object")
    (project / "answer_context.json").write_text(json.dumps(context))
    assert project_event("qa-bad", "in_progress", projects_root=root) is None


def test_micro_without_publication_year_is_unresolved(tmp_path):
    root = tmp_path / "projects"
    project = _project(root, "micro-no-year", mode="micro")
    (project / "scout_candidate.json").write_text(json.dumps({"series_issue_year": "Batman #77"}))
    (project / "comic_context.json").write_text(json.dumps({"pipeline_mode": "micro_moment", "series": "Batman"}))
    assert project_event("micro-no-year", "in_progress", projects_root=root) is None



def test_micro_start_year_is_not_publication_year(tmp_path):
    root = tmp_path / "projects"
    project = _project(root, "micro-start-year", mode="micro")
    (project / "scout_candidate.json").write_text(json.dumps({"series_issue_year": "Batman (2016) #77"}))
    (project / "comic_context.json").write_text(json.dumps({"pipeline_mode": "micro_moment"}))
    assert project_event("micro-start-year", "in_progress", projects_root=root) is None


def test_micro_keeps_separate_publication_year_in_event_label(tmp_path):
    root = tmp_path / "projects"
    project = _project(root, "micro-source-year", mode="micro")
    (project / "scout_candidate.json").write_text(json.dumps({
        "series_issue_year": "Batman #77", "source_year": "2019",
    }))
    (project / "comic_context.json").write_text(json.dumps({"pipeline_mode": "micro_moment"}))
    event = project_event("micro-source-year", "in_progress", projects_root=root)
    assert event["label"] == "Batman #77 (2019)"
    assert event["key"] == "batman||77"


def test_micro_mode_comes_from_context_without_candidate_file(tmp_path):
    root = tmp_path / "projects"
    project = _project(root, "micro-context", mode="recap")
    (project / "comic_context.json").write_text(json.dumps({
        "pipeline_mode": "micro_moment", "series_issue_year": "Batman #77 (2019)"
    }))
    event = project_event("micro-context", "in_progress", projects_root=root)
    assert event["mode"] == "micro"


def test_recap_composes_series_issue_and_publication_year(tmp_path):
    root = tmp_path / "projects"
    _project(root, "recap-fields", mode="recap")
    (root / "recap-fields" / "comic_context.json").write_text(json.dumps({
        "title": "Dark Night", "series": "Batman", "issue": 77, "year": 2019,
    }))
    event = project_event("recap-fields", "produced", projects_root=root)
    assert event["mode"] == "recap"
    assert event["key"] == "batman||77"


def test_repeat_approval_is_idempotent(tmp_path):
    root = tmp_path / "projects"
    _project(root, "p", mode="micro")
    ledger = windows_ledger(tmp_path / "ledger.db")
    assert record_milestone("p", "in_progress", projects_root=root, ledger=ledger) == "recorded"
    assert record_milestone("p", "in_progress", projects_root=root, ledger=ledger) == "already_recorded"
    assert ledger.count_events() == 1


def test_changed_script_adds_one_new_milestone(tmp_path):
    root = tmp_path / "projects"
    project = _project(root, "p", mode="micro")
    ledger = windows_ledger(tmp_path / "ledger.db")
    assert record_milestone("p", "in_progress", projects_root=root, ledger=ledger) == "recorded"
    (project / "narration.json").write_text(json.dumps({"script": "Revised approved words"}))
    assert record_milestone("p", "in_progress", projects_root=root, ledger=ledger) == "recorded"
    assert ledger.count_events() == 2


def test_unresolved_identity_is_visible(tmp_path):
    root = tmp_path / "projects"
    project = root / "p"
    project.mkdir(parents=True)
    (project / "comic_context.json").write_text(json.dumps({"title": "Unknown story"}))
    (project / "narration.json").write_text(json.dumps({"script": "secret"}))
    ledger = windows_ledger(tmp_path / "ledger.db")
    assert project_event("p", "in_progress", projects_root=root) is None
    assert record_milestone("p", "in_progress", projects_root=root, ledger=ledger) == "unresolved_identity"
    assert ledger.count_events() == 0


def test_import_projects_does_not_mark_partial_nonempty_mp4_produced(tmp_path):
    root = tmp_path / "projects"
    project = _project(root, "partial", mode="micro")
    (project / "final.mp4").write_bytes(b"partial ffmpeg output")
    ledger = windows_ledger(tmp_path / "ledger.db")
    result = ledger.import_projects(root)
    assert result.inserted == 0
    assert result.skipped == 1


def test_render_verifier_accepts_matching_marker_and_probes_legacy_mp4(tmp_path, monkeypatch):
    import shutil
    import subprocess
    import pytest
    from stages.research_scout import production_ledger
    from ui import bridge

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg/ffprobe unavailable")
    project = tmp_path / "render"
    project.mkdir()
    valid = project / "final.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=16x16:d=0.04",
                    "-c:v", "mpeg4", "-y", str(valid)], check=True, timeout=15)
    stat_sig = list(bridge._render_signature(valid))
    (project / "state.json").write_text(json.dumps({"verified_final_signature": stat_sig}))
    assert production_ledger.is_verified_final(project)

    (project / "state.json").write_text("{}")
    production_ledger._clear_verified_final_cache()
    assert production_ledger.is_verified_final(project)

    (project / "final.mp4").write_bytes(b"partial ffmpeg output")
    production_ledger._clear_verified_final_cache()
    assert not production_ledger.is_verified_final(project)


def test_render_verifier_handles_corrupt_state_json(tmp_path):
    from stages.research_scout import production_ledger
    project = tmp_path / "corrupt-state"
    project.mkdir()
    (project / "state.json").write_bytes(b"\xff")
    (project / "final.mp4").write_bytes(b"partial output")
    assert not production_ledger.is_verified_final(project)


def test_render_marker_survives_stale_app_state_save_and_rejects_replaced_file(tmp_path, monkeypatch):
    import json
    from stages.research_scout import production_ledger
    from ui import bridge, state

    projects = tmp_path / "projects"
    project = projects / "p"
    project.mkdir(parents=True)
    final = project / "final.mp4"
    final.write_bytes(b"already verified output")
    monkeypatch.setattr(state, "PROJECTS_ROOT", projects)
    monkeypatch.setattr(production_ledger.shutil, "which", lambda _name: None)

    signature = bridge._render_signature(final)
    bridge._mark_render_verified(project, signature, lambda _message: None)
    state.save_state(state.AppState(project_name="p"))

    sidecar = project / "final.mp4.verified.json"
    assert json.loads(sidecar.read_text())["verified_final_signature"] == list(signature)
    assert production_ledger.is_verified_final(project)

    final.write_bytes(b"replaced output with different stat")
    production_ledger._clear_verified_final_cache()
    assert not production_ledger.is_verified_final(project)


def test_concurrent_repeat_milestone_is_atomic(tmp_path):
    root = tmp_path / "projects"
    _project(root, "p", mode="micro")
    ledger = windows_ledger(tmp_path / "ledger.db")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: record_milestone("p", "in_progress", projects_root=root, ledger=ledger), range(8)))
    assert results.count("recorded") == 1
    assert results.count("already_recorded") == 7
    assert ledger.count_events() == 1


def test_each_render_path_records_produced_once(tmp_path, monkeypatch):
    import sys
    import types
    import subprocess
    from ui import bridge
    from stages.research_scout import production_ledger

    projects = tmp_path / "projects"
    projects.mkdir()
    fixture = tmp_path / "valid.mp4"
    _write_valid_mp4(fixture)
    rendered_bytes = fixture.read_bytes()
    monkeypatch.setattr(bridge, "PROJECTS_ROOT", projects)
    _project(projects, "stage5", mode="micro")
    _project(projects, "stage6", mode="micro")
    ledger = windows_ledger(tmp_path / "ledger.db")
    monkeypatch.setattr(production_ledger, "Ledger", lambda: ledger)
    monkeypatch.setattr(production_ledger, "_probe_mp4", lambda _path: True)
    pipeline = types.ModuleType("stages.stage_5.pipeline")
    def render_stage5(name, force):
        output = projects / name / "final.mp4"
        output.write_bytes(rendered_bytes)
        return output
    pipeline.assemble_project = render_stage5
    monkeypatch.setitem(sys.modules, "stages.stage_5.pipeline", pipeline)
    bridge.run_stage_5("stage5", lambda _line: None)
    bridge.run_stage_5("stage5", lambda _line: None)

    class Process:
        def __init__(self, cmd, **kwargs):
            self.cmd = cmd
            self.stdout = []
            if "stages.stage_5" in cmd:
                (projects / "stage6" / "final.mp4").write_bytes(rendered_bytes)

        def wait(self):
            return 0

    monkeypatch.setattr(subprocess, "Popen", Process)
    bridge.run_stage6_render("stage6", lambda _line: None)
    bridge.run_stage6_render("stage6", lambda _line: None)
    rows = [event for event in ledger.events() if event["kind"] == "produced"]
    assert len(rows) == 2
    assert {event["refs"]["project"] for event in rows} == {"stage5", "stage6"}


def test_failed_render_records_nothing(tmp_path, monkeypatch):
    import sys
    import types
    import subprocess
    import pytest
    from ui import bridge

    projects = tmp_path / "projects"
    projects.mkdir()
    monkeypatch.setattr(bridge, "PROJECTS_ROOT", projects)
    project = projects / "p"
    project.mkdir()
    (project / "final.mp4").write_bytes(b"stale")
    recorded = []
    monkeypatch.setattr("stages.research_scout.production_ledger.record_milestone",
                        lambda *args, **kwargs: recorded.append(args))

    class Process:
        def __init__(self, cmd, **kwargs):
            self.cmd = cmd
            self.stdout = []

        def wait(self):
            return 0

    monkeypatch.setattr(subprocess, "Popen", Process)
    with pytest.raises(RuntimeError, match="final.mp4"):
        bridge.run_stage6_render("p", lambda _line: None)
    assert recorded == []


def test_empty_stage5_output_records_nothing(tmp_path, monkeypatch):
    import sys
    import types
    import pytest
    from ui import bridge

    projects = tmp_path / "projects"
    projects.mkdir()
    monkeypatch.setattr(bridge, "PROJECTS_ROOT", projects)
    project = projects / "p"
    project.mkdir()
    pipeline = types.ModuleType("stages.stage_5.pipeline")
    def assemble(_name, force):
        output = project / "final.mp4"
        output.write_bytes(b"")
        return output
    pipeline.assemble_project = assemble
    monkeypatch.setitem(sys.modules, "stages.stage_5.pipeline", pipeline)
    recorded = []
    monkeypatch.setattr("stages.research_scout.production_ledger.record_milestone",
                        lambda *args, **kwargs: recorded.append(args))
    with pytest.raises(RuntimeError, match="final.mp4 is missing or empty"):
        bridge.run_stage_5("p", lambda _line: None)
    assert recorded == []


def test_partial_nonempty_stage5_output_is_not_marked_verified(tmp_path, monkeypatch):
    import sys
    import types
    import pytest
    from ui import bridge

    projects = tmp_path / "projects"
    projects.mkdir()
    project = projects / "p"
    project.mkdir()
    monkeypatch.setattr(bridge, "PROJECTS_ROOT", projects)
    pipeline = types.ModuleType("stages.stage_5.pipeline")
    def assemble(_name, force):
        output = project / "final.mp4"
        output.write_bytes(b"partial ffmpeg output")
        return output
    pipeline.assemble_project = assemble
    monkeypatch.setitem(sys.modules, "stages.stage_5.pipeline", pipeline)
    with pytest.raises(RuntimeError, match="failed MP4 verification"):
        bridge.run_stage_5("p", lambda _line: None)
    state_path = project / "state.json"
    assert not state_path.exists() or "verified_final_signature" not in json.loads(state_path.read_text())


def test_stage5_stale_final_without_marker_records_nothing(tmp_path, monkeypatch):
    import sys
    import types
    import pytest
    from ui import bridge

    projects = tmp_path / "projects"
    project = projects / "p"
    project.mkdir(parents=True)
    output = project / "final.mp4"
    output.write_bytes(b"old render")
    monkeypatch.setattr(bridge, "PROJECTS_ROOT", projects)
    pipeline = types.ModuleType("stages.stage_5.pipeline")
    pipeline.assemble_project = lambda _name, force: output
    monkeypatch.setitem(sys.modules, "stages.stage_5.pipeline", pipeline)
    recorded = []
    monkeypatch.setattr("stages.research_scout.production_ledger.record_milestone",
                        lambda *args, **kwargs: recorded.append(args))
    with pytest.raises(RuntimeError, match="fresh or previously verified"):
        bridge.run_stage_5("p", lambda _line: None)
    assert recorded == []


def test_previously_verified_noop_render_remains_successful(tmp_path, monkeypatch):
    import json
    import subprocess
    from ui import bridge

    projects = tmp_path / "projects"
    project = _project(projects, "p", mode="micro")
    final = project / "final.mp4"
    _write_valid_mp4(final)
    state = {"verified_final_signature": list(bridge._render_signature(final))}
    (project / "state.json").write_text(json.dumps(state))
    monkeypatch.setattr(bridge, "PROJECTS_ROOT", projects)
    recorded = []
    monkeypatch.setattr("stages.research_scout.production_ledger.record_milestone",
                        lambda name, kind, **kwargs: recorded.append((name, kind)) or "already_recorded")

    class Process:
        def __init__(self, cmd, **kwargs):
            self.stdout = []

        def wait(self):
            return 0

    monkeypatch.setattr(subprocess, "Popen", Process)
    assert bridge.run_stage6_render("p", lambda _line: None) == str(final)
    assert recorded == [("p", "produced")]
