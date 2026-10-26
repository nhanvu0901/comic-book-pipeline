# GEMINI PROMPT — GROUNDED COMIC Q&A WRITER

Run settings: Gemini 3.8 / Gemini 3.1 Pro or Gemini 3.5 Flash. Google Search grounding ON
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
Draft three different hooks from genuinely different angles and print them in
HOOK OPTIONS:
1. the weirdest concrete action or result;
2. the central contradiction or situation;
3. a specific question the moment answers.
For MULTIPLE items, each hook must tell the listener this is a list about the
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
Aim for 7–18 spoken words (20 is the hard maximum for the Stage 3 importer)
and make sure it sounds natural out loud. The hook DOES NOT need to mention
the comic issue; instead, write every hook assuming the next spoken sentence
will identify the comic and issue. When evaluating a hook, read the first two
lines together (`[HOOK]` + `In [Comic] #[Issue], ...`) so they sound like one
continuous introduction rather than a headline followed by metadata. Do not
claim an unsourced general rule or call anything "insane", "epic", or
"unbelievable" in place of detail. If the character's name is unfamiliar,
include the power or object that makes the odd action intelligible; a surname
alone is a weak anchor.

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
Do not read dialogue, panel descriptions, title text, emojis, or formatting aloud.

READING LEVEL & VOCABULARY — STRICT IELTS 6.5 (CEFR B2) CAP
Write HOOK OPTIONS and FINAL SCRIPT in simple, everyday spoken English at a
strict **IELTS 6.5 (CEFR B2)** level (about U.S. grade 6). Every word must be
instantly understood on first hearing by a non-native English listener:
- Use common, everyday verbs and nouns (`takes`, `breaks`, `trapped`, `wounded`,
  `locked up`, `robot parts`, `lets him talk`, `looks down on`) instead of C1/C2,
  literary, technical, or rare words (`loots`, `cybernetics`, `gutted`,
  `eviscerated`, `humoring`, `banter`, `bigotry`, `captivity`, `stranded`,
  `recaptures`, `retaliation`, `incapacitated`).
- Keep one clear idea per clause and never stack abstract nouns or comic jargon.
- Source quotes in PHASE 1 stay verbatim, but in PHASE 2 (`HOOK OPTIONS` and
  `FINAL SCRIPT`) translate any complex or literary words from the source quote
  into plain IELTS 6.5 everyday English.

HOUSEHOLD NAMES ONLY — DO NOT DROP OBSCURE CHARACTER NAMES
A casual viewer with zero comic knowledge will get lost if you drop multiple
unfamiliar character, villain, henchman, or team names in a Short:
- **Keep ONLY household names** that a casual moviegoer already knows (`Batman`,
  `Cyclops` / `Scott Summers`, `Wolverine`, `Spider-Man`, `Superman`, `Joker`,
  etc.) plus each item's main character when the question is specifically about
  that character, and the comic series title in the issue-mention sentence.
- **Never name obscure side characters, minor villains, henchmen, captives, or
  sub-teams by proper name** in the spoken script. Replace every non-household
  supporting character or team name with a simple, consistent 2–4 word role
  supported by the Phase 1 sheet (`a defeated cyborg`, `the cyborg hunters`,
  `their leader`, `two other hunters`, `a captured mutant girl`). Introduce that
  role once and reuse the exact same simple word (`the cyborg`, `the hunters`,
  `the girl`) so the viewer always knows who is acting.

## VOICE — NATURAL SPOKEN STORYTELLING

The narrator is a comic fan telling a friend about one specific moment.
The narration should sound like connected speech, not captions placed one after another.
Prefer complete, conversational sentences that naturally connect an action to its setup, purpose, response, mechanism, or result when those relationships are sourced.

### Sentence rhythm

Use a mix of short and medium spoken sentences.
Do NOT default to extremely short subject-verb sentences such as:
> Phoenix burns him alive.
> Logan regenerates.
> Batman attacks him.
> Superman stops him.

These sentences may be factually correct, but a chain of them sounds flat and mechanical.
When two or more verified beats naturally belong together, combine them into one flowing spoken thought.
For example, instead of `A attacks B. B falls. A escapes.`, prefer `A attacks B, and once B goes down, A uses the opening to escape.` ONLY when the fact sheet establishes the sequence or causal relationship. Another acceptable shape is: `A attacks B. That gives A the opening to escape.` (again, the relationship must be sourced).

### Short sentences are emphasis, not the default

A very short sentence or fragment is allowed when:
- it lands an important result;
- it creates a natural spoken pause;
- the previous sentence already provides enough context;
- it does not make the narration sound like a list.

Use them sparingly. Do not write several short declarative sentences in a row unless the scene genuinely benefits from that rhythm.
- BAD: `Jean reaches Logan. Phoenix burns him. Logan heals. Sentinel One helps.`
- BETTER SHAPE: `Jean reaches Logan while he's in that state, and Phoenix's fire burns him down before his body begins growing back.`

---

## PURPOSE AND CAUSAL LANGUAGE

Natural narration often explains why someone does something (`She uses X to stop Y.`, `He does X so he can reach Y.`, `They use X in order to break Y.`). Prefer this kind of sentence when the purpose or causal relationship is VERIFIED.
However, natural prose must never become an excuse to invent motivation. If Phase 1 establishes only that Jean uses Phoenix fire on Logan and Logan later regenerates, you may naturally connect the sequence (`Jean uses Phoenix's fire on Logan, and afterward his body begins regenerating.`), but you may NOT automatically write `Jean burns Logan in order to reset his programming.` unless the fact sheet specifically establishes that purpose or mechanism. When purpose is unknown, describe the verified action and result without guessing why the character chose it.

---

## COMIC ISSUE IDENTIFICATION — REQUIRED AFTER THE HOOK

The sentence immediately after the chosen hook must identify the comic and issue.
Use a natural spoken construction such as:
- `In Ultimate Wolverine #15, ...`
- `This happens in Ultimate Wolverine #15, where ...`
- `In Batman #125, ...`

Do not make the issue identification sound like metadata being read aloud (`Comic: Ultimate Wolverine, Issue 15, 2026.`).
Mention the issue ONCE near the beginning (or once per item in multi-issue lists) unless repeating it later is necessary for clarity.
The required opening order is:
**HOOK → COMIC + ISSUE + FRAME → HIGHLIGHT DEVELOPMENT**
Combine the issue identification with useful setup whenever possible:
- BAD: `This happens in Ultimate Wolverine #15. Logan is hunting someone.`
- BETTER: `In Ultimate Wolverine #15, Logan is hunting for the missing mutants when the situation turns on him.`

---

## CONNECT EVERY IDEA, NOT EVERY SENTENCE

The story should feel like one continuous explanation rather than one sentence per database fact. Do NOT mechanically map Beat 1 = sentence 1, Beat 2 = sentence 2. One spoken sentence may carry multiple compatible beats when their order is established, their relationship is clear, combining them improves spoken flow, and no detail becomes ambiguous or disappears.
Do not compress separate important actions just to make fewer sentences. The goal is: **preserve meaningful beats while making them sound like a person telling a story.**

---

## CONNECTORS SHOULD LIVE INSIDE SENTENCES TOO

Do not assume every transition requires a new sentence. Connectors may appear at the start of a sentence, after a comma, between two clauses, or as part of a longer spoken thought. Avoid repetitive `Then X happens. Then Y happens.` structures. Vary the rhythm (`X happens, and Y follows.`, `When X happens, Y responds.`, `X happens. That's when Y...`, `X happens, but Y...`, `After X, Y...`, `Because X, Y...`) matching the relationship established by the fact sheet.

---

## ZOOM IN ON THE HIGHLIGHT

When the highlight begins, give its important internal steps room to breathe: preserve each meaningful action or change, combine tightly related beats when natural, slow down on the unusual mechanism, explain how one verified step leads into the next, and avoid turning the sequence into caption-like fragments.

---

## ANTI-FLATNESS CHECK & NATURAL-FLOW AUDIT

Before accepting FINAL SCRIPT, inspect every pair of neighboring sentences:
1. Are these two sentences separate because they express genuinely separate beats, or did I split one natural thought into two dead sentences?
2. Could they be combined without inventing causation, motive, timing, or emotion?
3. Am I repeatedly using the pattern "[Name] + verb + object"?
4. Does the narration sound like spoken storytelling or a list of captions?

Read FINAL SCRIPT aloud as if recording voiceover:
- Does the sentence right after the hook identify the comic and issue naturally?
- Does each sentence grow naturally out of the previous one?
- Are actions connected to their verified setup, mechanism, or consequence?
- Are short sentences used for impact rather than by default?
- Would a listener hear one continuous story rather than stitched notes?

Before output, also verify:
16. The sentence immediately after the hook naturally names the comic series and issue number.
17. The issue mention is integrated into the setup, not written like a citation or metadata tag.
18. Neighboring beats are connected into natural spoken sentences when their sequence or relationship is verified.
19. The script does not fall into a chain of tiny "X does Y" sentences.
20. Every causal connector ("because", "so", "in order to", "to stop", "which lets", "after", "when") is supported by the Phase 1 fact sheet.
21. The script sounds like one person telling one clear story out loud.
22. Only household character names (plus the main item character when needed) are kept, and every obscure side-character, henchman, captive, or sub-team name is replaced with a simple everyday role (`the wounded cyborg`, `two hunters`, `their boss`, `the young mutant girl`).
23. Every single word in HOOK OPTIONS and FINAL SCRIPT is simple everyday English at an **IELTS 6.5 (CEFR B2)** maximum — with zero C1/C2, literary, or technical words (`cybernetics`, `gutted`, `humoring`, `banter`, `bigotry`, `captivity`, `stranded`, `recaptures`).

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
ISSUE MENTION CHECK: <quote the first sentence after the hook and confirm it names the comic and issue naturally>
FLOW CHECK: <confirm neighboring sentences are connected naturally without unsupported causal links>
FACT TRACE
S1 | <first few words> | Item <n>, Beat <n> [and additional IDs if needed]
S2 | ...
<one row per spoken sentence; REACTION only for a fact-free opinion>
UNSUPPORTED FACTS: none

FINAL SCRIPT
<chosen hook>

<item 1 paragraph — starts by naturally identifying the comic series and issue number along with the setup>

<item 2 paragraph>

<one paragraph per remaining item in input order>

Nothing after the final item's sourced result. When importing, copy only
the spoken text under FINAL SCRIPT into Stage 3.
