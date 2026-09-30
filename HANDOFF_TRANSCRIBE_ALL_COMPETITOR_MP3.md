# Handoff: transcribe every competitor MP3

## Resume status — 2026-09-30 19:33 ADT

The existing resumable worker is
`/Users/nhanvu/Documents/code/comic-book-pipeline/research/transcribe_worker.py`.
Its latest dry run found **2,034 valid SRTs and 1,774 pending MP3s** across the
expected 3,808 files. The previous run stopped at Groq's 2,000 requests/day
limit. A single-file retry from the current Codex shell returned `Connection
error`, so no API transcription could be completed from this shell. The worker
manifest remains intact with 1,808 `DONE`, 226 `DONE_EXISTING`, and 1,774
`PENDING` rows; the failure list is empty. The worker's manifest reader was
corrected to preserve trailing empty error fields and thus retain attempt
counts when resumed.

Run this command in a terminal with network access after Groq requests are
available again. It skips valid existing SRTs and resumes the pending rows:

```bash
cd /Users/nhanvu/Documents/code/comic-book-pipeline
/Users/nhanvu/Desktop/facebook/groq-transcriber/.venv/bin/python \
  research/transcribe_worker.py --workers 2
```

Check progress without making API requests:

```bash
cd /Users/nhanvu/Documents/code/comic-book-pipeline
/Users/nhanvu/Desktop/facebook/groq-transcriber/.venv/bin/python \
  research/transcribe_worker.py --dry-run
```

## Objective

Transcribe all **3,808 existing MP3 files** in:

`/Users/nhanvu/Documents/code/comic-book-pipeline/research/competitor_audio_2026-09-28/`

There are five channel folders:

| Folder | Expected MP3 count |
|---|---:|
| `Flikey` | 132 |
| `ComicsUnlocked` | 1,319 |
| `ComicCandid` | 860 |
| `Comic_Escape` | 1,095 |
| `comiczyt` | 402 |
| **Total** | **3,808** |

This handoff is for the transcription worker. Do not rewrite the narration
prompts or infer hook rules until the transcript corpus is complete.

## Existing tools

The Groq transcription utility is:

`/Users/nhanvu/Desktop/facebook/groq-transcriber/transcribe.py`

Use the virtual environment that already contains its dependencies:

`/Users/nhanvu/Desktop/facebook/groq-transcriber/.venv/bin/python`

The utility uses `whisper-large-v3-turbo`, `verbose_json`, and writes SRT
timestamps. It supports MP3 input and splits files longer than ten minutes.
The `.env` in the transcriber directory must provide `GROQ_API_KEY`. Never put
the key in this repository, a log, a manifest, or a commit.

## Required output layout

Write one SRT beside each source MP3:

`<channel>/<video_id>.srt`

The MP3 filename is the YouTube video ID. Do not rename or modify any MP3.
Existing SRT files are valid completed work and must be skipped unless the
worker explicitly marks them for retry. The archive started with 34 sample
SRT files; see the current count in the resume status above.

Also write these resumable files outside the git-tracked source tree or in a
git-ignored subdirectory:

- `transcription_manifest.tsv`: one row per MP3 with `channel`, `video_id`,
  `mp3_path`, `srt_path`, `status`, `attempts`, `duration_seconds`, `error`.
- `transcription_failures.tsv`: only files that still fail after retries.
- `transcription_worker.log`: timestamps, batch number, filename, retry reason,
  and final status. Never log credentials or full API responses.

Use a temporary output such as `<video_id>.srt.part` and rename it to `.srt`
only after the transcription is non-empty and passes validation. This makes
the job safely resumable after interruption.

## Processing procedure

1. Enumerate `*.mp3` recursively and assert that the inventory is exactly
   3,808 files, with the five expected folder counts above. Stop and report if
   the count differs; do not silently omit files.
2. Enumerate existing `*.srt` files. Mark those as `DONE_EXISTING` after
   validating that they contain at least one timestamp and one non-empty text
   line.
3. Process only MP3s without a valid sibling SRT. Use bounded concurrency,
   initially **2 workers**. Increase only if the API remains stable and rate
   limits are not reached. Do not launch 3,808 requests at once.
4. Retry transient failures (connection reset, timeout, HTTP 429, HTTP 5xx)
   with exponential backoff and jitter: 30s, 90s, 180s, then 300s. Use at
   most five attempts per file. Do not retry deterministic failures such as a
   missing file, invalid audio, missing API key, or authentication failure.
5. After each successful file, flush the manifest and log immediately. A
   restart must resume from the manifest and sibling SRTs without redoing
   completed files.
6. Keep the original channel directory in the output path. Do not flatten all
   SRTs into one folder because video IDs can be audited against channel TSVs.

## SRT validation

For every produced SRT, verify:

- the file is UTF-8 and non-empty;
- every cue has an index, `HH:MM:SS,mmm --> HH:MM:SS,mmm`, and text;
- cue start is less than cue end;
- cue timestamps are non-decreasing;
- the final cue does not exceed the MP3 duration by more than 2 seconds;
- text is not only punctuation or whitespace;
- the first spoken cue starts at or after 0 seconds and is retained;
- no temporary `.part` file is counted as complete.

Do not clean, paraphrase, summarize, or remove repeated words from the SRT.
The transcript must preserve what the ASR returned. If a later analysis wants
normalized text, create a separate derived file and keep the raw SRT unchanged.

## Quality-control sample

After the full run, randomly sample at least 50 completed files, with at least
5 from each channel. Also inspect every file that has:

- an empty or unusually short transcript;
- no cue in the first 2 seconds;
- a transcription error or retry count of 3 or more;
- a duration mismatch;
- non-English text when the audio is expected to be English.

Record the sample results in `transcription_qc.tsv` with `channel`, `video_id`,
`checked`, `first_words`, `issues`, and `action`. Do not delete questionable
transcripts; mark them for review or retry.

## Completion criteria

The job is complete only when:

- the inventory still contains 3,808 MP3s in the expected folders;
- there are 3,808 valid sibling SRTs, counting the 34 existing samples;
- every manifest row is `DONE` or `DONE_EXISTING`;
- `transcription_failures.tsv` is empty, or lists explicit permanent failures
  with the exact reason and a separate retry command;
- the manifest, QC sample, and final counts have been flushed to disk;
- a restart dry-run reports zero files needing work.

The final handoff report must include total files, newly transcribed files,
existing files reused, failures, retry count, elapsed time, and the five
per-channel totals. Do not claim that all audio was transcribed until the
completion criteria above pass.

## Suggested command shape

Run from the transcriber directory so its `.env` is loaded:

```bash
cd /Users/nhanvu/Desktop/facebook/groq-transcriber
./.venv/bin/python transcribe.py \
  /Users/nhanvu/Documents/code/comic-book-pipeline/research/competitor_audio_2026-09-28/Flikey/VIDEO_ID.mp3 \
  -o /Users/nhanvu/Documents/code/comic-book-pipeline/research/competitor_audio_2026-09-28/Flikey/VIDEO_ID.srt
```

The command above is an example for one file. The existing
`research/transcribe_worker.py` already supplies the resumable inventory,
manifest, retry policy, validation, and bounded concurrency described above.
Use the resume command at the top of this handoff for the remaining files.

## What the next analysis should receive

Return the completed corpus path, manifest, QC report, final counts, and the
failure list. The next analyst can then extract the first 15 seconds, opening
sentence, first concrete event, and first reversal per video without touching
the raw audio or SRT files.
