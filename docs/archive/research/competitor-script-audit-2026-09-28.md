# Comic Shorts script audit — 2026-09-28

## Corpus and method

Used yt-dlp to enumerate the five requested Shorts pages: Flikey (134),
ComicsUnlocked (1,321), ComicCandid (860), Comic_Escape (1,098), and comiczyt
(402): 3,815 listed videos. Downloaded MP3 and used the local Groq transcriber
for 34 samples, chosen from each channel's most viewed, recent, and middle
entries. Audio and SRT files are in the git-ignored
research/competitor_audio_2026-09-28/ directory. Transcripts were checked
for narrative structure; speech recognition may miss names and punctuation.
The local archive contains 3,808 MP3s; seven listed videos require signed-in
age confirmation. The archive README lists their IDs.
The sample is descriptive. Public view counts cannot isolate a script's
effect from topic, upload age, voice, visuals, or existing audience.

| Channel | Sampled Short | Spoken shape |
|---|---|---|
| [Flikey](https://www.youtube.com/@Flikey/shorts) | [Thanos helps an old lady](https://www.youtube.com/shorts/a-IxEA-Vvz4) | An apparently kind action opens the story; a causal chain reveals the cruel consequence. The comic's irony carries the ending. |
| [ComicsUnlocked](https://www.youtube.com/@ComicsUnlocked/shorts) | [Three useless mutant powers](https://www.youtube.com/shorts/T52AmoEmiGs) | Announces the list immediately. Each power's drawback is the payoff; the final drawback lands without a separate narrator joke. |
| [ComicCandid](https://www.youtube.com/@ComicCandid/shorts) | [Rogue touches Deadpool](https://www.youtube.com/shorts/DCPFfr_i-6s) | Opens on the unusual interaction, explains the rule, then reveals the physical change. The narrator briefly adds a slang reaction. |
| [Comic_Escape](https://www.youtube.com/@Comic_Escape/shorts) | [Wolverine's children](https://www.youtube.com/shorts/AwTMifpoFmE) | Announces a three-part list, then moves from item to item without a recurring tie-back or outro. |
| [comiczyt](https://www.youtube.com/@comiczyt/shorts) | [Deadpool is hired to kill Santa](https://www.youtube.com/shorts/OG96T36QR14) | The comic's absurd premise and reversals carry the humor; the narrator mostly states causes and consequences. This is a much longer Short, so its word count does not set a 50-second target. |

Sampled median narration pace by channel was approximately 187, 188, 202,
191, and 210 words per minute respectively, in table order. This is only
a rough transcription-based estimate. At 190–205 words per minute, a
50-second script is roughly 158–171 words before any deliberate pauses.
A rigid 170–220-word body plus hook risks crowding the delivery.
The local Stage 3 importer estimates narration at 2.9 words per second;
at that setting, 110–160 words is about 38–55 seconds. The updated Q&A
prompt allows that range because the shorter reference Shorts do too.
Measured TTS speed remains the better runtime check.

## Prompt changes supported by the sample

- Keep the exact odd choice, mechanism, or reversal from the sourced comic.
  Give it space; don't replace it with a stock reaction.
- Allow question hooks for Q&A. [ComicsUnlocked's Deadpool/Wolverine
  Short](https://www.youtube.com/shorts/VgGtmFTAh_k) begins with one.
  A factual statement can work too; choose for the material.
- Stop once the last item's answer or consequence lands. A recurring
  tie-back sentence, mandatory two-joke quota, and forced loop add the
  most formulaic lines to the old prompts.
- Use fewer words when names and relationships need explaining. Preserve
  item order and the source-to-sentence audit.

## What YouTube actually says

[YouTube's Shorts search and discovery guide](https://support.google.com/youtube/answer/11914225?co=YOUTUBE._YTVideoType%3Dshorts&hl=en)
lists choosing to view, average view duration, average percentage viewed,
likes, post-watch surveys, personalization, topic interest, competition,
and seasonality. It does not give a required 3-second retention percentage,
an average-percentage-viewed penalty threshold, or a claim that replays are
the strongest signal. The [Shorts analytics definitions](https://support.google.com/youtube/answer/12220281?co=GENIE.Platform%3DDesktop&hl=en)
define early "stayed to watch" without a universal exact second count.
[YouTube's current analytics guide](https://support.google.com/youtube/answer/12220281?co=GENIE.Platform%3DDesktop&hl=en)
says a public view now counts when playback starts; it is a poor substitute
for the channel's viewed-versus-swiped and retention data.
[YouTube's retention help](https://support.google.com/youtube/answer/9314415?hl=en)
notes that a spike can also mean the segment was unclear and had to be
rewatched. [YouTube's own creator advice](https://blog.youtube/creator-and-artist-stories/transitioning-your-long-form-content-to-youtube-shorts/)
explicitly allows a question or surprising fact as a hook.

This evidence supports testing clear openings and payoffs against this
channel's own Shorts analytics. It does not prove that a wording change
alone will increase views.

## Gemini CLI style probes

The probes used fictional or transcript-derived fact sheets, with search
disabled, so they test Phase 2 writing and source obedience only. They do not
verify any real comic issue. The prompt files preserve their existing Phase 1.
The local AGY model list did not include Gemini 3.5 Flash; its available Flash
probe used Gemini 3.8 Flash.

| Probe | Earlier prompt | Revised Phase 2 |
|---|---|---|
| Three-item Q&A, Gemini 3.1 Pro | Generic question hook; added unsourced "swarming" and "didn't just attack"; inserted "later" between separate comics. | The final probe signalled the list and named its first example in a 15-word hook; 129 spoken words, three distinct payoffs, no such additions; Stage 3 paragraph mapping passed. |
| Same Q&A, AGY's Gemini 3.8 Flash | Repeated item openings were a risk. | The revised draft used a specific question hook and three distinct openings, 127 words; a final instruction now asks for a clear cause when an item starts with the answer. |
| Single-moment Thanos fixture, Gemini 3.1 Pro | Hook inflated the consequence to "world peace"; misreported a prevented future as something Stephanie actually did. | With a richer source sheet, 107 words, accurate counterfactual, and the verified consequence as the last line. |

One thin-source micro probe still showed a model failure: it inferred that
an accident itself did not happen from a beat that only established a missed
meeting. Phase 2 now explicitly forbids turning conditional details into
events. In the follow-up Gemini 3.1 Pro probe, the revised prompt returned
`NO INFO: NEED MORE VERIFIED BEATS` for that thin sheet. The audit is a
second layer, not proof that Gemini will obey perfectly; review the trace
against the quotes before producing a video.

## Existing Phase 1 limits left unchanged

The supplied micro-moment Phase 1 requires two agreeing Tier 2 sources but
its output form has only one source/quote slot per beat. The topic Q&A Phase 1
does not separately record relationship or volume evidence. These are
pre-existing verification gaps; this edit intentionally changes only writing
behavior. The new Phase 2 forbids filling any such gap from memory.
