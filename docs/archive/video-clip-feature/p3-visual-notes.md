# P3 visuals (`video-qa/p3-visual`) — what shipped, the contracts, the evidence

Mode `screen_qa`: a Q&A Short whose pictures are video clips / stills / text cards — **no comic page
anywhere**. Everything below is in NEW modules; the stable comic path is touched in exactly three
places, all inert for every other mode (see "Stable-path footprint").

## Modules

| Module | Role |
|---|---|
| `stages/stage_5/screen_beats.py` | PURE planning (no ffmpeg/flet): canonical beat rows + frame-exact timeline windows |
| `stages/stage_5/screen_shots.py` | `build_shots_for_screen_qa`, `render_screen_shot` (never-crash chain), `run_screen_qa_pipeline` (Stage 5 for the mode) |
| `stages/stage_5/screen_selection.py` | Master's picks per beat: clips / stills / approval / backup / narration pin (all file IO of the screen) |
| `utils/screen_card.py` | NEW wrapped-text card (repo font `fonts/Anton-Regular.ttf`). `utils/text_card.render_text_card` is untouched (hash-guarded by a test) |
| `ui/screens/s_screen_gate.py` | the select-beat screen; thin view over `screen_selection` |

## Beat keys = the review scheme (0-based)

`"intro" | "outro" | "<scene_id>" | "<scene_id>:<frag_idx>"` — the SAME keys `review_gate.build_candidates`
writes to `locks.json` (`review_gate.bookend_row_keys` is reused, not copied). So an MP4 picked in P1's
`/moments_review` (`clips.json` `beat`) and a still locked in the screen resolve through the unchanged
`clips.resolve_clip_assignments` / `shots._resolve_custom_images`. (The first draft keyed `sid:idx+1`
and would have put every clip one fragment off.) `beat_timing.calculate_beat_durations` ids are
1-based — do not use them as manifest keys.

## Timeline = the audio, exactly

Each scene owns `prev_scene_end → its own end` (scene 1 from 0), so inter-sentence and lead silence is
absorbed (the comic builder's rule). The last window runs to `audio_duration + 0.20s` when the scenes
end before the audio (BUG #122 Fix C). Cuts inside a scene land in the silence between the real words
(`word_timestamps`) or proportionally to word counts; every boundary is snapped to a whole frame by
cumulative rounding → `sum(frames)/30 >= audio`, drift <= half a frame. Unpicked windows under
`QA_MIN_SHOT_SECONDS` merge into their longer same-scene neighbour; a beat with a pick never does.

## Never-crash chain (`render_screen_shot`)

| Level | What | Notes |
|---|---|---|
| 1 | the beat's clip — `clips.render_clip_shot` (P1 fit/contract) | a section missing on disk but with `source_url` is re-fetched via `clip_fetch.fetch_clip_section` from `source_start + in-point` |
| 2 | BACKUP clip — `clips.json` entry `"backup": {id,file\|source_url,start,end,source_start}` | filled best-effort after a pick by `screen_selection.suggest_backup` (cached shortlist, no new search) |
| 3 | still / custom image — `shots.render_shot` (unchanged), Ken Burns, real motions only | |
| 4 | text card (`utils/screen_card`) → plain-colour frame via ffmpeg `color` | a beat nobody picked for lands here directly, with no error |

Every level honours the shot contract (h264 yuv420p 1080x1920 30fps, N frames, no audio, SAR unsignalled)
so `_concat`'s stream copy works across all four; a clip shot that falls back keeps `clip_fallback`
non-empty so `_assemble_video` stops treating it as a clip (hard cuts only around real clip shots).
`ClipTooShort` (P1's pure `fit()`) is just "level 1 failed".

## Interfaces

* **p3-core** — reads `narration.json` (Scene schema, `mode:"screen_qa"`, `visual_beats` `{text,query}`) and
  `screen_context.json`; p3-core's render step calls `stage_5.pipeline.assemble_project` (review gate + hash
  guard + audio loading in one place) which dispatches here. `stages/stage_5/screen_beats.py`'s
  `screen_beat_rows` / `plan_windows` signatures are pinned by p3-core's forward contract test.
* **P1** — uses `parse_manifest`, `resolve_clip_assignments`, `apply_clips_to_shots`, `render_clip_shot`,
  `verify_shot_contract`, `shot_log_entry`, `clip_fetch.fetch_clip_section`, `stage_5.pipeline._assemble_video`
  (hard cuts) and the `/moments_review`, `/api/pick_moment` routes + `add/remove_moment_picked_listener`.
  TTS status only through `stages.stage_4.tts_status` (falls back to the same `review/tts_status.json`).
* **review gate** — Approve writes `locks.json` approved + `narration_sha1`, so `ensure_reviewed` (Stage 4 and 5)
  passes; any later change of a pick withdraws the approval. A narration that no longer matches the one the picks
  were made against clears EVERY pick (Master decision a).

## Stable-path footprint (all inert unless `mode == "screen_qa"` / a `screen_context.json` exists)

1. `stages/stage_5/pipeline.assemble_project` — a 4-line dispatch before any comic page loading.
2. `ui/screens/s_review_gate.build` — hands a `screen_qa` project to the screen (was already there).
3. `ui/state.list_projects` — also lists a dir holding `screen_context.json` (without it a screen project never
   appears in the picker).

NOT added: `PipelineMode.SCREEN_QA` (crashes `s1_identify` — B4), any `MODES` entry (changes the Stage-3 prompt).

## Evidence (this folder)

* `e2e_windows_report.txt` — fixture → a REAL YouTube section downloaded through P1's `fetch_clip_section`
  (needs `YTDLP_BIN` = a current yt-dlp, see the last gotcha) → real Chatterbox TTS on the Windows CPU →
  `assemble_project` → `final.mp4`: final == audio length (10.288s), every scene cut within a frame of the scene
  end, one shot per level (clip, still, backup clip). `e2e_windows_report_stale_ytdlp.txt` is the same run with the
  production venv's stale yt-dlp: the section download fails and beat 1:0 falls all the way to a card — the chain
  doing its job.
* `final_contact.png` — frames of that `final.mp4` (real 16:9 trailer section contained over its blur, still with
  Ken Burns, backup clip, short clip held).
* `10..15_*.png` + `harness/p3v_pw_test.py` — Playwright (Chrome, headless) against the REAL `python -m ui --lan`
  (ENABLE_VIDEO_CLIPS=1, FastAPI wrap) on port 8561 via an SSH tunnel: render, Approve, "Chọn MP4" opens the P1 tab
  for exactly that beat, a pick repaints the Flet page by itself, "Thẻ chữ", still through the file chooser,
  Continue → TTS. 18/18 checks.
* `harness/` also has the Windows launchers. Detached jobs MUST be started through WMI (`_p3v_wmi.ps1`): a
  `Start-Process`/`start /b` child dies when the SSH session ends.

## Gotchas found on the way (not fixed here — stable path)

* `ft.ElevatedButton.text = ...` repaints nothing on flet 0.84 AND 0.86 (the label is `.content`): the comic
  gate's Approve/Un-approve label (`s_review_gate` `approve_btn.text = ...`) never changes; the screen uses `.content`.
* `ui/_flet_compat` makes `ft.padding/margin.symmetric` keyword-only on flet >= 0.85: positional calls pass on a 0.84
  Mac and crash the 0.86 server (a test now guards every `ui/` file).
* Windows box: the production venv's yt-dlp (2026.07.04) gets HTTP 403 from googlevideo for every download — also
  full ones — so `fetch_clip_section` dies with `ffmpeg exited with code 3436169992`. yt-dlp 2026.08.19 in a scratch
  venv works (`YTDLP_BIN` is honoured by `clip_fetch._ytdlp_cmd`). Updating the production venv is Master's call.
