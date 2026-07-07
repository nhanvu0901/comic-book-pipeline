# Long-form (8+ min) mode — design spec (2026-07-07)

GOAL: 8+ minute videos for BOTH recap and Q&A, **without changing current core code**
(Stage 1-5 stay byte-identical). Seamless stitch (NO chapter cards). No render in this build.

## Principle: SEGMENT-AND-STITCH
Core pipeline makes ONE tight ~60-90s segment. Long-form = run it N times (each segment a
normal sub-project), then seamlessly concat. Total ≈ N × segment. Nothing in Stage 3's tuned
band / matcher / gates changes — each segment is just a normal short video.

New thin layer only: `stages/longform/` (orchestrator + decompose + stitch) + CLI + tests.

## Module layout
- `stages/longform/__init__.py`
- `stages/longform/decompose.py` — split a source into N ready sub-project slugs.
- `stages/longform/stitch.py` — seamless-concat N segment final.mp4 → one long mp4.
- `stages/longform/orchestrator.py` — top-level: decompose → per-segment (Stage 3→5) → stitch.
- `stages/longform/__main__.py` + CLI.
- `tests/test_longform_*.py`.

## Interfaces (FROZEN — build to these)
```
# decompose.py
def decompose_recap(saga_project: str, *, log) -> list[str]:
    '''An ALREADY-downloaded+preprocessed saga project (raw_comic ch01..chN + manifest +
    preprocessed/ + comic_context.issues[]). For each issue i: create sub-project
    "{saga}__seg{i:02d}", copy that issue's raw_comic pages + preprocessed page JSONs into it,
    write a SINGLE-ISSUE comic_context (title/series/issue from issues[i], plot_source stays
    recap, NOT is_arc), and a cluster_to_name.json copy. Returns ordered sub-project slugs.
    Pure file-ops + reuse of existing context shapes — NO Stage-2 re-run, NO core edit.'''

def decompose_qa(question: str, project: str, *, max_items: int, log) -> list[str]:
    '''Run answer_research for a LARGER item set (e.g. 12-15). Group items into K segments
    (e.g. 3-4 items each) so each segment is a normal ~60s Q&A countdown. For each group:
    create sub-project "{project}__seg{k:02d}" with an answer_context.json holding that group's
    items (+ a segment-scoped comic_context saga shape) and the group's downloaded chapters.
    Reuses answer_research.build_contexts + download_readers_only per group. Returns slugs.'''

# orchestrator.py
def run_longform(mode: str, *, source: str, target_minutes: float = 8.0,
                 project: str, atempo: float = 1.35, log) -> str:
    '''mode in {"recap","qa"}. Decompose → for each sub-project run write_script(mode) +
    save_narration + synthesize_project(post_atempo=atempo, skip_review=<recap:auto, qa:gated>)
    + assemble_project(force=True) → segment final.mp4. Collect segment mp4s in order, call
    stitch_segments → {project}/final.mp4. Returns final path. Stop-after flags for resumability.
    Q&A segments still honor the review gate per segment (Master locks panels per segment).'''

# stitch.py
def stitch_segments(segment_mp4s: list[Path], out_path: Path, *,
                    dissolve: float = 0.4, log) -> Path:
    '''Seamless concat of finished 9:16 H.264 segment videos (each already has burned captions +
    mixed audio, same specs). Default: ffmpeg concat demuxer (copy) for hard-cut seams; if
    dissolve>0, a segment-level xfade+acrossfade chain (mirror stage_5._xfade_chain but on full
    videos, video AND audio crossfaded). One continuous 8+ min mp4. No chapter cards.'''
```

## Reuse map (call these UNCHANGED)
- Stage 3: `stages.stage_3.pipeline.write_script(project, mode)` + `save_narration`.
- Stage 4: `stages.stage_4.pipeline.synthesize_project(project, post_atempo=1.35, force=True, skip_review=...)`.
- Stage 5: `stages.stage_5.pipeline.assemble_project(project, force=True)` → final.mp4.
- Saga download (Q&A/recap ingest): `stages.stage_2.url_mode.download_saga / download_readers_only`.
- Q&A research: `stages.stage_1.answer_research.research_answer / build_contexts`.
- Per-issue mapping: `stages._arc.issue_index_of_page`.
- Concat/xfade reference: `stages.stage_5.pipeline._concat / _xfade_chain / _final_encode`.

## Hard rules
- ZERO edits to stages/stage_1..5 core files, config band knobs, matcher. Only NEW files under
  stages/longform/ + a CLI + tests. (A tiny additive helper import is OK; changing behavior is not.)
- Recap segment = ONE issue (single-comic context → existing tuned ~70s recap). Q&A segment =
  one item-group countdown (existing tuned ~60s).
- Voice consistency: pick voice ONCE (recap: select_voice on the saga; qa: once) and pass the
  SAME voice_id to every segment's synthesize_project so timbre matches across segments.
- No render in this build — build + unit tests (stub the heavy Stage calls; test decompose file-ops,
  stitch command construction, orchestrator sequencing) only. Master runs the real 8-min later.
- Fail-loud per segment; a failed segment logs + is skipped, orchestrator continues (never abort the
  whole long-form for one bad issue) but reports which segments shipped.
```
