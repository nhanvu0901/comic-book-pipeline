# Scout: turned-down questions ("None of these — find 5 more")

## What happens

Pressing **None of these — find 5 more** in Stage 1 turns down the batch on screen.
Every question in it **except the one that is ticked** is appended to the ledger
(`data/ledger/ledger.db`) as one event:

| field | value |
|---|---|
| `mode` | `qa` or `micro` (the scout mode) |
| `kind` | `rejected` |
| `text` | the question (Q&A) or the moment (micro) |
| `label` | same as `text`; for micro the issue label (`Series #N (YYYY)`) when the card has one |
| `key` | empty — on purpose (a keyed row would be seen by micro's issue gate / the shadow report) |
| `scope` / `reason_code` | `item` / `scout_reroll_rejected` |
| `refs` | `angle`, `position`, `batch_size`, `batch_id`, micro `series_issue_year`, optional `context.publication_year` |

* Idempotent: the event id is derived from `(mode, kind, normalised text, lift count)`;
  pressing again, or a question coming back in a later batch, adds nothing.
* Windows server only (the ledger's writer guard). On the Mac the click logs
  `re-roll: central ledger is read-only here…` and the re-roll carries on.
* It runs as its own UI task queued before the discovery, in a worker thread; it
  can neither delay nor fail the re-roll. The export snapshot
  (`data/ledger/export.jsonl`) is refreshed afterwards.
* The angle-fallback entry (the raw angle shown when research found nothing) is never recorded.

## What it affects — and what it does not

Discover's **question avoid list** (`avoid_list.question_avoid_lines`) now includes
the active rejections **of the same mode**:

1. this session's shown questions (newest batch first),
2. ledger rows newest first — active production/ban questions and the mode's rejections,
3. `qa_question_banlist.md` dated rows.

The **hard filter** (`youcom_scout.is_burned`) checks that whole list. Only the
prompt's AVOID section is cut to 50 (`avoid_list.PROMPT_AVOID_LIMIT`).

`rejected` is **not** in `ledger_inventory._ACTIVE`, so these stay as they were:
micro's hard issue duplicate (`Ledger.is_hard_duplicate`), `effective_state`
precedence, recap, `load_production_inventory`, `inventory_issue_keys`, the shadow
report. Only `ledger_inventory.load_question_avoid` reads rejections.

## Look at them

```
python - <<'PY'
import sqlite3
con = sqlite3.connect("file:data/ledger/ledger.db?mode=ro", uri=True)
for row in con.execute("select ts, mode, text from events where kind='rejected' order by ts desc"):
    print(row)
PY
```

## Undo a rejection

The ledger is append-only: nothing is deleted. A rejection is lifted by a **later
`proposed` event for the same text** (a manual `unbanned` event with that text lifts it too).

On the Windows server (it needs `SCOUT_LEDGER_WRITER=windows-server`), from the repo root:

```
python -c "from stages.research_scout import scout_rejections as s; print(s.lift_rejection('qa', ['<the exact question text>']))"
```

Returns `lifted`, `nothing_to_lift` (not currently rejected), `read_only`, or
`export_pending` (event saved, snapshot refresh failed — run it again). Matching
ignores case and spacing. Rejecting the same text again later records a fresh
rejection.
