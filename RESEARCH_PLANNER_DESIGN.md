# Research Planner — Stage 1 self-authored-schema design (2026-08-21)

## Goal

The Stage 1 chat agent must research ANY story/character question the Master
types — not a fixed question shape — using the You.com Research tool
(effort=standard always), authoring its own output schema per question, in ONE
unified flow that serves Q&A mode, micro mode, and ad-hoc lookups alike.

Design principle applied: no question taxonomy in code. Questions are
coordinates, not categories — the planner fills 4 knobs; code compiles them.
A new kind of question never needs new code.

## The ResearchPlan contract (4 knobs)

One small JSON object produced by a cheap planner LLM per research round:

```json
{
  "unit": "one character",
  "cardinality": "exhaustive",
  "ranking": "",
  "extra_fields": [
    {"name": "resistance_type", "type": "string",
     "description": "immune / broke_free / assisted / moral_refusal / hypothetical"}
  ],
  "research_prompt": "List EVERY character who resisted, broke free of, ..."
}
```

- **unit** — what ONE candidate represents (free text: "one character",
  "one issue", "one single drawn scene", "one verdict", "one alternate
  version"). Injected into the prompt: "One candidate per {unit} — never merge."
- **cardinality** — exactly one of:
  - `exhaustive` — the list IS the answer; sweep every source, never stop at
    the famous names (Q&A lists: resisters, kills, deaths, Mjolnir lifters).
  - `options` — candidates are alternatives for the Master to pick from
    (micro-moment proposals, question discovery).
  - `pinpoint` — a closed, pre-bounded set; cover exactly the named range
    ("exact issues Deathstroke appears in DCeased: Unkillables #1-3",
    claim verification, "what happens in issue X").
- **ranking** — "" for unordered, else the criterion as free text
  ("fan-consensus best", "most brutal", "strongest shown on page"). Non-empty
  ranking makes the compiler add a required `rank_reason` field and the prompt
  demand ordered output with justification. (Added for superlative questions —
  "best feats of the Hulk" — which are curation, not plain enumeration.)
- **extra_fields** — 0..6 additional per-item fields the planner wants
  (`issue_number`, `what_deathstroke_does`, `winner`, `fan_consensus_note`…).

## Pipeline (one flow, no mode branches)

```
Master message (chat, Stage 1)
  │
  ▼
[1] PLANNER — config.SCOUT_EVIDENCE_MODEL (DeepSeek) via OpenRouter,
    response_format=json_schema, 2 attempts (same retry shape as
    openrouter_gate.review). Input: user_intent + feedback_log + mode hint +
    the 8-family few-shot table + digest header. Output: ResearchPlan.
  │  invalid twice → FALLBACK PLAN (see below)
  ▼
[2] SCHEMA COMPILER — pure code, no LLM.
    core fields, always present, never overridable:
      title, summary, series_issue_year, what_visibly_happens, evidence_urls
    + validated extra_fields (+ rank_reason when ranking != "").
    Emits the You.com-strict wrapper: additionalProperties:false everywhere,
    every property required, candidates[] + notes. NO minItems/maxItems
    (API rejects them — probed 2026-08-21).
  ▼
[3] PROMPT ASSEMBLER — pure code. Invariant rules first (cite only retrieved
    URLs; exact series + issue + year when a source names it; what VISIBLY
    happens on the page; never invent; hypotheticals labeled, last), then
    plan.research_prompt, then the cardinality block, then the ranking block,
    then the digest.
  ▼
[4] TOOL — YouComClient.research(..., effort=config.YOUCOM_RESEARCH_EFFORT)
    — standard; deep stays a config knob only.
  ▼
[5] GATES + CHAT UI — unchanged. evidence.validate_candidate and the candidate
    cards read the guaranteed core fields; extra fields render as detail lines.
```

## Compiler validation rules (the safety boundary)

The planner never writes raw JSON Schema. The compiler rejects and falls back
when a plan violates any of:

- extra_fields count > 6
- field name not matching `^[a-z][a-z0-9_]{0,39}$`
- field name colliding with a core field, `id`, or `flags`
- type outside {"string", "string_array"}
- cardinality outside the 3 allowed values
- research_prompt empty or > 4000 chars

Fallback plan = today's behavior exactly: the mode's fixed template
(general_qa.v2.md / general_micro) + the fixed `general_output_schema()`.
The planner can therefore never break a research round — only improve it.

## What "mode" still means

qa|micro no longer selects prompts or schemas. It remains only:
1. the production gate at decide_specific (QA: 3–5 selected, MICRO: exactly 1),
2. the created project's pipeline_mode (explore_answer vs micro_moment),
3. a hint passed to the planner.

Pinpoint lookups need no new session state: results render as candidate cards;
the Master may stop after reading, or select and continue to production —
the existing state machine already tolerates staying in review.

## Feedback loop integration (with the chat UI)

- The plan is SHOWN as a chat bubble before results ("Each candidate = one
  ISSUE · pinpoint #1-3 · extra: what_deathstroke_does"), so the Master can
  correct the plan by typing — feedback_log feeds the planner next round,
  meaning feedback can change the SCHEMA (unit/fields/ranking), not just the
  wording of the research prompt.
- Artifacts per round: `general/plan.rev{N}.v1.json` next to
  `general/candidates.rev{N}.v1.json`; audit detail carries a one-line plan
  summary. Reruns are fully reconstructable.

## The 8 question families (planner few-shots, NOT code branches)

| family | unit | cardinality | ranking |
|---|---|---|---|
| feats & power ("best Hulk strength feats") | feat | exhaustive | yes |
| broken constants ("times Batman used a gun") | violation | exhaustive | no |
| wins/losses/deaths ("who beat Thanos 1v1") | fight/death | exhaustive | no |
| artifacts & mantles ("everyone who lifted Mjolnir") | holder/lift | exhaustive | no |
| resistances ("who resisted the Anti-Life Equation") | character | exhaustive | no |
| issue lookups ("exact issues of Deathstroke in Unkillables #1-3") | issue/beat | pinpoint | no |
| single moments ("most brutal Punisher page") | scene | options | yes |
| versions & debates ("every evil Superman across universes") | version/question | exhaustive/options | optional |

## Cost

+1 DeepSeek call per research round (~cents). Still exactly 1 You.com research
call per round (standard). Nothing else changes.

## Test plan

- Compiler unit tests: strict wrapper shape, every validation rule, reserved
  names, rank_reason injection.
- Planner fallback: invalid JSON twice → fallback plan used, audit says so.
- Workflow integration: plan artifact written per round; assembled prompt
  contains the cardinality block and unit sentence; schema contains the extra
  field; feedback in feedback_log reaches the planner input.
- Live smoke (3 families, standard effort): the Darkseid resisters question,
  the Deathstroke pinpoint, "best feats of the Hulk" — each must return
  structured candidates whose schema matches its plan.

## Out of scope (explicitly)

- Multi-call angle fan-out per round (youcom_scout-style) — future cost knob;
  the chat loop's rerun-with-feedback covers recall for now.
- Publication history / creator questions — the channel researches story and
  characters only.
