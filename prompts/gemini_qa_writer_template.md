GEMINI PROMPT — Q&A NARRATION WRITER (Gen-Z voice), GROUNDED

Run settings: Gemini 3.1 Pro. Temperature 1.0 (default — do not lower it).
Google Search grounding ON. Thinking level HIGH. Runs in two turns: let it
finish PHASE 1, then reply WRITE.

Operator note: after reviewing the audit, paste only the spoken lines under
FINAL SCRIPT into the Stage 3 UI. Its preview counter includes any audit text
you paste, even though the importer removes that text.

You write the narration for a roughly 40–55-second YouTube Short that answers one
comic-book question. I paste the finished text straight into my video pipeline.
This job runs in two phases. Do not start PHASE 2 until I type WRITE.

THE QUESTION:
{{QUESTION}}

THE SCOUT JSON — one object per answer item:
```json
{{SCOUT_JSON}}
```

Read each object like this:
item_number -> the item's fixed position. Your script keeps this order.
entity -> who or what the item is about
source_comic / source_year -> the item's comic (already volume-checked)
how_or_why -> what happened and why it answers the question — your first beat
drawable_moment -> the moment the picture for this item shows
relationships / stakes_why -> who these people are to each other and why it matters (when present)
verification_note -> where the research found it; open those sources first
reader_url -> pipeline data, never narrated

Every item goes in the video, in item_number order. Never skip, merge or reorder
one: the comic items are structured in this order and each paragraph of
your script is matched to its item by position. If an item cannot be grounded,
that is a stop condition below — write NO INFO and name the item.

That gives you ONE beat per item. You still need 2-3, so search only for the
missing setup/payoff beats. And open the sources in verification_note once: if
they do not say what how_or_why claims, treat the beat as unsourced.

PHASE 1 — GROUND EACH ITEM

The scout proved each item exists. That is not the same as knowing enough to
narrate it. Each item gets 40 to 60 words of screen time, and if you only have
the one-line summary above, the other fifty words come from your own memory.
That is where invented feats get in, and they always get in through the middle
of an item — the detail nobody thinks to check.

Source tiers

| Tier | Sources | May it establish a beat? |
| --- | --- | --- |
| 1 | the scanned page; a panel-by-panel breakdown showing images; publisher preview pages | Yes, alone |
| 2 | Marvel/DC Fandom issue synopsis; League of Comic Geeks; a professional review written at release (CBR, AIPT, Newsarama, ComicsBeat, Polygon) | Yes, if two agree |
| 3 | Reddit, Quora, forums, tweets, YouTube titles or descriptions, listicles, uncited fan wikis, anything AI-written | Never |

For every answer item, collect

  - 2 to 3 beats, each one plain sentence, each with its own Tier 1 or 2 URL
    and a verbatim quote from that source. One beat is the setup, one is the
    payoff.
  - Volume + year confirmed against a source. Series get relaunched under
    identical titles; Vol. 5 attributed to Vol. 6 ships and never gets caught.
  - Comic or adaptation? If the beat is really from a film, game, or animated
    version, say so and flag the item.

Three things that are claims, not facts

1.  Outcome does not imply choreography. The source says he survived. You have
    "he survives". You do not have "he takes the blast head-on and walks out of
    the crater" unless a source says the crater.
2.  State of mind is a claim. Laughing, terrified, unbothered, smug — only if a
    source names it. Otherwise it is yours, and you may not have it.
3.  Numbers are claims. No durations, counts, distances, or power levels unless
    quoted.

Stop conditions — write NO INFO and stop
- Any item has fewer than 2 sourced beats.
- Any item's payoff — the thing that makes it an answer to the question — is
  Tier 3 only.
- Two Tier 2 sources contradict each other on what happened, with no Tier 1
  tiebreak. Name which item failed and which condition tripped. I would rather
  swap one item than ship a script with a hole in the middle of it.

PHASE 1 output

Then stop and wait.

PHASE 2 — WRITE THE SHORT (only after I type WRITE)

The deliverable is an ENGLISH voiceover artifact. Write every hook and every
spoken line in English; do not translate it into the language of my messages
or surrounding project instructions. Return only the output blocks below.

The goal is a comic Short with the story movement of the reference channels:
a concrete odd fact immediately, a quick explanation of how it happened,
and a final result that lands without a narrator punchline. This is a
research-based writing target, not a claim about YouTube's algorithm.

SOURCE AND ORDER
Use only the verified Phase 1 sheet. Keep every answer item in its input order
and make exactly one paragraph for each. Never add a person, action, motive,
emotion, distance, number, relationship, or consequence. An item about a
different comic is a separate case: do not write "later", "meanwhile",
"then", or "after that" between items unless the sheet explicitly says those
events happened in that order. Use "First", "Next", or "Last" only when the
listener needs orientation. If an item lacks a sourced payoff, answer NO INFO
and name the item.
An invented alternative is still an invented fact. Do not say that someone
"didn't fight", "could have built" something else, "never used" an ability,
or did an action "without" another action unless the sheet verifies both.
Likewise, a casual modifier is a claim: "brief", "immediately", "ordinary",
"terrified", and "massive" need support. A conditional event does not prove
that event happened or did not happen in the actual comic timeline.

THE STORY ENGINE
Before writing, silently make one four-part card per item:
1. ANSWER: the exact construct or thing being asked about, in everyday words.
2. PROBLEM: the sourced situation that made the character use it.
3. MECHANISM: what the thing actually did.
4. RESULT: what changed because of it.
Use only slots the sheet supports. Do not pad an empty slot. Each item should
move from its specific answer or problem to a new result. If the result is
the funny part, say it plainly and stop. The comic supplies the punchline.

HOOK
Draft three different hooks internally and print them in HOOK OPTIONS. For
MULTIPLE items, each hook must tell the listener this is a list about the
theme AND name one sourced, unusual example from item 1. A hook about only
the first item makes the next item feel like a new video. You may use the
number of supplied items as the list count, but do not imply these are all
the examples in comic history or the objectively strangest ever. A hook
like "What were the strangest things X made?" is too generic on its own;
add the concrete first example. One candidate may be a question; the
others can be statements. For a SINGLE item, name its odd construct or
result without promising a list. Reject a hook built from an imaginary
alternative ("instead of fighting", "when he could have made a shield").
Choose the one a stranger understands quickly and that item 1 pays off.
Aim for 6–15 words; 20 is the hard
maximum for the Stage 3 importer. Do not claim an unsourced general rule or
call anything "insane", "epic", or "unbelievable" in place of detail.
If the character's name is unfamiliar, include the power or object that
makes the odd action intelligible; a surname alone is a weak anchor.

SCRIPT
The hook is its own line. Start item 1 immediately on the next paragraph.
For each item, give the answer early in the paragraph, explain only the
needed cause or rule, and finish with its verified result. Spend words on
a mechanism or consequence, not on a second description of how surprising
the item is. The final paragraph ends on the final item's result; there is
no separate recap or outro. Keep each item paragraph over 20 words so the
Stage 3 importer does not mistake it for a closing line.
Start consecutive items from different facts when the sheet permits: one
may start with the answer, another with the problem, another with its
strange mechanism. Compare the first five words of each item paragraph;
if they follow the same sentence pattern, recast one without adding facts.
Vary the opening only when the cause stays clear. If an item begins with
the answer, attach its sourced problem in the same sentence or the next
with a clear connection. Never put the setup after the answer as an
unconnected flashback.

Make the spoken language clean and connected. Use ordinary verbs and
contractions. One sentence can carry two related beats with "but" or "so"
when a person could say it in one breath. Mix short and medium sentences.
The first comic-specific oddity should arrive in the hook or first item
sentence. A stranger must understand who did what without a lore lecture.
Use a brief plain tag for an unfamiliar name only if the relationship or
action would otherwise be confusing. Do not read issue numbers, dialogue,
panel descriptions, title text, emojis, or formatting aloud.

HUMOR
Default to zero narrator jokes. In the 34 reference transcripts, the comic's
choice or consequence usually does the work. If ONE truly specific dry
reaction makes a verified detail clearer or sharper, it may be included
after the fact, never instead of the fact. Do not write a reaction because
a quota demands one. No canned scale-mismatch line, meme slang, job
application/HR/therapy/bills comparison, "certainly a choice", "rough
Tuesday", "man's cooked", or a paragraph-ending tie-back. Never explain
why your reaction is funny. A sad payoff stays sad.

LENGTH AND SOURCE CHECK
Aim for about 110–160 spoken words including the hook at this pipeline's
2.9-word-per-second estimate. The fact sheet decides the length: a short,
dense script is better than filling to a number. If the verified beats
cannot carry the target, return NO INFO; do not invent detail.
The Phase 1 mention of 40–60 words per item is a research warning, not a
spoken-word quota.

Read the result aloud. Remove any sentence that merely repeats the theme,
announces that a twist is coming, or tells the viewer how to feel. Compare
with PREVIOUS SCRIPTS when provided, and replace a repeated opening,
observation, or ending. Check every factual clause, including the hook,
against an item and beat. A non-factual reaction must be labeled REACTION;
if it implies an event or feeling not in the sheet, cut it.
Pay special attention to negative clauses: "didn't", "never", "without",
and "could have" are factual claims too. Trace modifiers and causal words,
not only the main verb; remove a clause if the quoted evidence cannot
establish its full meaning.

OUTPUT EXACTLY IN ENGLISH, WITH NO PREFACE

HOOK OPTIONS
1. <concrete sourced hook>
2. <different concrete sourced hook>
3. <different concrete sourced hook>
CHOSEN HOOK: <number>

SPOKEN WORD COUNT: <actual count of FINAL SCRIPT only>
FACT TRACE
S1 | <first few words> | Item <n>, Beat <n> [and additional IDs if needed]
S2 | ...
<one row per spoken sentence; REACTION only for a fact-free opinion>
UNSUPPORTED FACTS: none

FINAL SCRIPT
<chosen hook>

<item 1 paragraph>

<item 2 paragraph>

<one paragraph per remaining item in input order>

Nothing after the final item's sourced result. When importing, copy only
the spoken text under FINAL SCRIPT into Stage 3.
