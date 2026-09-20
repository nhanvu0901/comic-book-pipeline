# Handoff — override stops at the gate, and creation trips over a different one

Date: 2026-09-19
Branch: `feat/scout-single-selection-flow`, head `b0018fe`, pushed. Tree clean.
Baseline: `python3 -m pytest` → **3 failed, 1511 passed, 11 skipped**. The three are
pre-existing ffmpeg failures (`tests/art/test_assemble.py` ×2, `tests/test_outro_card.py`).
Do not fix them; report if the count moves.

Written for whoever picks this up. No prior context assumed.

**Read section 7 first if you only read one.** It is the cause the rest of
this document is downstream of.

---

## 1. What the user asked for

> When I tick "I have read the verdicts…" I want it to override and continue right away.
> But then it needs to gather the info detail — automate that when creating the project.
> What I choose is guaranteed to have issues.

They also believe the evidence gate is too strict and returns `inconclusive` most of the
time. Section 6 checks that claim against what is actually on disk; it does not hold up
yet, and that matters before anything is designed around it.

## 2. The reported error, and what it is not

```
ValueError: build_contexts: empty reader_url for item(s): Selective amnesia after head
injuries, Cancerous regeneration / 'dying factor', Skrull over-healing and explosion
— hand-fill reader_url in D:\...\answer_context.json then re-run with --rebuild-contexts
```

**This is not a crash, and nothing was lost.** `answer_research.build_contexts` writes
`answer_context.json` *before* it raises, by explicit design — the comment at
`stages/stage_1/answer_research.py:596` says the research must survive the failure so a
human can hand-fill and resume. On the box:

```
projects/what_is_the_hidden_cost_of_deadpools_healing_factor_in_the_c/
  answer_context.json     3728 bytes, 3 items
```

The override *worked*. Gate verdicts were overruled, creation ran, research was written.
It stopped at the last step.

## 3. Two different gates, and only one of them has an override

| | Where | What it checks | Override reaches it? |
|---|---|---|---|
| Evidence gate | `project_factory.create_project_from_session` | Are the verdicts `confirmed`? | **Yes** — the checkbox |
| Downloadability | `answer_research.build_contexts:601` | Does every item have a `reader_url`? | **No** — it has no override at all |

The checkbox is labelled "I have read the verdicts above and want to continue anyway", and
it does exactly that. The second gate is a separate fail-loud about whether the comic can
actually be fetched later, and nothing in the UI can influence it.

So "make override continue right away" is not a matter of widening the existing flag — it
is a decision about whether a second, unrelated guard should also become overridable.

## 4. Why all three reader_urls came back empty

`build_contexts` already automates what the user is asking for. At line 550 it calls
`resolve_reader_url()` for every item whose `reader_url` is blank, and only raises for the
ones that still have nothing. So the automation exists; it failed three times out of three.

The three items, read from the written `answer_context.json`:

| `source_comic` | `_parse_source_comic` gives | reader_url |
|---|---|---|
| `Deadpool: Invisible Touch #4 (2021)` | `('Deadpool: Invisible Touch', '2021', '4')` | `''` |
| `Black Panther vs. Deadpool #1 (2018)` | `('Black Panther vs. Deadpool', '2018', '1')` | `''` |
| `Deadpool Vol. 3 #3 (2008)` | `('Deadpool Vol. 3', '2008', '3')` | `''` |

The third is a plain parsing defect: **`Vol. 3` is volume notation, not part of the series
name.** No batcave series is called "Deadpool Vol. 3" — it is "Deadpool", 2008 volume, and
the year hint is already parsed out separately. Stripping `Vol. N` before searching is a
contained fix worth doing regardless of what else is decided.

The other two parse cleanly, so their failure is in the batcave search or in
`_rank_series_candidates`, not in parsing. Somebody should run `resolve_reader_url` against
those two strings directly with logging on before guessing further.

### Relevant recent change

`openrouter_gate` now coerces a `reader_url` to `""` unless it matches
`https://batcave.biz/reader/<digits>/<digits>`. That landed earlier on this branch because
the gate model was returning prose in that field — one observed value was *"Please review
the candidate's evidence URLs against the raw search results provided."* — which sailed
through the emptiness check and became a bogus reader URL downstream.

The consequence: gate-supplied reader_urls are now almost always empty for QA, so **every
item depends entirely on `resolve_reader_url`.** That is honest rather than silently wrong,
but it does mean this fail-loud fires far more often than it used to. Any measurement of
"how often does creation fail" from before that change is no longer comparable.

## 5. A separate defect visible in the same file

All three items came back `verified: False`:

```
'Selective amnesia after head injuries' not in cha[pter names]
'Cancerous regeneration / 'dying factor'' not in cha[pter names]
no Comic Vine volume named 'Deadpool Vol. 3'
```

The first two are not entity names — they are descriptions of the *cost*. `entity` is
supposed to hold the character or thing; `verify_issue` searches Comic Vine chapter names
for it, and a sentence will never match. The research prompt is filling `entity` with the
wrong kind of value for this class of question.

This is not what blocked creation, and it should not be fixed in the same change. It does
mean the `verified` flag is currently meaningless for QA sessions, so do not lean on it.

## 6. The "mostly inconclusive" claim does not hold up on the evidence available

Everything on disk right now, across both machines:

- Session `ef542ff1` — 3 `confirmed`, 2 `rejected`
- One session with gates on the Windows box — 1 `confirmed`, 2 `inconclusive`

Eight gates total: **four confirmed, two inconclusive, two rejected.** Not mostly
inconclusive. The two rejections were also correct ones — a misattributed issue number, and
a cited article that says the opposite of the claim it was cited for.

Older sessions that may have looked worse have been deleted, and the fix that gives the
gate the cited sources to read landed only hours before this was written, so most of the
user's impression predates it.

**Do not design an automatic override around this claim until it is measured.** Run a
handful of sessions on the current code and count. If `inconclusive` really dominates,
the reasons will say why — and the fix probably belongs in the gate prompt or the evidence
it is fed, not in a switch that skips the check.

## 7. The root cause underneath all of it: citations are not bound to claims

This sits before everything above. It explains the rejected verdicts, and it explains why
`resolve_reader_url` had nothing to find.

The general research round for that session returned **13 candidates built from 5 distinct
URLs**:

| times cited | URL |
|---|---|
| 5 | `marvel.fandom.com/wiki/Wade_Wilson_(Earth-616)` — the generic character page |
| 4 | `cbr.com/deadpools-healing-factor-redefined/` |
| 4 | `cbr.com/deadpool-just-revealed-what-his-healing-factors-other-weakness/` |
| 2 | `marvel.fandom.com/wiki/Deadpool_Vol_3_3` |
| 2 | `cbr.com/black-panther-deadpool-solve-death/` |

Read per candidate it is worse:

```
candidate-4  Deadpool: Invisible Touch #4 (2021)  ┐
candidate-5  Deadpool: Invisible Touch #4 (2021)  │  four separate "answers",
candidate-6  Deadpool: Invisible Touch #4 (2021)  │  one shared article
candidate-7  Deadpool: Invisible Touch #4 (2021)  ┘

candidate-2  Black Panther vs. Deadpool #2 (2018) ┐  identical citation pair,
candidate-3  Black Panther vs. Deadpool #1 (2018) ┘  two different issues claimed
```

So the search is not returning bad information. The research step found roughly three real
articles and spread them across thirteen candidates, inventing a distinct claim for each
and attaching whichever URL was to hand. The gate's two rejections were it catching exactly
that:

- *"the cited CBR article covers Deadpool: Invisible Touch #4, not that issue"* — the claim
  and the citation are about different comics.
- *"describes Deadpool losing his healing factor … the opposite of the claim"* — the
  citation is real and says the reverse of what it was cited for.

**This is also why section 4's lookups failed.** `resolve_reader_url` was sent hunting for
`Black Panther vs. Deadpool #1` on behalf of a candidate whose actual source never mentions
that issue. No resolver can find a reader URL for a comic the evidence does not discuss.

### A change made on this branch probably made it worse

`planner.CANDIDATE_TARGET` was raised from 10 to 20 earlier the same day (`ee80055`,
13:54), with what was meant as an escape hatch:

> If the sources genuinely support fewer than 20, return every one you found and say so in
> notes.

The model did not take it. Faced with a floor of twenty and about three usable sources, it
padded — which is the cheaper way to satisfy the instruction than admitting a shortfall.
That session's directory has since been deleted, so the timestamp cannot be confirmed; the
sequence of events makes it very likely it ran under the raised target.

The structural hole predates the change, though. Nothing in the schema or the prompt binds
a URL to the specific claim it is supposed to support. `evidence_urls` is a flat list on
the candidate, so one article can be cited for any number of unrelated assertions and
nothing objects.

### Three fixes, in order of how much they buy

**(a) Bind each citation to its claim.** Require the candidate to say which URL supports
its issue and year, with **a verbatim sentence quoted from that source**. A model cannot
quote a sentence that is not in the article it just read. This closes the hole rather than
narrowing it.

**(b) Refuse one source backing many candidates.** Four candidates citing a single CBR
article is padding, and it is detectable **in code** — no model judgement needed. Reject or
merge at parse time.

**(c) Stop demanding a candidate count.** Let the number follow the sources. Replace
`Return AT LEAST 20 candidates` with a target on **distinct sources**, and have the model
report how many it actually found. A source count is checkable; a candidate count invites
padding.

The target introduced at `ee80055` should come down, or be re-pointed at distinct sources
rather than candidates.

### Warning about the order of work

Doing (a) or (b) before (c) will make the candidate count **drop sharply** — possibly below
the three QA requires. That is not a new breakage. It is the first honest look at how many
answers the sources actually support. The current prompt hides that number by padding it.

## 8. The decision to make

The user wants override to carry through and the missing detail to be gathered
automatically. There are three distinct ways to read that, and they are not equivalent.

**(a) Make the resolver better.** Strip `Vol. N`; investigate the two search failures; add
a fallback lookup. Nothing becomes overridable, the gate keeps its meaning, and the common
case stops failing. Lowest risk, addresses the actual failure, does not deliver "continue
right away" when the resolver genuinely cannot find a comic.

**(b) Let override cover the downloadability gate too.** Creating with an empty
`reader_url` means the project is written with items that cannot be fetched later. Whatever
consumes `reader_url` downstream then has to tolerate blanks — check what does before
promising this, because the fail-loud exists precisely so a later stage does not discover
it. If this is chosen, the items must be visibly marked as unfetchable in
`answer_context.json`, not silently blank.

**(c) Gather the detail at creation time, interactively.** On a resolver miss, surface the
candidates it did find and let the user pick or paste a URL, rather than failing or
proceeding blind. Closest to "gather the info detail", most work, and it puts a human in
the one place where a human is actually better than the code.

My reading of the user's words is that they want (a) plus (b): fix the resolver so it
usually succeeds, and when it does not, let a deliberate override carry through rather than
stop. That reading should be confirmed before implementation — (b) alone would hide the
resolver problem instead of fixing it.

## 9. Reproducing

The failure is already on disk and costs no API calls to re-examine:

- Windows box, `D:\code\comic-book-pipeline`
- `research_sessions/` — the session whose gates are 1 confirmed / 2 inconclusive
- `projects/what_is_the_hidden_cost_of_deadpools_healing_factor_in_the_c/answer_context.json`
  — the three items with empty `reader_url`

To re-trigger: resume that session, tick the override, create the project.

To probe the resolver directly:

```python
from stages.stage_1.answer_research import resolve_reader_url
resolve_reader_url("Deadpool Vol. 3 #3 (2008)", "2008", "Deadpool")
```

## 10. Tests worth writing

- `_parse_source_comic` strips `Vol. N` from the series name and keeps the year hint.
- `resolve_reader_url` returns a URL for a volume-notated `source_comic`.
- Whatever route section 8 takes: an override that reaches `build_contexts` is covered by a
  test that asserts what lands in `answer_context.json`, not just that no exception was
  raised.
- If empty `reader_url` becomes permissible, one test per downstream consumer proving it
  tolerates a blank.

No network in tests — stub the batcave search. Note that nothing in this repo globally
forbids a socket in a test, and two test files were silently making real calls earlier on
this branch; it was caught from a jump in run time, not a red test.

## 11. Still open on this branch, unrelated to the above

- No global socket guard in `conftest.py`.
- Fetched cited pages are not written to the session artifact, so a verdict that turned on
  a fetched article cannot be reconstructed from disk.
- `stages/youcom_scout.py` hardcodes its own domain list including `reddit.com`, now
  inconsistent with `research_policies/source_profiles.v1.json`.
- The branch is 19 commits and several thousand lines with no review pass.
