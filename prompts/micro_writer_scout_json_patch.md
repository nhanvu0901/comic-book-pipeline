# PATCH — feed the You.com scout JSON into the Grimframe micro-moment writer

Apply to `GEMINI PROMPT — GRIMFRAME MICRO-MOMENT WRITER`. Two regions change; everything
from "# PHASE 2 — WRITE THE SHORT" onward is untouched.

A micro moment is ONE scene from ONE issue, so you run `stages/youcom_scout confirm` on
that single moment (not `enumerate`). The scout returns ONE object.

---

## REPLACE the `## INPUT` block with:

## INPUT

```
MOMENT:      <<one line: character — what happens — series, volume, #issue (year)>>
SCOUT JSON:  <<paste the single confirm object from stages/youcom_scout>>
```

The scout object carries: `verbatim_sentence`, `source_url` (+ `second_source_url`),
`volume_and_year`, `comic_or_adaptation`, `single_issue_or_multi`,
`subject_main_in_issue`, `reprint_check`, `verdict`.

If the scout `verdict` is `NOT CONFIRMED` or `CONFLICTING`, output `NO INFO` immediately
and stop — the moment is not safe to build on.

---

## REPLACE all of `# PHASE 1 — BUILD THE FACT SHEET` (down to, but not including,
## `# PHASE 2`) with:

# PHASE 1 — INGEST THE SCOUT, THEN BUILD OUT THE FACT SHEET

The scout confirmed the moment **exists** and handed you one verified sentence with a
URL, the resolved volume, and the adaptation and reprint checks. That is your first beat
and your metadata — do not redo it. But a micro Short narrates a scene from setup to
payoff, and one sentence is not a scene. You need **4 or more beats**, so the scout has
done maybe a quarter of PHASE 1. The rest is a real search, and it is where invented
content gets in.

## Step 1 — Seed from the scout

- `verbatim_sentence` + `source_url` becomes **B1**, tagged `[scout]`, at its source's
  tier. Do not re-verify it.
- Carry `volume_and_year`, `comic_or_adaptation`, `single_issue_or_multi`,
  `subject_main_in_issue`, and `reprint_check` straight into your output — done.

## Step 2 — Search out to 4+ beats (the real work)

Run at least 5 distinct searches to carry the moment from setup to payoff: the issue
title with the character name, the issue number, "review", "synopsis", "recap", and the
name of the specific event inside it. The scout gives you the payoff beat; you are mostly
hunting the setup beats that lead to it. Every ADDED beat obeys the tier table below.

**The scout does NOT give you relationships.** Who these people are to each other is the
failure mode nobody catches until the comments do, and the scout JSON has no field for
it — you must search it out yourself, with its own source, same as always.

## Step 3 — Distrust guard (the scout is a search engine, not a fact-checker)

Its domain filter is reliable; its judgement is not, and its `verbatim_sentence` is often
a wiki *Notes* field or a review paraphrase rather than the scanned page. So:
- **Open the `source_url`.** If the verbatim sentence is not on that page, demote B1 to
  unsourced and re-earn it in Step 2.
- If a Step-2 source **contradicts** the scout on who did what, flag `CONFLICT`; the
  higher tier wins.

## What a beat is

A **beat** is one thing that happens, one plain sentence, one URL, one verbatim quote
from a Tier 1 or Tier 2 source.
- "Wolverine walks into the pool" is a beat.
- "Wolverine, exhausted and grieving, walks into the pool" is a beat plus two inventions.

## Source tiers (for beats you ADD in Step 2)

| Tier | Sources | May it establish a beat? |
|---|---|---|
| **1** | the scanned page; a panel-by-panel breakdown showing images; publisher preview pages | **Yes, alone** |
| **2** | Marvel/DC Fandom issue synopsis; League of Comic Geeks; a professional review written at release (CBR, AIPT, Newsarama, ComicsBeat, Polygon) that describes the scene | **Yes, if two agree** |
| **3** | Reddit, Quora, forums, tweets, YouTube titles or descriptions, listicles, uncited fan wikis, anything AI-written | **Never** |

Tier 3 may tell you where to look. Tier 3 may never establish a beat.

## Beat rules

1. Every beat needs a **named subject who does something.** If a source reports only an
   outcome, record it and mark it `NO AGENT` — narrate the outcome, never invent the agent.
2. **Choreography is not implied by outcome.** "The building collapses" is not "he
   punches through the support column and the building collapses."
3. **State of mind is a claim.** Fear, regret, love, hesitation, betrayal — beats only if
   a source names them. A scout sentence asserting a feeling still needs a source for it.
4. Record the **relationship** between the people, with its own source (Step 2).
5. **Adaptation check per beat** for anything you add — the scout only checked its own.
6. **Same-issue check** for anything you add — mark any beat from a different issue.

## Stop conditions — output `NO INFO` and stop

- After Step 2, fewer than 4 sourced beats.
- The payoff beat is Tier 3 only, or rests only on a scout beat that failed the URL check.
- Volume or year cannot be resolved (the scout gave it — but if your search contradicts
  it, this trips).
- Two Tier 2 sources contradict on who did what with no Tier 1 tiebreak.

Returning `NO INFO` is a success. A confident script built on a scene that did not happen
costs a full production cycle.

## PHASE 1 output

```
COMIC: <series, volume, #issue (year), publisher>
CARRIED FROM SCOUT: volume=<> | comic/adaptation=<> | single/multi=<> | subject-main=<> | reprint=<>

BEATS
B1 | <one plain sentence> | [scout] | Tier <n> | <URL> | "<verbatim quote>" | URL-check: pass/fail
B2 | <one plain sentence> | [added] | Tier <n> | <URL> | "<verbatim quote>"
B3 | ...
B4 | ...
B5 | ...

RELATIONSHIPS
R1 | <X is Y's ___> | Tier <n> | <URL> | "<verbatim quote>"    (searched by you — not in scout JSON)

NAMES I MUST USE | <name — 3-6 plain words a moviegoer would understand>
ALL BEATS IN ONE ISSUE | yes / no — <explain>
GAPS | <what I could not confirm and therefore cannot write>
SEARCHES RUN | <list>
```

Then stop and wait.
