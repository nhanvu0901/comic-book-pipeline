# Background Music

Stage 5 owns automatic music. It first writes a narration-only MP4, measures its true duration,
then asks the LLM to build a MiniMax Music 3 Studio brief from the final narration, beat map and
Review Beats genre preference. The private Hugging Face Space returns a project-local `bgm.mp3`.

There is no shared default track or project-folder scan. A caller may still pass an explicit,
author-selected music path to the Art renderer; a missing explicit path is narration-only.

## What Stage 5 does with it

1. Measures the narration's own integrated loudness and puts a validated generated bed `BG_MUSIC_OFFSET_LU`
   below it (default 8 LU), so the bed tracks whatever the TTS delivered instead of assuming
   a fixed level. Mirrors EBU Tech 3343's advice to set the speech anchor first.
2. Finds the REAL silences with ffmpeg `silencedetect` on the rendered audio — *not* from
   `word_timestamps.json`, whose timings are interpolated evenly inside each sentence and
   therefore contain no silence at all.
3. Ducks the bed by `BG_MUSIC_DUCK_DB` (default -6 dB) under speech, lifting only for gaps
   longer than `BG_MUSIC_DUCK_MIN_GAP_S` (default 0.6s). Shorter gaps stay ducked: lifting for
   a 0.3s hole with a 0.25s ramp each side never reaches full level before ducking again, which
   is what pumping sounds like.
4. Mixes the bed with narration, then loudnorms the **full mix** to -14 LUFS.

Any problem — LLM, HF authentication, ZeroGPU quota/queue, remote generation or validation — is
logged and keeps the already-completed narration-only MP4. A music request can never fail a
render or substitute older/library music.

## Tuning

Measured on a real 61s render: moving the bed anywhere from 0 to 25 LU under the voice changes
final integrated loudness by well under 0.1 LU, and the level *during speech* by 0.07 dB — the
duck absorbs it. So `BG_MUSIC_OFFSET_LU` is safe to tune purely by ear; it cannot break the
-14 LUFS normalisation. 8 LU is the default, chosen by ear against this narration.

The knobs are craft, not standards. No standards body (W3C, EBU, ATSC, Netflix) specifies a
music-vs-speech level; ATSC A/85's own guide for mixers says *"always mix relying on your
hearing."* See `MUSIC_SCORING_RESEARCH_2026-08-12.md`.

## Authentication

Run `hf auth login` once on the render machine, or set `HF_TOKEN`. The token needs read access
to `Neopet2001/MiniMax-Music3`; it is never written into a project file.
