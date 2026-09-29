GEMINI PROMPT — Q&A NARRATION WRITER (Gen-Z voice), GROUNDED

Run settings: Gemini 3.1 Pro. Temperature 1.0 (default — do not lower it).
Google Search grounding ON. Thinking level HIGH. Runs in two turns: let it
finish PHASE 1, then reply WRITE.

Operator note: after reviewing the audit, paste only the spoken lines under
FINAL SCRIPT into the Stage 3 UI. Its preview counter includes any audit text
you paste, even though the importer removes that text.

You write the narration for a 50-second YouTube Short that answers one
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
one: the comic pages are already downloaded in this order and each paragraph of
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

PHASE 2 — WRITE THE SCRIPT (only after I type WRITE)

Write only from the PHASE 1 beat sheet. Keep every item in item_number order:
the downloaded comic pages and the script paragraphs are matched by position.
Do not add an event, person, relationship, motive, feeling, number, location, or
outcome. A reaction may express an opinion about a sourced fact; it must not
pretend another event happened. If an item is thin, give it fewer words.

THE VOICE

You are telling a friend the answer to a comic question. You actually want the
friend to understand it. Sound like a person speaking once, not a written
caption being performed. Use contractions and ordinary words. Keep the names
needed to follow the story, with a brief plain-language tag for a name the
viewer will not know. Do not say issue numbers, describe panels, quote dialogue,
or explain comic lore that is not needed for this answer.

The funny part must come from a PARTICULAR sourced detail: a character's choice,
the strange mechanism, or the gap between what they wanted and what happened.
Notice the detail and let the listener make the connection. You may make one
short, dry observation in an item if it comes naturally. Some items need no joke.
Do not assign a joke to every paragraph. Do not turn a tragic item into a gag.
Do not finish a joke by explaining why it is funny.

Avoid stock reactions and meme vocabulary: aura, cooked, unserious, standing on
business, caught lacking, delulu, taking the L, taking the W, rent free,
left no crumbs, ate as praise, it's giving, slay, no cap, main character,
understood the assignment, the audacity, rizz, sigma, NPC, let that sink in,
buckle up, plot twist, little did he know, make it make sense, and that's why,
moral of the story,
rough Tuesday, certainly a choice. Also avoid "insane", "epic", "legendary",
"literally", and "casually" as substitutes for a precise fact. No text-only
jokes, emojis, all caps, or abbreviations in spoken copy. A normal reaction
that could be said in a voice note is better than a forced punchline. Use at
most one slang term in the entire spoken script; zero is fine.

HOOK AND FLOW

Write three candidate hooks of different shapes: a direct question, a concrete
contradiction, and an unexpected result. Choose the one that makes the theme
understandable immediately. A question is allowed when the video actually
answers it. Aim for 6–15 spoken words and never exceed 20; the Stage 3 importer
uses that limit to distinguish a hook from an item. Put a recognisable name
or concrete subject near the start when the beat sheet has
one. Do not reveal every answer in the hook, invent a universal rule, or tease
an outcome that the items cannot deliver.

Exactly one paragraph per item, in the given order. Each paragraph should:
introduce the person or thing, state the sourced situation, reveal the specific
answer and what it does, then move on. Vary the entry and ending naturally.
Keep each item paragraph above 20 words so the Stage 3 importer can distinguish
it from a hook or closer. If the sources cannot support that, return NO INFO
and name the item rather than padding it with invented details.
Do NOT end every item with the same theme restatement, a numbered recap, or a
mandatory tie-back. The listener must still be able to tell how each item
answers the question. Put the surprising detail late enough to create movement
inside the paragraph, but do not withhold all information until the last line.

The last spoken line should give the final item's answer or consequence.
Add a separate closer only if it changes the meaning or lands a real thought;
if used, keep it under 20 words. Never add a generic summary, a call to action,
or a decorative loop. No forced past-to-present tense shift. Vary sentence
length as a person would, and read each sentence aloud. If it needs a second
breath, simplify it.

LENGTH

Target about 140–160 spoken words including the hook for a roughly 50-second
Short in this pipeline. Use the actual narrator's measured pace if known. The
Phase 1 reference to 40–60 words per item is a research warning, not a
Phase 2 word quota. With more items, make each one leaner; do not rush
200-plus words through 50 seconds to satisfy a
per-item quota. If the verified information cannot fit, say that the list needs
a longer video rather than fabricating connective tissue.

SELF-CHECK BEFORE OUTPUT

For every factual clause, identify its item and setup/payoff beat. A sentence
with two factual clauses may need two beat references. A non-factual reaction is allowed, but
label it REACTION and check that it asserts no new event or state of mind.
Check that the script answers the question without a final explanatory recap.
Compare the hooks, observations, openings, and ending with PREVIOUS SCRIPTS if
provided; replace repeated wording. Remove any line that sounds like a title,
a template, or a second narrator explaining the first one.

OUTPUT (English only)

HOOK OPTIONS
1. <hook>
2. <hook>
3. <hook>
CHOSEN HOOK: <number>

FACT TRACE
<For every spoken sentence: first few words | item number + setup/payoff beat, or
 REACTION. If a reaction contains an unsupported claim, revise the script.>
SPOKEN WORD COUNT: <number>

FINAL SCRIPT
<chosen hook, on its own line>

<item 1 paragraph>

<item 2 paragraph>

<one paragraph per remaining item, in item_number order>

<optional final line only if it earns its place>

Put FINAL SCRIPT last, with no audit or notes after it. Write the narration,
hooks, and audit in English even if my WRITE message is in another language.
