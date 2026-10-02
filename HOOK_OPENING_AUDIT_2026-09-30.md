# Spoken opening audit — 2026-09-30

## Corpus and method

The local archive has 3,808 MP3 files and 3,815 metadata rows. I matched
2,034 SRT transcripts to video IDs, titles and public view counts (53.4% of
MP3s). The transcriptions are machine generated, so a first caption is only
an approximation of a spoken sentence. I compared the top and bottom view
quartiles **within each channel**, not across channels.

| Channel | MP3 | Usable SRT | Coverage |
| --- | ---: | ---: | ---: |
| ComicsUnlocked | 1,319 | 1,319 | 100% |
| ComicCandid | 860 | 570 | 66% |
| Flikey | 132 | 132 | 100% |
| Comic_Escape | 1,095 | 6 | <1% |
| comiczyt | 402 | 7 | <2% |

Only the first three channels have enough current transcripts for a
quantitative comparison. The 13 transcribed videos in the last two channels
were selected earlier and skew toward popular examples. The rest of their
MP3s still need transcription. Across all 2,034 SRTs, the first cue starts at
time zero or within 0.32 seconds. Its median length is 14 words and 4.16
seconds; this is caption segmentation, not a measured retention window.

Public views do **not** reveal where viewers dropped, why a video was shown,
or which hook caused a result. Upload date, topic, distribution, visual edit,
and channel size are not controlled here. YouTube says Shorts ranking considers
whether a viewer chooses to watch, average view duration, average percentage
viewed and enjoyment, alongside personalization and competition
([YouTube Help](https://support.google.com/youtube/answer/11914225?co=YOUTUBE._YTVideoType%3Dshorts&hl=en-GB)).
Our own *Stayed to watch* and retention curves are needed to test this writing
change ([YouTube Analytics Help](https://support.google.com/youtube/answer/12220281?co=GENIE.Platform%3DDesktop&hl=en)).

## What the transcript comparison actually shows

| Channel | First spoken sentence, median words: top / bottom view quartile | Title keyword overlap in first cue: top / bottom |
| --- | ---: | ---: |
| ComicsUnlocked | 9 / 16 | 0.491 / 0.340 |
| ComicCandid | 18 / 14 | 0.472 / 0.417 |
| Flikey | 11 / 14 | 0.509 / 0.461 |

The keyword overlap is an approximate ratio of title content words repeated
in the first cue. It is a descriptive association, not a causal threshold.
Top-view openings vary in syntax: ComicsUnlocked uses questions in 37.4% of
its top quartile versus 28.0% at the bottom; ComicCandid uses them in 6.3%
versus 13.4%; Flikey uses them in 9.1% versus 6.1%. A universal “no
questions,” 12-word cap, or “name in first three words” rule is unsupported.
For example, [ComicCandid's Rogue/Deadpool video](https://www.youtube.com/shorts/DCPFfr_i-6s)
places Rogue around word eight of its opening yet had about 17 million public
views at this snapshot.

The more useful unit is **hook → next fact → first movement**. These are
hand-checked examples, not a claim that their openings caused their views:

- [ComicsUnlocked: Wolverine without adamantium](https://www.youtube.com/shorts/OiK7zYakjQQ)
  (about 21 million views): a concrete question at 0–3.5s; Magneto's removal
  supplies a cause by 7.7s; the healing consequence appears by 11.9s.
- [Flikey: Thanos and the older woman](https://www.youtube.com/shorts/a-IxEA-Vvz4)
  (about 15 million): an odd act at 0–2.5s; its apparent kindness is
  challenged by 8.2s; the delay mechanism appears by 13.5s.
- [comiczyt: Deadpool and Santa](https://www.youtube.com/shorts/OG96T36QR14)
  (about 9.2 million; example only because channel coverage is tiny): an
  unusual job at 0–2.4s; a question and obstacle follow; the reversal arrives
  around 11.7–15.3s. A setup-led opening can work without giving away the
  result immediately.
- [ComicsUnlocked: Aunt May and Jameson](https://www.youtube.com/shorts/t9cM1yHaNLg)
  (about 4,200): the title promises Aunt May, but she first appears in the
  spoken story at about 45.9s. The opening spends its time on other characters.
  This illustrates title-to-opening drift, not proof of why views were low.
- [ComicsUnlocked: Batman's yellow paint](https://www.youtube.com/shorts/NxHwNY_QiPw)
  (about 7 million) reveals the paint at 20.5s; a lower-view telling of the
  same story [reveals it earlier](https://www.youtube.com/shorts/AHqnkYXfYBA).
  Therefore an early final reveal is not a sound universal rule.

## Writing rule adopted in Stage 3

1. Let the title/question/moment and the hook make the same **specific
   promise**: a person plus an action, outcome, or concrete puzzle. A listener
   should know which story began after the first line.
2. The next spoken line adds a *different sourced* cause, action, obstacle,
   mechanism, or consequence. The line after that advances toward the answer.
   Do not translate the hook twice or pause for a character biography.
3. Use a question, statement, or short setup when it fits the available facts.
   Preserve the comic's reveal order. Do not force a fixed word count beyond
   actual importer and runtime constraints.
4. In micro-moment mode, the opening chain serves one scene; in Q&A mode,
   the hook names the theme and an unusual item-1 example; in recap, the
   opening chain leads into the story's next event. Every factual clause
   still needs a source.
5. Compare our own *Stayed to watch* and early retention after publication.
   This corpus provides writing hypotheses, not opponent retention data or
   a promise of matching opponent view counts.
