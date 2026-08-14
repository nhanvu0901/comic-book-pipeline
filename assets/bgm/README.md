# Background Music

Drop a music file here as `default.mp3`, or point `BG_MUSIC_PATH` in `.env` at one, or drop a
`bgm.mp3` (also `.m4a/.wav/.ogg/.flac`) straight into a project folder — a project-local file
wins over the shared default. **With no file anywhere, the render is narration-only** and byte
identical to a render from before the bed existed.

## What Stage 5 does with it

1. Measures the narration's own integrated loudness and puts the bed `BG_MUSIC_OFFSET_LU`
   below it (default 8 LU), so the bed tracks whatever the TTS delivered instead of assuming
   a fixed level. Mirrors EBU Tech 3343's advice to set the speech anchor first.
2. Finds the REAL silences with ffmpeg `silencedetect` on the rendered audio — *not* from
   `word_timestamps.json`, whose timings are interpolated evenly inside each sentence and
   therefore contain no silence at all.
3. Ducks the bed by `BG_MUSIC_DUCK_DB` (default -6 dB) under speech, lifting only for gaps
   longer than `BG_MUSIC_DUCK_MIN_GAP_S` (default 0.6s). Shorter gaps stay ducked: lifting for
   a 0.3s hole with a 0.25s ramp each side never reaches full level before ducking again, which
   is what pumping sounds like.
4. Loops the bed to cover the narration, mixes, then loudnorms the **full mix** to -14 LUFS.

Any problem — file missing, unreadable, unmeasurable — logs and falls back to narration-only.
A music bed can never fail a render.

## Tuning

Measured on a real 61s render: moving the bed anywhere from 0 to 25 LU under the voice changes
final integrated loudness by well under 0.1 LU, and the level *during speech* by 0.07 dB — the
duck absorbs it. So `BG_MUSIC_OFFSET_LU` is safe to tune purely by ear; it cannot break the
-14 LUFS normalisation. 8 LU is the default, chosen by ear against this narration.

The knobs are craft, not standards. No standards body (W3C, EBU, ATSC, Netflix) specifies a
music-vs-speech level; ATSC A/85's own guide for mixers says *"always mix relying on your
hearing."* See `MUSIC_SCORING_RESEARCH_2026-08-12.md`.

## Where to get music

**Do not assume "royalty-free" means claim-free.** Pixabay states that many of its contributors
register their tracks in YouTube Content ID; Epidemic Sound fingerprints its whole catalogue and
still claims Shorts even when you have safelisted correctly. The research doc above has the
verified sourcing findings and the recommended primary/fallback.
