# P3 core handoff — `video-qa/p3-core` (screen_qa research / narration / CLI)

Status: ready for the integrator. Diff vs `feat/video-qa-hybrid` is **new files only**
(`config.py`, `stages/stage_3/modes.py`, `write_script.py`, `shots.py`, `utils/text_card.py`, `ui/` untouched).

## What is here

| File | Role |
|---|---|
| `stages/screen_pipeline.py` | CLI: `research -> narrate -> tts -> render`, one `[screen-pipeline] step=… status=…` line per step |
| `stages/stage_1/screen_research.py` | question -> `screen_context.json`, grounded in You.com web evidence |
| `stages/stage_3/screen_qa.py` | `screen_context.json` -> `narration.json` (mode `screen_qa`) |
| `tests/test_screen_{research,narration,pipeline_glue,qa_registration,qa_integration}.py` | unit + live |

Names differ from the plan table: the narration module is `screen_qa.py` (plan: `screen_script.py`).

## Contract (shared with `video-qa/p3-visual`)

* `screen_context.json` = `{question, [answer_summary], items:[{entity, event, adaptation_title, year:int, summary, visual_query, source_urls[]}]}`.
  `answer_summary` is an **optional** extra (the researched one-line answer; explain-type questions need it as the
  writer's destination). Readers that ignore it are unaffected.
* `narration.json` = the existing `Narration`/`Scene` schema, `mode: "screen_qa"`, `page_ref 0 / panel_ref -1`:
  scene 1 = the spoken hook (`is_intro`), then **two scenes per item** (CONTEXT, then MOMENT), then the spoken outro (`is_outro`).
  Every new film/series is cited by **title + year** on its CONTEXT scene; never `#`/issue numbers.
* `Scene.visual_beats[i] = {"text", "query"}`: `text` is a verbatim fragment (fragments rebuild the scene text), `query`
  is a clip-search phrase that always names the film. Beat keys are positional (`intro`/`outro` for a single fragment,
  `<scene_id>:<0-based n>` otherwise — `review_gate.bookend_row_keys`, as p3-visual's `screen_beats` derives them).
* Render goes through `stages.stage_5.pipeline.assemble_project`, which dispatches `mode == "screen_qa"` to
  p3-visual's `run_screen_qa_pipeline`. The CLI refuses a narration whose mode is not `screen_qa`.

## Registration: deliberately NOT in `PipelineMode` / `stage_3.modes.MODES`

* A `MODES` entry changes `describe_catalog()` = the Stage-3 propose prompt of **every** comic (and lets the LLM propose it).
* A `PipelineMode.SCREEN_QA` member makes `ui/screens/s1_identify.py` raise `KeyError` (`MODE_LABELS[mode]`, it iterates the enum).
* The key is the plain constant `stages.stage_3.screen_qa.SCREEN_QA_MODE`. `tests/test_screen_qa_registration.py` guards both.

## Running

```
python -m stages.screen_pipeline --question "How did the Avengers travel back in time in Endgame?" --project endgame_time_travel --stop-after narrate
# review/approve narration + clips in the review gate, then (narration is approved by SHA — do not re-run narrate):
python -m stages.screen_pipeline --project endgame_time_travel --start-at tts
python -m stages.screen_pipeline --project endgame_time_travel --start-at render     # re-render only
pytest -m integration tests/test_screen_qa_integration.py -s                          # live; skipped without YDC_API_KEY+OPENROUTER_API_KEY
```

## Evidence (2026-10-08)

* Mac: full suite on the merged head — 2267 passed, 16 skipped, 0 failed (no `.env` in the worktree).
* Windows (`D:\code\cbp-video-test-p3c`, prod venv read-only, flag OFF, `POST_ATEMPO` from `.env` = 1.15):
  * unit tests for the 4 screen modules: 79 passed;
  * LIVE `pytest -m integration tests/test_screen_qa_integration.py` (Endgame question): **passed in 248 s**
    — 5 grounded items, 12 scenes (hook + 5x2 + outro), title + year cited, `{text, query}` beats;
  * TTS step (`--start-at tts`, local Chatterbox on CPU, review gate approved the way a UI approve would):
    OK, 12 chunks, 53.9 s of audio after atempo 1.15 (~4.5 min wall);
  * render step (`--start-at render` on p3-core merged with p3-visual, no clips picked so all 19 shots are text cards):
    OK, `final.mp4` 1080x1920 30 fps, 53.9 s video / 53.93 s audio.
* The forward-contract test `test_contract_with_the_p3_visual_beat_planner` is skipped on this branch alone and passes on p3-core + p3-visual merged.

## Needs the integrator / p3-visual (cross-branch)

1. **Drop `SCREEN_QA = "screen_qa"` from p3-visual's `config.py`** (it re-adds the `s1_identify` KeyError on merge; nothing uses it).
   Verified: merging p3-visual into p3-core turns `test_screen_qa_registration.py` red (2 tests).
2. **Approval path for screen_qa.** `s_screen_gate.py` never writes `review/locks.json` `{approved, narration_sha1}`, and `review_gate`
   has no screen_qa exemption, so Stage 4/5 `ensure_reviewed` blocks every screen_qa project. The gate's continue button should approve via
   `review_gate.save_state` (the tests here approve the same way).
3. Chatterbox voice: the test checkout had no `assets/voices/arthur_ref.wav` (built-in voice) — irrelevant to code, but audio is not the channel voice there.

## Not done / known limits

* No loop-tease after the outro (comic Q&A appends one when `ENABLE_LOOP_TEASE`; it needs a comic plot, so it was not ported).
* A writer draft with the wrong scene count is rejected by the chain validator; if every model fails the CLI ships the deterministic
  script and says so in the narrate status line (`deterministic fallback`).
* The first model in the creative chain (`deepseek/deepseek-v4-flash`) stalled 90 s on most live calls; each stall is skipped by the chain.
