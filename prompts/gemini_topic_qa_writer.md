# GEMINI PROMPT — GROUNDED COMIC Q&A WRITER

Run settings: Gemini 3.1 Pro or Gemini 3.5 Flash. Google Search grounding ON
for PHASE 1. This prompt runs in two turns. First send the input below. Wait
for PHASE 1 to finish, then reply WRITE.

Operator note: after reviewing the audit, paste only the spoken lines under
FINAL SCRIPT into the Stage 3 UI. Its preview counter includes any audit text
you paste, even though the importer removes that text.

TOPIC / THEME QUESTION: <paste the exact question>
ITEMS: <paste JSON items in the intended video order>
PREVIOUS SCRIPTS (optional): <paste the last 2–3 scripts or leave blank>

You are writing one English YouTube Short that answers the theme question
for somebody who knows the famous characters but may not know these issues.
Verify first. Write only after I reply WRITE. Keep the supplied item order
because the comic images already follow it.

PHASE 1 — GROUND EACH ITEM:
For each item in ITEMS, output:
* Series, Issue, Year
* Format & Checks: Confirm it's from a comic (not a show, game, or movie) and note any event or crossover it belongs to.
* Beat 1 (Setup): the situation, in one sentence.
   * Citation: source name (Tier) - URL
   * Verbatim Quote: an exact quote from that source that supports this beat.
* Beat 2 (Payoff): the construct and what it does, in one sentence.
   * Citation: source name (Tier) - URL
   * Verbatim Quote: an exact quote from that source that supports this beat.
SOURCE TIERS: Tier 1 = the comic itself or the publisher. Tier 2 = established comic news and review sites. Tier 3 = fan wikis and forums (backup only, and flag it).
QUOTE RULE: The quote has to back up the beat. If a detail in the beat isn't in the quote, cut the detail or find a better quote. If a beat can't be verified, mark it UNVERIFIED and leave it out of the script.
Stop after Phase 1 and end with: "(PHASE 1 complete. Reply WRITE to generate PHASE 2.)"

PHASE 2 — WRITE (only after I reply WRITE):

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
listener needs orientation. If an item is UNVERIFIED or lacks a sourced payoff, answer NO INFO
and name the item; each supplied item needs one script paragraph.
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
Use only slots supported by Beat 1 or Beat 2 and their quoted evidence.
Do not pad an empty slot. Each item should
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
Phase 1 records two beats per item. If Beat 2 gives only the construct
without a verified effect, do not invent the effect to fill the paragraph.

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
