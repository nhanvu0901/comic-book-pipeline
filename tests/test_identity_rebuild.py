"""Stage-1 self-ID hardening (2026-07-04): Stage 1 resolved "Moon Knight (2021) #9
Stranger" but fetched the PLOT of "Strange (2022) #9" — same writer/issue#/near-title,
so identity fields were right and only plot_summary was another comic's. The fix (see
stages/stage_2/identity_check.py) catches this via proper-noun disagreement and
rebuilds plot_summary from the panels themselves. Pure logic + mocked SDK/summarize —
no network, no render, no pytest fixtures beyond monkeypatch."""
import json

import stages.stage_2.identity_check as ic
import stages.stage_2.pipeline as pipe
from stages.stage_2.identity_check import (
    plot_agrees_with_pages,
    proper_noun_overlap,
    prompt_disagrees_with_plot,
    rebuild_plot_from_panels,
)

# ── Realistic fixtures: the actual wrong-comic mix-up ───────────────────────────
_STRANGE_PLOT = (
    "Doctor Strange battles Clea after a Harvestman ambush inside the Sanctum "
    "Sanctorum, while Wong warns him of a coming Empirikul threat."
)
_MK_PAGES_CORPUS = (
    "Marc Spector wakes inside the House of Shadows, haunted by Khonshu. "
    "Steven Grant argues with Marc about who controls their body. "
    "Layla El-Faouly arrives to help Moon Knight escape the asylum."
)
_MK_PLOT_CORRECT = (
    "Marc Spector, also known as Moon Knight, is trapped inside the House of "
    "Shadows by Khonshu. Steven Grant and Layla El-Faouly help him break free "
    "and confront Khonshu's judgment."
)
_MK_USER_PROMPT = "Moon Knight (2021) #9, the one where Marc Spector confronts Khonshu"


def _story_pages(summaries: list[str]) -> list[dict]:
    return [
        {"page_number": i + 1, "is_story_page": True, "page_summary": s}
        for i, s in enumerate(summaries)
    ]


# ── 1. overlap separates wrong-comic vs same-comic ──────────────────────────────

def test_overlap_separates_wrong_comic_from_same_comic():
    wrong = proper_noun_overlap(_STRANGE_PLOT, _MK_PAGES_CORPUS)
    right = proper_noun_overlap(_MK_PLOT_CORRECT, _MK_PAGES_CORPUS)
    assert wrong < 0.15
    assert right > 0.30


# ── 2. thin/empty page corpus → can't judge, never flags ────────────────────────

def test_plot_agrees_returns_none_on_thin_corpus():
    ctx = {"plot_summary": _STRANGE_PLOT}
    assert plot_agrees_with_pages(ctx, []) is None
    assert plot_agrees_with_pages(ctx, _story_pages(["Marc walks in."])) is None


def test_plot_agrees_true_and_false_on_rich_corpus():
    pages = _story_pages([_MK_PAGES_CORPUS])
    assert plot_agrees_with_pages({"plot_summary": _MK_PLOT_CORRECT}, pages) is True
    assert plot_agrees_with_pages({"plot_summary": _STRANGE_PLOT}, pages) is False


# ── 3. Hook 0: prompt vs plot disagreement ───────────────────────────────────────

def test_prompt_disagrees_with_wrong_plot():
    ctx = {"user_prompt": _MK_USER_PROMPT, "plot_summary": _STRANGE_PLOT}
    assert prompt_disagrees_with_plot(ctx) is True


def test_prompt_agrees_with_matching_plot():
    ctx = {"user_prompt": _MK_USER_PROMPT, "plot_summary": _MK_PLOT_CORRECT}
    assert prompt_disagrees_with_plot(ctx) is False


def test_prompt_disagrees_noop_without_user_prompt():
    # url-mode projects carry no user_prompt — nothing to cross-check, never flags.
    ctx = {"plot_summary": _STRANGE_PLOT}
    assert prompt_disagrees_with_plot(ctx) is False


def test_hook0_sets_identity_suspect_on_mismatch(tmp_path, monkeypatch):
    project_root = tmp_path
    ctx_path = project_root / "comic_context.json"
    ctx_path.write_text(json.dumps({"user_prompt": _MK_USER_PROMPT, "plot_summary": _STRANGE_PLOT}))
    logs = []
    pipe._run_identity_precheck(project_root, logs.append)
    saved = json.loads(ctx_path.read_text())
    assert saved.get("identity_suspect") is True
    assert any("suspect wrong comic" in m for m in logs)


def test_hook0_no_flag_when_prompt_matches_plot(tmp_path):
    project_root = tmp_path
    ctx_path = project_root / "comic_context.json"
    ctx_path.write_text(json.dumps({"user_prompt": _MK_USER_PROMPT, "plot_summary": _MK_PLOT_CORRECT}))
    pipe._run_identity_precheck(project_root, lambda _m: None)
    saved = json.loads(ctx_path.read_text())
    assert "identity_suspect" not in saved


# ── 4. rebuild shape: SDK + summarize mocked ────────────────────────────────────

def test_rebuild_shape(monkeypatch):
    monkeypatch.setattr(ic, "PLOT_REBUILD_FROM_PANELS", True)

    fake_sdk_calls = []

    def fake_sdk_complete(system, user, **kwargs):
        fake_sdk_calls.append((system, user))
        return _MK_PLOT_CORRECT

    def fake_summarize_context(ctx, *, progress=None):
        return {"story_arc": ctx["plot_summary"], "characters": [
            {"name": "Marc Spector", "aliases": ["Moon Knight"], "role": "protagonist", "visual": ""},
        ], "setting": "New York", "key_objects": []}

    monkeypatch.setattr("stages._claude_sdk.sdk_complete", fake_sdk_complete)
    monkeypatch.setattr(
        "stages.stage_1.tools.summarize_context.summarize_context", fake_summarize_context)

    ctx = {"plot_summary": _STRANGE_PLOT, "summary": {"stale": True}}
    pages = _story_pages([_MK_PAGES_CORPUS])
    ok = rebuild_plot_from_panels(ctx, pages, log=lambda _m: None)

    assert ok is True
    assert len(fake_sdk_calls) == 1
    assert ctx["plot_summary"] == _MK_PLOT_CORRECT
    assert ctx["plot_summary_wiki"] == _STRANGE_PLOT
    assert ctx["plot_source"] == "panels"
    assert ctx["plot_status"] == "OK"
    assert isinstance(ctx["summary"]["characters"], list)
    assert all(isinstance(c, dict) for c in ctx["summary"]["characters"])


def test_rebuild_returns_false_on_sdk_failure(monkeypatch):
    monkeypatch.setattr(ic, "PLOT_REBUILD_FROM_PANELS", True)
    monkeypatch.setattr("stages._claude_sdk.sdk_complete", lambda *a, **k: None)
    ctx = {"plot_summary": _STRANGE_PLOT}
    ok = rebuild_plot_from_panels(ctx, _story_pages([_MK_PAGES_CORPUS]), log=lambda _m: None)
    assert ok is False
    assert ctx["plot_summary"] == _STRANGE_PLOT  # untouched on failure


def test_rebuild_respects_kill_switch(monkeypatch):
    monkeypatch.setattr(ic, "PLOT_REBUILD_FROM_PANELS", False)
    calls = []
    monkeypatch.setattr("stages._claude_sdk.sdk_complete", lambda *a, **k: calls.append(1))
    ctx = {"plot_summary": _STRANGE_PLOT}
    ok = rebuild_plot_from_panels(ctx, _story_pages([_MK_PAGES_CORPUS]), log=lambda _m: None)
    assert ok is False
    assert not calls


def test_rebuild_no_op_on_empty_page_corpus(monkeypatch):
    monkeypatch.setattr(ic, "PLOT_REBUILD_FROM_PANELS", True)
    calls = []
    monkeypatch.setattr("stages._claude_sdk.sdk_complete", lambda *a, **k: calls.append(1))
    ctx = {"plot_summary": _STRANGE_PLOT}
    ok = rebuild_plot_from_panels(ctx, _story_pages(["", "  "]), log=lambda _m: None)
    assert ok is False
    assert not calls  # never calls the SDK with an empty corpus


# ── 5. idempotency: second call with plot_source=="panels" skips the SDK ────────

def test_hook2_idempotent_unless_forced(tmp_path, monkeypatch):
    project_root = tmp_path
    ctx_path = project_root / "comic_context.json"
    # identity_suspect is ALSO set here so that once --force bypasses the
    # plot_source=="panels" skip, a real trigger condition still fires — this
    # isolates the idempotency guard itself from the separate trigger-condition
    # logic tested elsewhere in this file.
    ctx_path.write_text(json.dumps({
        "plot_summary": _MK_PLOT_CORRECT, "plot_source": "panels", "plot_status": "OK",
        "identity_suspect": True,
    }))
    calls = []
    monkeypatch.setattr(
        "stages.stage_2.identity_check.rebuild_plot_from_panels",
        lambda *a, **k: calls.append(1) or True,
    )
    pipe._run_identity_repair(project_root, [], lambda _m: None, force_refresh=False)
    assert not calls  # already rebuilt once (plot_source=="panels") — skipped

    pipe._run_identity_repair(project_root, [], lambda _m: None, force_refresh=True)
    assert len(calls) == 1  # --force bypasses the idempotency skip


def test_hook2_triggers_on_missing_plot_status(tmp_path, monkeypatch):
    project_root = tmp_path
    ctx_path = project_root / "comic_context.json"
    ctx_path.write_text(json.dumps({"plot_summary": "", "plot_status": "MISSING"}))
    calls = []
    monkeypatch.setattr(
        "stages.stage_2.identity_check.rebuild_plot_from_panels",
        lambda *a, **k: calls.append(1) or True,
    )
    pipe._run_identity_repair(project_root, [], lambda _m: None)
    assert len(calls) == 1


def test_hook2_no_trigger_on_healthy_plot(tmp_path, monkeypatch):
    project_root = tmp_path
    ctx_path = project_root / "comic_context.json"
    # Healthy: has a wiki_url, long plot, agrees with the (rich) page corpus, not suspect.
    ctx_path.write_text(json.dumps({
        "plot_summary": _MK_PLOT_CORRECT + " " + _MK_PLOT_CORRECT,  # >300 chars easily
        "wiki_url": "https://marvel.fandom.com/wiki/Moon_Knight_Vol_9_9",
        "plot_status": "OK",
    }))
    calls = []
    monkeypatch.setattr(
        "stages.stage_2.identity_check.rebuild_plot_from_panels",
        lambda *a, **k: calls.append(1) or True,
    )
    pages = _story_pages([_MK_PAGES_CORPUS] * 3)
    pipe._run_identity_repair(project_root, pages, lambda _m: None)
    assert not calls


# ── 6. module __main__ self-check ────────────────────────────────────────────────

def test_module_self_check_runs_clean():
    import subprocess
    import sys
    result = subprocess.run(
        [sys.executable, "-m", "stages.stage_2.identity_check"],
        capture_output=True, text=True, cwd=__file__.rsplit("/tests/", 1)[0],
    )
    assert result.returncode == 0, result.stderr
    assert "OK:" in result.stdout
