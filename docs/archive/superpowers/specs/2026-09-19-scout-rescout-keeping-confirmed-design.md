# Re-scout while keeping what is already confirmed

Date: 2026-09-19
Status: approved, ready for implementation
Branch: `feat/scout-single-selection-flow`

## Problem

After verifying a selection, a user commonly ends up with a mix — one `confirmed`,
one `inconclusive`, one `rejected`. There is no way to go looking for replacements
for the two bad ones while keeping the good one. The only route back to fresh
candidates is a feedback re-run, which starts the whole round over and throws away
gating that has already been paid for.

That feature cannot be built on the current data model, because **candidate ids are
positional**:

```python
# stages/research_scout/workflow.py, _extract_candidates()
candidate.setdefault("id", f"candidate-{index}")
```

`candidate-1` means nothing more than "first in this round's list". After another
research round it is a different comic entirely.

**This is already a live defect, not just a blocker.** `rerun_general()` bumps the
revision and returns to `GENERAL_DRAFT` without clearing anything:

```python
def rerun_general(self, session_id, feedback=""):
    session.revision += 1
    session.state = SessionState.GENERAL_DRAFT
    return self.store.save(...)
    # selected_specific_candidate_ids: untouched
    # specific/evidence_gate.v1.json: untouched
```

So a `confirmed` gate written for `candidate-1` in round 1 is displayed against
whatever lands at position 1 in round 2. Green verdict, wrong comic, no error. This
predates the single-selection work.

## Design

### 1. Candidate identity — namespace by revision

Round 1 keeps today's `candidate-N` ids. Every later round writes
`r{revision}-candidate-{n}`. Ids from different rounds can no longer collide.

Candidates carried forward from an earlier round **keep their original ids** and are
prepended to the new round's list, so their gates stay valid without rewriting them.

Rejected alternatives: content-derived ids (a hash of `series_issue_year` plus entity)
drift whenever the model rewords a field; a separate artifact for kept candidates
forces `_candidates_by_id` and `_gate_assignments` to read two sources for no gain.

### 2. New action

`rescout_keeping_confirmed(session_id)`, allowed from `CANDIDATE_REVIEW`:

1. Read the gate artifact; collect the candidate ids whose `verdict` is `confirmed`.
2. Set `session.kept_candidate_ids` to those ids.
3. Prune `specific/evidence_gate.v1.json` down to those candidates' gates.
4. Set `selected_specific_candidate_ids` to those ids.
5. `revision += 1`, state to `GENERAL_DRAFT`.
6. Append a `FeedbackNote` naming what is already held and what was turned down.

Refuse with a clear error when no gate is `confirmed` — there is nothing to keep, and
the plain feedback re-run already covers that case.

### 3. `run_general` honours the carry-over

Before overwriting `general/candidates.v1.json`, read it and pull out the candidates
named by `kept_candidate_ids`. Prepend them to the new round's candidates, write the
merged list, then clear `kept_candidate_ids`.

`general/candidates.rev{N}.v1.json` archives the merged list — what the user actually
saw that round.

### 4. Exclusions need no prompt change

The "already held / already turned down" list travels as a `FeedbackNote`.
`_intent_with_feedback()` folds notes into the fallback prompt and the planner reads
them on the planner path, so both routes see it. No `general_qa.v3.md`.

### 5. Fix the existing defect

Plain `rerun_general()` must also clear `selected_specific_candidate_ids` and the gate
artifact. Namespaced ids alone would turn the corruption into dangling references
rather than wrong ones; clearing makes it correct. Without this the new action is safe
but the old path stays broken.

### 6. UI

In `CANDIDATE_REVIEW`, once verification has produced at least one `confirmed` gate:

```
[ Re-scout, keep confirmed (1) ]   costs one research call
```

Hidden when nothing is confirmed. English, matching the rest of the screen.

## Testing

Test-driven throughout.

- Ids from round 2 never collide with round 1.
- A kept candidate keeps its id and its gate, and is **not** gated again — assert this
  with a tripwire that raises if the model is called for it.
- Gates for dropped candidates are pruned.
- After a re-scout the selection is exactly the kept ids.
- `candidates.rev{N}.v1.json` records the merged list.
- `rescout_keeping_confirmed` refuses when nothing is confirmed.
- Plain `rerun_general` clears the selection and the gates.
- A session written before this change still loads and its `candidate-N` ids resolve.

## Out of scope

Prompt and policy rewrites; Stage 2+; the art pipeline.
