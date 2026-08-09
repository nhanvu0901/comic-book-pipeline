# PATCH — feed the You.com scout JSON into the Q&A narration writer

Apply to `GEMINI PROMPT — Q&A NARRATION WRITER`. Two regions change; everything from
"# PHASE 2 — WRITE THE SCRIPT" onward is untouched.

---

## REPLACE the INPUT block (the `THE QUESTION` + `THE ANSWER ITEMS` section) with:

**THE QUESTION:** `<<QUESTION>>`

**THE SCOUT JSON** — paste the `candidates` array produced by `stages/youcom_scout`
(the `enumerate` items you chose, each with its `confirm` record). Every object already
carries, per answer item: `series_issue_year`, `what_visibly_happens`,
`verbatim_sentence`, `source_url` (+ `second_source_url`), `volume_and_year`,
`comic_or_adaptation`, `single_issue_or_multi`, `subject_main_in_issue`,
`reprint_check`, `well_known_or_deep_cut`.

```
<<paste the scout JSON here — one object per answer item>>
```

Use only the items in this JSON. Do not add answer items of your own.

---

## REPLACE all of `# PHASE 1 — GROUND EACH ITEM` (down to, but not including,
## `# PHASE 2`) with:

# PHASE 1 — INGEST THE SCOUT, THEN FILL THE GAP

The scout already did the expensive half. For each item it found a real source, pulled
a **verbatim sentence**, resolved the **volume + year**, ran the **adaptation** and
**reprint** checks, and judged whether the subject is the issue's **main character**.
Do not redo that work. Ingest it.

But the scout gives you roughly **one** beat per item, and each item gets 40-60 words of
screen time — you still need **2 to 3 beats per item**. That second and third beat is
the gap, and the gap is where invented feats get in. Your PHASE 1 job is to fill it from
sources, not from memory.

## Step 1 — Convert each scout item into a seed beat

For every object in the JSON:
- Its `verbatim_sentence` + `source_url` is **B.1**, at the tier its source belongs to.
  Tag it `[scout]`. Do not re-verify a CONFIRMED beat.
- Carry `volume_and_year`, `comic_or_adaptation`, `single_issue_or_multi`,
  `subject_main_in_issue`, and `reprint_check` straight into your output. Those checks
  are done — restate them, do not repeat the search.
- **Skip any item whose scout `verdict` is `NOT CONFIRMED` or `CONFLICTING`.** Name the
  one you dropped so I can swap it before you write.

## Step 2 — Fill to 2-3 beats per item (this is the real search)

Search only for the MISSING beats — the setup or the payoff that the scout's one
sentence does not already cover. One beat is the setup, one is the payoff. Every beat you
ADD obeys the full tier table below.

## Step 3 — Distrust guard (the scout is a search engine, not a fact-checker)

The scout's domain filter is reliable; its judgement is not. The 2026-08-05 pilot
double-counted UK reprint magazines as separate issues and mislabeled famous moments as
deep cuts, and its `verbatim_sentence` is often lifted from a wiki *Notes* field or a
review paraphrase — not the scanned page. So:
- **Open the `source_url`.** If the verbatim sentence is not actually on that page,
  demote the beat to unsourced and treat it like any Step-2 gap.
- If your own search **contradicts** the scout on what happened, flag `CONFLICT`; the
  higher tier wins.
- **Re-judge `well_known_or_deep_cut` yourself.** If you can recount three beats of the
  moment from memory, it is well-known no matter what the scout labeled it. The channel
  wants the answers to be deep cuts; a mislabel here wastes the item.

## Source tiers (for beats you ADD in Step 2)

| Tier | Sources | May it establish a beat? |
|---|---|---|
| **1** | the scanned page; a panel-by-panel breakdown showing images; publisher preview pages | **Yes, alone** |
| **2** | Marvel/DC Fandom issue synopsis; League of Comic Geeks; a professional review written at release (CBR, AIPT, Newsarama, ComicsBeat, Polygon) | **Yes, if two agree** |
| **3** | Reddit, Quora, forums, tweets, YouTube titles or descriptions, listicles, uncited fan wikis, anything AI-written | **Never** |

## Three things that are claims, not facts (applies to scout beats too)

1. **Outcome does not imply choreography.** "He survived" is not "he took the blast
   head-on and walked out of the crater" unless a source says the crater.
2. **State of mind is a claim.** Laughing, terrified, unbothered, smug — only if a source
   names it. A scout `what_visibly_happens` that asserts a feeling still needs a source
   that names the feeling.
3. **Numbers are claims.** No durations, counts, distances, or power levels unless quoted.

## Stop conditions — write `NO INFO` and stop

- After Step 2, any item still has fewer than 2 sourced beats.
- Any item's payoff — the thing that makes it an answer — is Tier 3 only, or rests only
  on a scout beat that failed the Step-3 URL check.
- Two Tier 2 sources contradict on what happened, with no Tier 1 tiebreak.

Name which item failed and which condition tripped. I would rather swap one item than
ship a script with a hole in the middle of it.

## PHASE 1 output

```
ITEM 1 — <character> — <series, volume, #issue (year)>
B1.1 | <plain sentence> | [scout] | Tier <n> | <URL> | "<verbatim quote>" | URL-check: pass/fail
B1.2 | <plain sentence> | [added] | Tier <n> | <URL> | "<verbatim quote>"
CARRIED FROM SCOUT: volume=<> | comic/adaptation=<> | single/multi=<> | subject-main=<> | reprint=<>
DEEP-CUT RE-JUDGED: <well-known / deep cut> — <one line why>

ITEM 2 — ...
ITEM 3 — ...

DROPPED (scout verdict not CONFIRMED): <item or "none">
GAPS: <what I could not confirm and therefore cannot write>
SEARCHES RUN: <list — should be FEWER than before, since Step 1 is free>
```

Then stop and wait.
