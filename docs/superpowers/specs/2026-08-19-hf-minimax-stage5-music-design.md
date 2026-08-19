# HF MiniMax Music 3 scoring for Stage 5

## Purpose

Replace the local ACE-Step/DirectML score generator with the project's private
Hugging Face Space (`Neopet2001/MiniMax-Music3`). The scorer must use the final
narration and final video duration, preserve the Review Beats genre preference,
and never prevent a video from being rendered.

## User-facing contract

- Review Beats continues to show an editable **Music genre** field. It is a
  preference, not a pre-written music prompt.
- The LLM writes every generated music instruction after analysing the final
  narration, its dramatic beats, the selected genre, and the measured duration.
- The final output is either a scored `final.mp4`, or a narration-only
  `final.mp4`. There is no stock/library/previous-score fallback.
- A failed music request must be visible in Stage 5 logs, but it is not a Stage
  5 failure.

## Pipeline order

```text
Stage 4 finalises narration.wav + narration.json
        |
Stage 5 renders panels/motion and creates narration-only final.mp4
        |
ffprobe measures final.mp4 duration (source of truth)
        |
LLM builds a MiniMax Studio state from narration + beats + genre + duration
        |
HF private Space /studio_generate creates project-local bgm.mp3
        |
mix bgm.mp3 with narration; remux audio while stream-copying the final video
        |
atomically replace final.mp4
```

The first `final.mp4` is a safe narration-only intermediate. If the scoring
branch fails, it remains the delivered final output. A successful scoring branch
does not redraw, re-encode, or alter the video stream.

## Music brief

`music.json` is the persisted input/output contract. It contains:

- `genre`: the Review Beats preference;
- `duration_seconds`: measured with `ffprobe` from narration-only `final.mp4`;
- `narration_sha256` and `beats_sha256`: final-source identity;
- `model`: the LLM used to prepare the score;
- `minimax_state`: `description`, instrumental section tags in `lyrics`,
  `global_meta`, `vocals`, and `arrangement`;
- short LLM reasoning for review/debugging.

The LLM is responsible for concrete instrumentation, tempo, structure and
language in the MiniMax state. Python validates required fields and appends only
the universal safety constraint that the score is instrumental/no vocals. It
does not define a genre or fixed tag list.

Changing the narration, beat map, selected genre, output duration, MiniMax Space
or generation settings invalidates the old score. A stale `bgm.mp3` must not be
used.

## HF generation adapter

One focused adapter owns the private Space call:

- Space: `Neopet2001/MiniMax-Music3`.
- Endpoint: `/studio_generate`; it bypasses the Space's optional text-composer
  service because the pipeline already has an LLM-prepared Studio state.
- Authentication: `HF_TOKEN` when present; otherwise the standard Hugging Face
  token stored by `hf auth login`.
- The returned audio is downloaded, validated with `ffprobe`, converted to
  `bgm.mp3`, and stamped with the complete brief identity.
- The generated audio must cover the final-video duration. Longer audio is
  trimmed by the final mix; short/truncated/non-audio results are failures.

The adapter has no retries that substitute an old score, a stock asset, or an
alternative music model. Any failure returns `None` with a useful log message.

## Stage 5 integration and failure handling

1. Produce the narration-only final render as today.
2. Read its duration from the completed MP4.
3. Rebuild/validate the score brief and call the adapter only when its identity
   differs from the score stamp.
4. On success, mix narration plus BGM and remux to a temporary MP4, validate it,
   then atomically replace `final.mp4`.
5. On any score failure, leave the existing narration-only MP4 untouched and
   record `bgm_used: null` plus the failure reason in Stage 5 metadata/logs.

Explicit caller-provided BGM may remain an explicit authoring choice; automatic
resolution must not scan project leftovers or `assets/bgm`, and must never treat
an old auto-generated BGM as valid.

## ACE-Step removal

Remove the ACE-Step production surface:

- `stages/_acestep_worker.py`;
- ACE-Step DirectML setup/probe scripts;
- `ACESTEP_*` configuration and DirectML assumptions;
- ACE-specific tests and docs;
- all imports, comments and runtime probes referring to ACE-Step.

Keep the generic Stage 5 mix/ducking code. It accepts an explicit, validated
generated MP3 and is independent from the model that supplied it.

Do not remove the local `.venv-acestep-directml` directory in this change: it is
machine state, not a repository dependency. Deleting it requires a separate
explicit destructive-action request.

## Tests

- LLM brief uses final narration, beats, genre and measured MP4 duration, with
  no static musical prompt.
- Adapter sends the LLM-produced Studio state to `/studio_generate` and rejects
  missing, corrupt and too-short audio.
- A normal success remuxes new mixed audio into the existing video stream.
- Every failure mode leaves a valid narration-only `final.mp4` and never resolves
  an asset/default/stale score.
- Changing narration, genre, beats or duration invalidates a prior score.
- No repository code/config/test references ACE-Step after removal.

## Non-goals

- Altering panel motion, transitions, caption rendering or narration.
- Building beat-synchronised stem composition; MiniMax generates one finished
  score file.
- Guaranteeing ZeroGPU availability or quota. Those conditions intentionally
  degrade to narration-only render.
