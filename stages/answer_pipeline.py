"""
Agent-first orchestrator CLI for explore_answer (Q&A) mode.

Runs the full pipeline for a QUESTION-driven Short end to end: research the
answer across N comics, download the cited issues, preprocess, write the
countdown narration, synthesize TTS, render the final video. See
EXPLORE_ANSWER_DESIGN.md (root) for the format spec.

NO interactivity anywhere (no input(), no mode pickers) — every decision is a
flag or a sane default, so an agent can drive this unattended. Each step
prints ONE machine-parseable status line:

    [answer-pipeline] step=<name> status=<ok|fail> detail=...

Usage:
    python -m stages.answer_pipeline --question "Who has survived Ghost Rider's Penance Stare?" --project ghost_rider_penance_survivors
    python -m stages.answer_pipeline --project ghost_rider_penance_survivors --skip-research --stop-after preprocess
    python -m stages.answer_pipeline --project ghost_rider_penance_survivors --skip-research --skip-download

Steps are resumable by relying on each stage's own caching (Stage 2 hashes
pages, Stage 4/5 skip existing output unless --force) — re-running the same
command after a partial failure picks up where it left off, EXCEPT narrate
which always regenerates narration.json fresh (it has no cache of its own;
same as plain Stage 3). tts/render below therefore force-regenerate too:
Stage 4 caches audio.wav keyed on nothing but its own existence, so a
narration rewrite with no --force would silently ship the OLD audio under
the NEW captions.
"""
from __future__ import annotations

from config import POST_ATEMPO

import argparse
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Callable

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

STEPS = ["research", "download", "preprocess", "narrate", "tts", "render"]


# ─── Step bodies ─────────────────────────────────────────────────────────────
# Each returns a short human-readable detail string on success, or raises.
# Kept as free functions (not methods) so tests can monkeypatch them directly
# by name (stages.answer_pipeline._step_research, etc.) without touching main().


def _attach_money_target(answer_path: Path, log: Callable[[str], None]) -> None:
    """MONEY SHOT funnel Phan 1: derive the one money-shot target from the just-written
    answer_context.json and stash it there under `money_target`. Best-effort — a
    failed derivation (no API key / both free models down / unparseable) leaves the
    field simply absent (derive_money_target already returns None + logs why), so the
    funnel downstream just skips money-shot scoring for this project."""
    from stages.money_shot import derive_money_target

    answer_ctx = json.loads(answer_path.read_text())
    target = derive_money_target(answer_ctx, log=log)
    if target is not None:
        answer_ctx["money_target"] = target
        answer_path.write_text(json.dumps(answer_ctx, indent=2, ensure_ascii=False))
        log(f"[answer-pipeline] money_target: {target.get('money_event', '')!r}")


def _step_research(args: argparse.Namespace, log: Callable[[str], None]) -> str:
    """Web-research the answer and write comic_context.json + answer_context.json.

    Imported lazily (not at module top) so --skip-research works even when the
    Claude SDK / web-research deps aren't installed in the running environment.
    Matches stages/stage_1/answer_research.py's actual signatures exactly:
    research_answer(question, *, max_items, log) -> dict; build_contexts(question,
    research, project_name, *, log) -> (answer_path, comic_path). Both raise on
    unusable research (SDK down, too few verified items, empty reader_url) —
    left to propagate so the caller reports status=fail with the real reason.
    """
    if args.rebuild_contexts:
        # Resume path: research already persisted to answer_context.json (possibly
        # hand-edited to fill missing reader_urls) — rebuild comic_context from it
        # instead of paying for a fresh SDK research call. The file's shape is a
        # superset of research_answer()'s return, so it feeds build_contexts as-is.
        from config import POST_ATEMPO, get_project_dirs
        from stages.stage_1.answer_research import build_contexts

        answer_path = get_project_dirs(args.project)["root"] / "answer_context.json"
        research = json.loads(answer_path.read_text())
        answer_path, _comic_path = build_contexts(
            research.get("question", args.question), research, args.project,
            researched_at=research.get("researched_at", ""), log=log)
        _attach_money_target(answer_path, log)
        return f"rebuilt from {answer_path.name} ({len(research.get('items') or [])} item(s))"

    if not args.question:
        raise ValueError("--question is required unless --skip-research")
    from stages.stage_1.answer_research import build_contexts, research_answer

    research = research_answer(args.question, max_items=args.max_items,
                               hint=getattr(args, "hint", ""), log=log)
    answer_path, _comic_path = build_contexts(
        args.question, research, args.project, log=log)
    _attach_money_target(answer_path, log)
    return f"{len(research.get('items') or [])} item(s) -> {answer_path.name}"


def _load_reader_urls(project_name: str) -> list[str]:
    """Read the ordered reader_urls list research wrote into comic_context.json
    (the SAME field name url_mode.py already uses for reader-URL projects —
    see download_from_readers' `extra = {"reader_urls": urls}`). Reads from
    disk rather than a prior step's return value so the download step is
    resumable across separate invocations (e.g. after --stop-after research)."""
    from config import get_project_dirs

    ctx_path = get_project_dirs(project_name)["root"] / "comic_context.json"
    if not ctx_path.exists():
        raise FileNotFoundError(
            f"comic_context.json missing for '{project_name}' — run the research step first")
    ctx = json.loads(ctx_path.read_text())
    urls = [u for u in (ctx.get("reader_urls") or []) if u]
    if not urls:
        raise ValueError(f"comic_context.json has no reader_urls for '{project_name}'")
    return urls


def _step_download(args: argparse.Namespace, log: Callable[[str], None]) -> str:
    from stages.stage_2.url_mode import download_readers_only

    urls = _load_reader_urls(args.project)
    # Pass the FULL item-ordered list (duplicates included): download_readers_only
    # dedups itself while keeping each URL's first-occurrence rank as its chapter
    # label — a positional dedup here shifted later chapters' "#N" labels and broke
    # the beat→item→panel-pool mapping (audit 2026-07-06).
    result = download_readers_only(args.project, urls, progress=log)
    return f"{result.get('total_pages', 0)} page(s) across {result.get('chapters', 0)} chapter(s)"


def _step_preprocess(args: argparse.Namespace, log: Callable[[str], None]) -> str:
    from stages.stage_2.pipeline import preprocess_project

    pages = preprocess_project(args.project, progress=log)
    return f"{len(pages)} page(s) preprocessed"


def _step_narrate(args: argparse.Namespace, log: Callable[[str], None]) -> str:
    from stages.stage_3.pipeline import save_narration, write_script

    nar = write_script(args.project, "explore_answer", progress=log)
    save_narration(nar, args.project, progress=log)
    return f"{len(nar.scenes)} scene(s), ~{nar.estimated_duration_seconds}s"


def _step_tts(args: argparse.Namespace, log: Callable[[str], None]) -> str:
    from stages.stage_4.pipeline import synthesize_project

    result = synthesize_project(args.project, post_atempo=args.atempo, force=True,
                                skip_review=args.skip_review)
    return f"audio {result.audio_duration_seconds:.1f}s"


def _step_render(args: argparse.Namespace, log: Callable[[str], None]) -> str:
    """Render via a SUBPROCESS, not an in-process call. config.py's env-derived
    constants (ENABLE_OUTRO_CARD included) are computed once at `config`'s
    first import and cached — by this point in the pipeline `config` has
    already been imported (Stage 2/3/4 above all import it), so mutating
    os.environ in-process here would NOT change the value stage_5.pipeline
    reads. A fresh subprocess re-imports config from scratch, so the env
    override actually takes effect. The Q&A ending loops/teases the question
    (ADDENDUM v2 in EXPLORE_ANSWER_DESIGN.md) — a branding outro card breaks
    that seamless loop, so it's off for this render only.
    """
    # Panel selection for a Q&A project now happens INSIDE Stage 5: build_shots renders at
    # caption-chunk granularity restricted to each beat's review-locked panels
    # (shots._build_shots_per_chunk_locked, gated on plot_source == "answer_research" + locks).
    # No pre-step is needed — the old headless sentence-match refinement is retired.
    env = dict(os.environ)
    env["ENABLE_OUTRO_CARD"] = "false"
    cmd = [sys.executable, "-m", "stages.stage_5", "--project", args.project, "--force"]
    if args.skip_review:
        cmd.append("--skip-review")
    proc = subprocess.run(cmd, cwd=str(_REPO_ROOT), env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"stage_5 subprocess exited {proc.returncode}")
    return "final.mp4 rendered"


def _run_step(step: str, args: argparse.Namespace, log: Callable[[str], None]) -> str:
    """Dispatch by name via globals(), not a dict built at import time — a dict of
    direct function references would freeze in the ORIGINAL _step_* objects, so a
    test's `monkeypatch.setattr(module, "_step_research", fake)` would silently
    have no effect (the dict still points at the pre-patch function). globals()
    is this module's live __dict__, so a monkeypatched name is picked up here."""
    return globals()[f"_step_{step}"](args, log)


# ─── CLI ─────────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m stages.answer_pipeline",
        description="explore_answer (Q&A) mode: question -> researched countdown Short, end to end.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Examples:
          python -m stages.answer_pipeline --question "Who has survived Ghost Rider's Penance Stare?" --project ghost_rider_penance_survivors
          python -m stages.answer_pipeline --project ghost_rider_penance_survivors --skip-research --stop-after preprocess
          python -m stages.answer_pipeline --project ghost_rider_penance_survivors --skip-research --skip-download
        """),
    )
    parser.add_argument("--question", default="", help="The question to answer (required unless --skip-research).")
    parser.add_argument("--project", required=True, help="Project slug under projects/.")
    parser.add_argument("--max-items", type=int, default=5, help="Max countdown items to research. Default 5.")
    parser.add_argument("--hint", default="", help=(
        "Optional grounding hint for the research agent (e.g. the likely story/issue a "
        "scout already identified). Verified against sources, not trusted blindly — "
        "abstract 'Why/How' questions wander without one."))
    parser.add_argument("--atempo", type=float, default=POST_ATEMPO,
                        help="ffmpeg atempo for Stage 4 TTS (pitch-preserving speed-up). Default from config.POST_ATEMPO.")
    parser.add_argument("--rebuild-contexts", action="store_true",
                        help="Rebuild comic_context.json from the project's existing "
                             "answer_context.json (e.g. after hand-filling reader_urls) "
                             "instead of a fresh SDK research call.")
    parser.add_argument("--skip-research", action="store_true",
                        help="Skip research; reuse an existing comic_context.json/answer_context.json.")
    parser.add_argument("--skip-download", action="store_true",
                        help="Skip download; reuse an already-downloaded raw_comic/.")
    parser.add_argument("--skip-review", action="store_true",
                        help="Pass-through to Stage 4/5. NOTE: ignored for answer_research (Q&A) "
                             "projects — Q&A panel choices must be reviewed (see review gate).")
    parser.add_argument("--stop-after", choices=STEPS, default=None,
                        help="Run through this step then stop (default: run all the way to render).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    stop_index = STEPS.index(args.stop_after) if args.stop_after else len(STEPS) - 1

    skip = {"research": args.skip_research, "download": args.skip_download}

    for i, step in enumerate(STEPS):
        if skip.get(step):
            print(f"[answer-pipeline] step={step} status=ok detail=skipped")
        else:
            try:
                detail = _run_step(step, args, print)
            except SystemExit as exc:
                # The review gate (ensure_reviewed) raises SystemExit when the Q&A
                # project isn't approved yet — without this clause the process died
                # WITHOUT the machine-parseable status line the module promises
                # ("one status line per step"), so an agent driving this CLI saw
                # nothing to parse. Q&A runs are EXPECTED to stop here: approve in
                # the review UI between narrate and tts, then re-run.
                print(f"[answer-pipeline] step={step} status=fail detail=ReviewGateBlocked: {exc}")
                return 1
            except Exception as exc:  # noqa: BLE001 - report every failure mode uniformly, agent parses status=fail
                print(f"[answer-pipeline] step={step} status=fail detail={type(exc).__name__}: {exc}")
                return 1
            print(f"[answer-pipeline] step={step} status=ok detail={detail}")
        if i >= stop_index:
            break

    return 0


if __name__ == "__main__":
    sys.exit(main())
