"""Stage 2 must repair missing Q&A reader URLs before downloading pages."""

from __future__ import annotations

import asyncio
import json

import flet as ft

import ui.screens.s2_download as s2_download
import ui.state as ui_state
from stages.stage_1 import answer_research
from stages.research_scout.models import ResearchSession, ScoutMode, SessionState
from ui.state import AppState

from tests.ui_test_doubles import StrictFakePage as FakePage


def _walk(control, depth: int = 0):
    if control is None or depth > 60:
        return
    yield control
    for attr in ("controls", "actions"):
        for child in getattr(control, attr, None) or []:
            yield from _walk(child, depth + 1)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from _walk(content, depth + 1)


def _by_key(root, key: str):
    return next(control for control in _walk(root) if getattr(control, "key", None) == key)


def _run_task(page: FakePage):
    (func,), _kwargs = page.tasks[-1]
    asyncio.run(func())


def _build(monkeypatch, missing, repair):
    page = FakePage()
    state = AppState(project_name="qa-project")
    monkeypatch.setattr(s2_download, "load_raw_pages", lambda _project: [])
    monkeypatch.setattr(s2_download, "get_scout_missing_readers", lambda _project: list(missing), raising=False)
    monkeypatch.setattr(s2_download, "repair_scout_readers", repair, raising=False)
    monkeypatch.setattr(s2_download, "save_state", lambda _state: None)
    root = s2_download.build(page, state, on_go=lambda _stage: None, on_state_change=lambda: None)
    return page, root


def _return_button(root):
    return _by_key(root, "return-to-stage1")


def _sidebar_stage_one(root):
    """The first stage row in three_col's left stepper."""
    return root.controls[0].content.controls[1]


def _returned_session() -> ResearchSession:
    return ResearchSession(
        id="restored-session",
        mode=ScoutMode.QA,
        user_intent="Who has beaten Superman?",
        state=SessionState.CANDIDATE_REVIEW,
        selected_specific_candidate_ids=["candidate-a", "candidate-b", "candidate-c"],
        created_project=None,
    )


def _return_ready_screen(monkeypatch, *, on_go=None):
    page = FakePage()
    state = AppState(
        project_name="custom-project",
        current_stage=2,
        scout_session_id="",  # _finish_create_project deliberately cleared it.
        approved={str(n): True for n in range(1, 7)},
        dirty={"4": True},
    )
    calls = []
    monkeypatch.setattr(s2_download, "load_raw_pages", lambda _project: [])
    monkeypatch.setattr(s2_download, "get_scout_missing_readers", lambda _project: [])
    monkeypatch.setattr(s2_download, "save_state", lambda _state: None)
    monkeypatch.setattr(
        s2_download, "return_scout_project_to_research",
        lambda project: calls.append(project) or _returned_session(),
        raising=False,
    )
    root = s2_download.build(
        page, state, on_go=on_go or (lambda _stage: None), on_state_change=lambda: None,
    )
    return page, state, root, calls


def test_return_button_restores_the_saved_research_session_and_invalidates_pipeline(monkeypatch):
    page, state, root, calls = _return_ready_screen(monkeypatch)

    _return_button(root).on_click(None)
    _run_task(page)

    assert calls == ["custom-project"]
    assert state.scout_session_id == "restored-session"
    assert state.scout_mode == "qa"
    assert state.last_prompt == "Who has beaten Superman?"
    assert state.pipeline_mode == "explore_answer"
    assert state.current_stage == 1
    assert all(not state.is_approved(stage) for stage in range(1, 9))
    assert state.dirty == {}


def test_sidebar_stage_one_uses_the_same_return_flow_as_the_explicit_button(monkeypatch):
    page, state, root, calls = _return_ready_screen(monkeypatch)
    stages = []
    # Rebuild only to wire this test's navigation observation into the sidebar.
    page, state, root, calls = _return_ready_screen(monkeypatch, on_go=stages.append)

    _sidebar_stage_one(root).on_click(None)
    _run_task(page)

    assert calls == ["custom-project"]
    assert stages == [1]
    assert state.scout_session_id == "restored-session"
    assert state.current_stage == 1


def test_with_no_project_loaded_stage_one_is_just_a_step_back(monkeypatch):
    """After "+ New project", or while a research session has not made a project yet, the
    sidebar still reaches Stage 2 — and its Stage 1 row answered "No project loaded —
    cannot restore its Stage 1 research." and stayed put, so the sidebar had no way back.
    With no project there is nothing to restore; both routes simply go back."""
    page = FakePage()
    state = AppState(project_name="", current_stage=2)
    stages = []
    monkeypatch.setattr(s2_download, "load_raw_pages", lambda _project: [])
    monkeypatch.setattr(s2_download, "get_scout_missing_readers", lambda _project: [])
    monkeypatch.setattr(s2_download, "save_state", lambda _state: None)

    def _must_not_restore(_project):
        raise AssertionError("there is no project to return to research")

    monkeypatch.setattr(s2_download, "return_scout_project_to_research", _must_not_restore,
                        raising=False)
    root = s2_download.build(page, state, on_go=stages.append, on_state_change=lambda: None)

    _sidebar_stage_one(root).on_click(None)
    _run_task(page)
    _return_button(root).on_click(None)
    _run_task(page)

    assert stages == [1, 1]


def test_return_error_keeps_the_current_project_and_does_not_navigate(monkeypatch):
    page, state, root, _calls = _return_ready_screen(monkeypatch)
    before = (state.project_name, state.current_stage, dict(state.approved), dict(state.dirty))
    monkeypatch.setattr(
        s2_download, "return_scout_project_to_research",
        lambda _project: (_ for _ in ()).throw(ValueError("Original research session is unavailable")),
        raising=False,
    )

    _return_button(root).on_click(None)
    _run_task(page)

    assert (state.project_name, state.current_stage, state.approved, state.dirty) == before
    assert "Original research session is unavailable" in "\n".join(
        str(control.value or "") for control in _walk(root) if isinstance(control, ft.Text)
    )


def test_return_error_reenables_the_action_for_a_retry(monkeypatch):
    page, state, root, _calls = _return_ready_screen(monkeypatch)
    attempts = []

    def restore(_project):
        attempts.append(_project)
        if len(attempts) == 1:
            raise ValueError("session temporarily unavailable")
        return _returned_session()

    monkeypatch.setattr(s2_download, "return_scout_project_to_research", restore)
    _return_button(root).on_click(None)
    _run_task(page)
    _return_button(root).on_click(None)
    _run_task(page)

    assert attempts == ["custom-project", "custom-project"]
    assert state.current_stage == 1


def test_return_retries_after_state_save_fails_post_restore(monkeypatch):
    navigated = []
    page, state, root, calls = _return_ready_screen(monkeypatch, on_go=navigated.append)
    saves = []

    def save_once_then_succeed(saved_state):
        saves.append(saved_state.scout_session_id)
        if len(saves) == 1:
            raise OSError("state.json temporarily unavailable")

    monkeypatch.setattr(s2_download, "save_state", save_once_then_succeed)

    _return_button(root).on_click(None)
    _run_task(page)
    assert calls == ["custom-project"]
    assert navigated == []
    assert state.scout_session_id == "restored-session"

    _return_button(root).on_click(None)
    _run_task(page)

    assert calls == ["custom-project"]
    assert saves == ["restored-session", "restored-session"]
    assert navigated == [1]


def test_sidebar_return_resumes_the_already_restored_production_form_without_backend(monkeypatch):
    page = FakePage()
    navigated = []
    state = AppState(
        project_name="custom-project", current_stage=2,
        scout_session_id="restored-session", scout_mode="qa",
        last_prompt="Who has beaten Superman?", pipeline_mode="explore_answer",
        returned_scout_project="custom-project", returned_scout_session_id="restored-session",
    )
    monkeypatch.setattr(s2_download, "load_raw_pages", lambda _project: [])
    monkeypatch.setattr(s2_download, "get_scout_missing_readers", lambda _project: [])
    monkeypatch.setattr(s2_download, "save_state", lambda _state: None)
    monkeypatch.setattr(
        s2_download, "return_scout_project_to_research",
        lambda _project: (_ for _ in ()).throw(AssertionError("backend must not run")),
    )
    root = s2_download.build(
        page, state, on_go=navigated.append, on_state_change=lambda: None,
    )

    _sidebar_stage_one(root).on_click(None)
    _run_task(page)

    assert navigated == [1]
    assert state.scout_session_id == "restored-session"
    assert state.current_stage == 2  # app's on_go performs the actual stage assignment.


def test_returned_state_persists_the_exact_session_identity(monkeypatch, tmp_path):
    monkeypatch.setattr(ui_state, "PROJECTS_ROOT", tmp_path)
    state = AppState(project_name="custom-project", approved={"1": True, "2": True})
    session = _returned_session()

    state.return_to_research(session.id, session.mode.value, session.user_intent)
    ui_state.save_state(state)
    loaded = ui_state.load_state("custom-project")

    assert loaded.scout_session_id == session.id
    assert loaded.returned_scout_session_id == session.id
    assert loaded.returned_scout_project == "custom-project"
    assert not loaded.is_approved(1) and not loaded.is_approved(2)


def test_restored_unapproved_stage_one_cannot_download_the_old_context(monkeypatch):
    page = FakePage()
    state = AppState(
        project_name="custom-project", returned_scout_project="custom-project",
        returned_scout_session_id="restored-session",
    )
    monkeypatch.setattr(s2_download, "load_raw_pages", lambda _project: [])
    monkeypatch.setattr(s2_download, "get_scout_missing_readers", lambda _project: [])
    root = s2_download.build(page, state, on_go=lambda _stage: None, on_state_change=lambda: None)

    assert _by_key(root, "stage1-download").disabled is True


def test_missing_reader_panel_names_rank_entity_and_source_and_blocks_download(monkeypatch):
    page, root = _build(
        monkeypatch,
        [{"rank": 2, "entity": "Deadpool", "source_comic": "Deadpool #3 (2008)", "reader_url": ""}],
        lambda *_args, **_kwargs: [],
    )

    field = _by_key(root, "missing-reader-2")
    download = _by_key(root, "stage1-download")
    # The item is named in full on the line above the field; the field's own label stays
    # short (a label carrying the whole title overflowed the field and overprinted itself).
    assert _by_key(root, "missing-reader-title-2").value == "#2 — Deadpool — Deadpool #3 (2008)"
    assert field.label == "Reader URL for #2"
    assert download.disabled is True
    assert _by_key(root, "repair-reader-urls")
    assert not page.tasks


def test_partial_repair_preserves_typed_url_and_keeps_download_blocked(monkeypatch):
    missing = [{"rank": 1, "entity": "Wade", "source_comic": "Deadpool #1 (2012)", "reader_url": ""}]
    calls = []

    def repair(project, reader_urls=None, log=print):
        calls.append((project, reader_urls))
        return list(missing)

    page, root = _build(monkeypatch, missing, repair)
    field = _by_key(root, "missing-reader-1")
    field.value = "https://batcave.biz/reader/12/34"

    _by_key(root, "repair-reader-urls").on_click(None)
    _run_task(page)

    assert calls == [("qa-project", {1: "https://batcave.biz/reader/12/34"})]
    assert field.value == "https://batcave.biz/reader/12/34"
    assert _by_key(root, "stage1-download").disabled is True


def test_successful_repair_enables_stage_one_download(monkeypatch):
    missing = [{"rank": 1, "entity": "Wade", "source_comic": "Deadpool #1 (2012)", "reader_url": ""}]
    page, root = _build(monkeypatch, missing, lambda *_args, **_kwargs: [])

    _by_key(root, "repair-reader-urls").on_click(None)
    _run_task(page)

    assert _by_key(root, "stage1-download").disabled is False


def test_second_repair_submits_only_the_ranks_still_missing(monkeypatch):
    first = [
        {"rank": 1, "entity": "One", "source_comic": "One #1", "reader_url": ""},
        {"rank": 2, "entity": "Two", "source_comic": "Two #2", "reader_url": ""},
    ]
    second = [{"rank": 2, "entity": "Two", "source_comic": "Two #2", "reader_url": ""}]
    calls = []

    def repair(project, reader_urls=None, log=print):
        calls.append((project, reader_urls))
        return list(second) if len(calls) == 1 else []

    page, root = _build(monkeypatch, first, repair)
    _by_key(root, "missing-reader-1").value = "https://batcave.biz/reader/1/1"
    _by_key(root, "missing-reader-2").value = "https://batcave.biz/reader/2/2"
    _by_key(root, "repair-reader-urls").on_click(None)
    _run_task(page)
    _by_key(root, "missing-reader-2").value = "https://batcave.biz/reader/2/2"
    _by_key(root, "repair-reader-urls").on_click(None)
    _run_task(page)

    assert calls == [
        ("qa-project", {
            1: "https://batcave.biz/reader/1/1",
            2: "https://batcave.biz/reader/2/2",
        }),
        ("qa-project", {2: "https://batcave.biz/reader/2/2"}),
    ]


def test_reader_inspection_error_blocks_download_without_inventing_a_rank_zero_item(monkeypatch):
    page = FakePage()
    state = AppState(project_name="qa-project")
    monkeypatch.setattr(s2_download, "load_raw_pages", lambda _project: [])
    monkeypatch.setattr(
        s2_download, "get_scout_missing_readers",
        lambda _project: (_ for _ in ()).throw(ValueError("answer_context.json is malformed")),
    )
    monkeypatch.setattr(s2_download, "save_state", lambda _state: None)

    root = s2_download.build(page, state, on_go=lambda _stage: None, on_state_change=lambda: None)

    assert _by_key(root, "stage1-download").disabled is True
    assert not [control for control in _walk(root) if getattr(control, "key", None) == "missing-reader-0"]
    assert "answer_context.json is malformed" in "\n".join(
        str(control.value or "") for control in _walk(root) if isinstance(control, ft.Text)
    )


def test_click_time_reader_inspection_error_stays_in_the_repair_panel(monkeypatch):
    """A stale initial inspection must not escape the async Download task."""

    page = FakePage()
    state = AppState(project_name="qa-project")
    calls = []

    def inspect(_project):
        calls.append(_project)
        if len(calls) == 1:
            return []
        raise ValueError("answer_context.json changed while this screen was open")

    monkeypatch.setattr(s2_download, "load_raw_pages", lambda _project: [])
    monkeypatch.setattr(s2_download, "get_scout_missing_readers", inspect)
    monkeypatch.setattr(s2_download, "save_state", lambda _state: None)

    root = s2_download.build(page, state, on_go=lambda _stage: None, on_state_change=lambda: None)
    _by_key(root, "stage1-download").on_click(None)
    _run_task(page)

    assert calls == ["qa-project", "qa-project"]
    assert _by_key(root, "stage1-download").disabled is True
    assert "answer_context.json changed while this screen was open" in "\n".join(
        str(control.value or "") for control in _walk(root) if isinstance(control, ft.Text)
    )


def test_ui_repair_uses_the_real_persisted_reader_contract(monkeypatch, tmp_path):
    project_root = tmp_path / "qa-project"
    project_root.mkdir()
    answer = {
        "items": [
            {"rank": 1, "entity": "One", "source_comic": "One #1", "reader_url": ""},
            {"rank": 2, "entity": "Two", "source_comic": "Two #2", "reader_url": "https://batcave.biz/reader/2/2"},
        ]
    }
    (project_root / "answer_context.json").write_text(__import__("json").dumps(answer))
    (project_root / "comic_context.json").write_text("{}")
    monkeypatch.setattr(answer_research, "get_project_dirs", lambda _name: {"root": project_root})
    monkeypatch.setattr(s2_download, "load_raw_pages", lambda _project: [])
    monkeypatch.setattr(s2_download, "save_state", lambda _state: None)
    page = FakePage()
    state = AppState(project_name="qa-project")

    root = s2_download.build(page, state, on_go=lambda _stage: None, on_state_change=lambda: None)
    _by_key(root, "missing-reader-1").value = "https://batcave.biz/reader/1/1"
    _by_key(root, "repair-reader-urls").on_click(None)
    _run_task(page)

    saved = __import__("json").loads((project_root / "answer_context.json").read_text())
    assert [item["rank"] for item in saved["items"]] == [1, 2]
    assert [item["reader_url"] for item in saved["items"]] == [
        "https://batcave.biz/reader/1/1", "https://batcave.biz/reader/2/2",
    ]
    assert _by_key(root, "stage1-download").disabled is False


def test_normal_project_has_no_missing_reader_panel_and_download_stays_enabled(monkeypatch):
    _page, root = _build(monkeypatch, [], lambda *_args, **_kwargs: [])

    assert _by_key(root, "stage1-download").disabled is False
    assert not [control for control in _walk(root) if getattr(control, "key", None) == "repair-reader-urls"]


def test_url_direct_enrich_context_defaults_to_false(monkeypatch):
    _page, root = _build(monkeypatch, [], lambda *_args, **_kwargs: [])
    switches = [c for c in _walk(root) if isinstance(c, ft.Switch) and "Enrich context" in str(c.label or "")]
    assert len(switches) == 1
    assert switches[0].value is False, "enrich context switch should default to False"


def test_url_direct_auto_derives_project_name_when_blank(monkeypatch):
    page = FakePage()
    state = AppState(project_name="")
    monkeypatch.setattr(s2_download, "load_raw_pages", lambda _project: [])
    monkeypatch.setattr(s2_download, "get_scout_missing_readers", lambda _project: [])
    monkeypatch.setattr(s2_download, "save_state", lambda _state: None)

    downloaded = []
    def fake_download_from_url(proj, raw, issues, enrich, log):
        downloaded.append((proj, raw, enrich))
        return [{"label": "#1", "pages": ["/fake/page_01.jpg"]}]

    monkeypatch.setattr(s2_download, "run_stage_download_from_url", fake_download_from_url)

    root = s2_download.build(page, state, on_go=lambda _stage: None, on_state_change=lambda: None)
    text_fields = [c for c in _walk(root) if isinstance(c, ft.TextField)]
    url_field = next(c for c in text_fields if "Comic URL" in str(c.label or ""))
    proj_field = next(c for c in text_fields if "Project name" in str(c.label or ""))
    dl_url_btn = _by_key(root, "download-from-url")

    url_field.value = "https://batcave.biz/reader/31569/223504,https://batcave.biz/reader/30400/213523"
    proj_field.value = ""

    dl_url_btn.on_click(None)
    _run_task(page)

    assert proj_field.value == "comic_31569_223504"
    assert len(downloaded) == 1
    assert downloaded[0][0] == "comic_31569_223504"
    assert downloaded[0][2] is False  # enrich is False


# ─── URL-direct download: the typed project name ────────────────────────────

def _url_direct_screen(monkeypatch, tmp_path, state):
    monkeypatch.setattr(ui_state, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(s2_download, "load_raw_pages", lambda _project: [])
    monkeypatch.setattr(s2_download, "get_scout_missing_readers", lambda _project: [])
    downloads = []

    def _download(project, raw, issues, enrich, log):
        downloads.append(project)
        return []

    monkeypatch.setattr(s2_download, "run_stage_download_from_url", _download)
    page = FakePage()
    root = s2_download.build(page, state, on_go=lambda _s: None, on_state_change=lambda: None)
    fields = {c.label: c for c in _walk(root) if isinstance(c, ft.TextField)}
    fields["Comic URL(s)"].value = "https://batcave.biz/reader/123/456"
    return page, root, fields, downloads


def test_a_typed_project_name_becomes_a_safe_folder_name(monkeypatch, tmp_path):
    """Windows refuses ':' in a folder name, and the save ran before the handler's try —
    so "Ms. Marvel: No Normal" made the button do nothing at all."""
    state = AppState(project_name="", current_stage=2)
    page, root, fields, downloads = _url_direct_screen(monkeypatch, tmp_path, state)
    fields["Project name (created if new)"].value = "Ms. Marvel: No Normal"

    _by_key(root, "download-from-url").on_click(None)
    _run_task(page)

    assert downloads == ["ms_marvel_no_normal"]
    assert state.project_name == "ms_marvel_no_normal"
    assert fields["Project name (created if new)"].value == "ms_marvel_no_normal"
    assert (tmp_path / "ms_marvel_no_normal" / "state.json").exists()


def test_downloading_into_another_project_does_not_carry_this_ones_state(monkeypatch, tmp_path):
    state = AppState(project_name="first-comic", current_stage=2,
                     approved={"1": True, "4": True}, scout_session_id="first-session",
                     pipeline_mode="explore_answer")
    page, root, fields, downloads = _url_direct_screen(monkeypatch, tmp_path, state)
    fields["Project name (created if new)"].value = "second_comic"

    _by_key(root, "download-from-url").on_click(None)
    _run_task(page)

    saved = json.loads((tmp_path / "second_comic" / "state.json").read_text())
    assert saved["approved"] == {"2": True}
    assert saved["scout_session_id"] == ""
    assert saved["pipeline_mode"] == AppState().pipeline_mode


def test_a_failed_save_is_reported_and_the_button_works_again(monkeypatch, tmp_path):
    state = AppState(project_name="", current_stage=2)
    page, root, fields, downloads = _url_direct_screen(monkeypatch, tmp_path, state)
    fields["Project name (created if new)"].value = "some_comic"

    def _disk_full(_state):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(s2_download, "save_state", _disk_full)
    _by_key(root, "download-from-url").on_click(None)
    _run_task(page)
    status = [c.value for c in _walk(root) if isinstance(c, ft.Text) and c.color]
    assert any("Failed" in str(v) for v in status), status

    monkeypatch.setattr(s2_download, "save_state", lambda _state: None)
    _by_key(root, "download-from-url").on_click(None)
    _run_task(page)
    assert downloads == ["some_comic"]


def test_a_name_that_is_already_a_folder_name_is_kept_whole(monkeypatch, tmp_path):
    """Stage 1's slugify also cuts at 60 characters; applied to an existing longer slug it
    would silently download into a new, truncated project."""
    long_name = "a_series_slug_taken_from_its_batcave_url_that_runs_past_sixty_chars"
    state = AppState(project_name=long_name, current_stage=2)
    page, root, fields, downloads = _url_direct_screen(monkeypatch, tmp_path, state)

    _by_key(root, "download-from-url").on_click(None)
    _run_task(page)

    assert downloads == [long_name]
