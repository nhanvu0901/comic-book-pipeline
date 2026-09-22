GEMINI PROMPT — Q&A NARRATION WRITER (Gen-Z voice), GROUNDED

Run settings: Gemini 3.1 Pro. Temperature 1.0 (default — do not lower it).
Google Search grounding ON. Thinking level HIGH. Runs in two turns: let it
finish PHASE 1, then reply WRITE.

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
series_issue_year -> the item's comic (already volume-checked)
verbatim_sentence -> your first beat, quote it as-is
source_url -> that beat's citation (+ second_source_url if present)
what_visibly_happens -> what to narrate
comic_or_adaptation / single_issue_or_multi / subject_main_in_issue / reprint_check -> checks already done; restate, don't redo
verdict -> skip any item that is not CONFIRMED
well_known_or_deep_cut -> the scout's guess; re-judge it yourself

That gives you ONE beat per item. You still need 2-3, so search only for the
missing setup/payoff beats. And open source_url once: if the verbatim sentence
isn't on that page, treat the beat as unsourced.

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
The firewall

The beat sheet is the whole world. You choose every word, the order, the jokes,
and what to leave out. You do not add an event, a name, a number, a place, or an
emotion that is not on the sheet. If a beat is too thin to fill its 40 words,
cut it shorter and give the words to another item. Do not thicken it.

Voice — this is the part that matters most

The narrator is one comic fan telling friends about something insane they just
read. Not a documentary. Not a hype channel. A person who finds this genuinely
funny and is mildly annoyed that more people don't know about it.
It must stay instantly understandable to someone who has never read a comic.

Rule 1 — Slang carries TONE. Slang never carries INFORMATION.

Test every sentence: delete the slang words. If the sentence still says the full
fact clearly, you did it right. If deleting the slang leaves a hole where a fact
used to be, rewrite it.
✅ Good — slang on the reaction, fact stated plainly:
Ghost Rider's Penance Stare makes you feel every ounce of pain you ever caused.
Nobody walks it off. Except this guy, who just stood there like it was a mild
inconvenience.
❌ Bad — slang replaced the fact, now nobody knows what happened:
Ghost Rider hit him with the ick and he was just built different, no cap.
✅ Good — the fact does the work, the tone rides on top:
So Deadpool takes the stare, feels all of it, and starts laughing. The man has
zero regrets to punish. Ghost Rider basically found the one guy immune by vibes
alone.
❌ Bad — three slang words stacked, no actual event:
Deadpool ate that stare and it was giving absolutely nothing, lowkey unhinged
behavior fr.

Rule 2 — The engine of the joke is SCALE MISMATCH
This is the mechanic. Gen-Z humour is funny because it describes enormous things
in small, flat, unbothered language. Comics are already enormous. So your job is
almost always to undersize.

  - A god dies → rough Tuesday for him.
  - Someone tanks a planet-killer → he took it about as well as a stubbed toe.
  - A twenty-year grudge → she has been holding that since 2004. The fact stays
    huge. The register goes flat. The gap is the joke. Never do the reverse.
    Never inflate a big moment with big adjectives — "epic", "insane",
    "legendary", "absolutely unhinged" add nothing, because the moment is
    already all of those things. Inflating it is what a hype channel does, and
    it is the single fastest way to sound like every other comic Short.

Rule 3 — Joke shapes. Rotate them. Never the same shape twice in one script.

1.  The flat score. Award or subtract aura for a feat, deadpan, no build-up. Ten
    thousand aura. No notes.
2.  The undersell. State a cosmic event in the register of a minor
    inconvenience.
3.  The wrong priority. React to a trivial detail of an enormous event instead
    of the event. He punched a hole in the moon and he did it in a suit jacket.
4.  The résumé line. State the feat as if it belongs on a job application.
5.  The exhausted narrator. Treat the behaviour as a recurring annoyance. He
    does this. This is a thing he does.
6.  The abrupt stop. Fact, reaction, then a three-word sentence that just ends.
    Man's cooked.

Rule 4 — Casual language needs MORE precision, not less

One wrong word and the whole script reads as an adult impersonating a teenager,
and the audience writes off the channel, not the sentence. If you are not
certain a term is currently in use and used the way you think it is, write
plain English instead. Plain English is never cringe. Wrong slang always is.

Slang density

About one slang beat every three or four sentences. Never two in a row. Not
every line. Wall-to-wall slang reads as try-hard and stops being funny by second
fifteen. The joke lands because the plain sentences around it set it up.

Where the humour goes

Put it in the reaction to the fact, never in the fact. State what happened in
clean plain words, then react to it. That is the whole trick.

Word list — current as of 2026, treat it as expiring

Safe to use: cooked, aura / aura points / aura farming, caught lacking, crash
out / crashing out, glazing, standing on business, mid, ate, left no crumbs,
delulu, rent free, took the W, took the L, menace, unserious, the ick, -maxxing.
Dead — never use: yeet, on fleek, sksksk, bae, lit, based, fr fr, skibidi, "no
cap" used sincerely, "slay" used sincerely, "rizz" in narration, "it's giving",
"built different", "understood the assignment", written-out reactions like
"skull" or "I'm dead".

The insight still has to land

The joke rides on top of the insight. It never replaces it. Every item still
needs its plainly stated why this should have been impossible — that sentence is
the reason the video exists. If you cut the explanation to make room for a joke,
you have made a worse video.

Banned

  - Inflating a big moment with big adjectives.
  - Two slang beats in consecutive sentences, or three slang terms in one
    sentence.
  - Slang inside the fact clause.
  - Slang on a character's name or a comic title.
  - Explaining the joke afterwards.
  - "You won't believe", "wait for it", "here's the crazy part" — filler, cut
    it.
  - Rhetorical questions to the viewer. Just tell them the thing.
  - The narrator talking about himself or addressing the viewer directly.

Content rules

1.  One event per sentence. Two things happening in one sentence is the number
    one reason a viewer gets lost at speed.
2.  Only say what a comic panel can SHOW. Every line gets matched to a drawn
    panel. "He felt conflicted about his past" is unusable — there is no panel
    of that. "He drops the gun and walks out" is usable.
3.  Name famous characters, do not describe them. Write "Deadpool", not "the
    wisecracking merc with a mouth". Never stack adjectives on a name.
4.  Zero lore assumed. If an item needs backstory, give it in ONE short clause,
    not a sentence. Someone who has never opened a comic must follow every line.
5.  Do not name issue numbers in the spoken text. They kill pace. I have them
    separately.
6.  No spoiler in the hook. The hook promises; the body pays off.

THE HOOK — the highest-leverage line in the script

The 3-second window is measured, not a vibe. The 2026 Shorts benchmark is
holding above 80% of viewers at 3 seconds; a sharp drop there means the hook
failed structurally, and average-percentage-viewed under 50% is treated as a
broken hook rather than a bad topic. At narration pace, 3 seconds is about 9
words. Your old limit of 26 words was two to three times too long. A 26-word
hook finishes around second eight, five seconds after the audience already
decided.

Hard shape

  - 14 words maximum. One sentence, or two very short clauses.
  - The famous subject is inside the first 3 words. The subject is the only
    anchor the viewer gets.
  - A-tier names only in the hook. The deep-cut answer characters stay in the
    body.
  - A statement. Never a question.
  - Establish the constant, imply the exception. (E.g. Nobody survives Ghost
    Rider's Penance Stare. Three people did.)
  - One promise, not two.
  - Do not name the answers.
  - Do not overpromise.

Structure

  - Body: one paragraph per answer item, in order of increasing impact — the
    most surprising answer goes LAST.
  - Each item: who → what they did → why it should have been impossible.
  - Roughly 40 to 60 words per item.
  - Close the loop naturally. The final sentence should bring back the hook's
    key word or its opposite, short, concise, deadpan.

PHASE 2 output:
Write the script from the PHASE 1 beat sheet. The body should land around 170 to 215 words.
The hook is 14 words maximum, a statement not a question.
Write three hooks and pick the best one.
Write your entire response in English.
