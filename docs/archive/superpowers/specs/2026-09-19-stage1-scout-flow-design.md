# Stage 1 Research Scout — single-selection flow redesign

Date: 2026-09-19
Status: approved, ready for implementation plan

## Problem

Two defects, one root cause.

**1. The QA flow shows the same candidate list twice.**
`load_scout_candidates()` always reads `general/candidates.v1.json`. Both
`_general_review_content()` (renders `ft.Radio`) and `_specific_review_content()`
(renders `ft.Checkbox`) receive that same list. The user picks one candidate with a
radio, then picks three to five from the identical list with checkboxes. The radio
pick only decides which candidate gets evidence-gated; it is then discarded, because
the checkbox picks are what become the project.

**2. QA mode can never create a project.**
`workflow.research_specific()` writes one bare gate object:

```python
self.store.write_artifact(session.id, "specific/evidence_gate.v1.json",
                          gate.model_dump(mode="json"))
```

`project_factory._gate_assignments()` rejects that shape for QA:

```python
if not artifact.is_collection:
    if session.mode is ScoutMode.QA or len(gates) != 1:
        return invalid
```

Every selected candidate gets `gate is None`, producing `GateFlag.MALFORMED_OUTPUT`,
and `create_project_from_session` raises `production gates failed: malformed_output`.

No code path anywhere writes a gate collection. Tests pass only because they hand-build
`{"gates": [...]}`; `test_qa_factory_rejects_legacy_single_gate_reused_for_all_candidates`
asserts rejection of exactly what the writer always produces. No integration test covers
workflow -> project_factory for QA, which is how this shipped.

**3. Latent, blocks the fix from being enough.**
`project_factory._qa_research()` passes the gate's `reader_url` straight through:

```python
"reader_url": _first_text(gate, "reader_url") or _first_text(candidate, "reader_url"),
```

The gate schema accepts any string. An observed run returned prose:
`"Please review the candidate's evidence URLs against the raw search results provided."`
`build_contexts` only tests emptiness, so prose passes as a reader URL and suppresses
`resolve_reader_url()`. Fixing the gate shape alone moves the failure downstream.

## Design

### 1. State machine

```
GENERAL_DRAFT --run_general--> CANDIDATE_REVIEW --approve_selected--> PRODUCTION_GATES --> COMPLETE
                                   |      ^                                |
                                   +------+                                |
                              verify_selected                     back_to_candidates
```

- Remove `SessionState.SPECIFIC_REVIEW` and `ResearchSession.selected_general_candidate_id`.
- Add `SessionState.CANDIDATE_REVIEW`.
- Remove workflow actions `approve_general`, `research_specific`, `decide_specific`,
  `back_general`.
- Add `verify_selected`, `approve_selected`, `back_to_candidates`.
- `verify_selected` self-transitions `CANDIDATE_REVIEW -> CANDIDATE_REVIEW`, mirroring the
  existing `research_specific` self-transition.
- Keep `start`, `run_general`, `rerun_general`, `archive`, `discover_question`.
- `ResearchSession.selected_specific_candidate_ids` is kept. Only the `decide_specific`
  action goes; `verify_selected` now writes that field.
- `approve_selected(session_id)` takes no override argument: it only locks the selection
  and advances to `PRODUCTION_GATES`. Override is decided at creation time (section 6).

Running state is never persisted. There is no `VERIFYING` state: a crash mid-verify must
not strand a session.

### 2. Gate artifact

Keep the path `specific/evidence_gate.v1.json`. Renaming it would break `ui/bridge.py:319`,
the tests, and existing sessions while buying nothing.

`_load_gates()` already accepts `{"gates": [...]}` and `_gate_assignments()` already
resolves keyed collections correctly. Only the writer changes.

New written shape:

```json
{"gates": [
  {"candidate_id": "candidate-1", "verdict": "confirmed", "reason": "...",
   "evidence_urls": ["..."], "reader_url": "https://batcave.biz/reader/123/456",
   "flags": []}
]}
```

`candidate_id` is assigned by the workflow from the id it gated. The model is never
trusted to echo it back.

Raw search payloads are stored per candidate at
`specific/search.<candidate_id>.v1.json`.

### 3. Merge and prune

The artifact must hold exactly one entry per currently-selected candidate.

- `verify_selected(ids)` sets `session.selected_specific_candidate_ids = ids`.
- Gates for ids being verified are replaced.
- Gates for ids no longer selected are dropped.

Without pruning, `len(gates) != len(selected_ids)` and `_gate_assignments` returns
`invalid` — the original bug, reintroduced.

### 4. reader_url normalisation

In `openrouter_gate`, at parse time, coerce `reader_url` to `""` unless it matches
`https://batcave.biz/reader/<digits>/<digits>`. This keeps the on-disk artifact clean and
lets `resolve_reader_url()` do its deterministic lookup, with `build_contexts` failing
loud only when that genuinely cannot be pinned.

### 5. Parallel verification

`verify_selected` gates all selected candidates concurrently with
`concurrent.futures.ThreadPoolExecutor` — the calls are IO-bound HTTP, so threads suffice
and the method stays synchronous for the existing `bridge.run_blocking` path.

Signature: `verify_selected(session_id, candidate_ids, *, on_result=None)`, where
`on_result(candidate_id, gate_or_exception)` fires as each finishes. The UI marshals those
onto the Flet page for per-card progress.

`on_result` fires from worker threads and is for progress display only. It must not write
to the store. The gate artifact is written once, on the calling thread, after every branch
has settled — so a crash mid-verify leaves the previous artifact intact rather than a
half-written one.

One failing branch marks only its own card and offers a per-card re-verify, which calls
`verify_selected` with that single id and merges the result.

### 6. Blocking rules

| Condition | Overridable |
|---|---|
| `DUPLICATE`; gate missing or unassignable | No — a defect, not a judgement |
| verdict != `confirmed` | Yes, with explicit confirmation |
| `EXACT_ISSUE_REQUIRED`, `NO_VISUAL_EVENT`, `URL_NOT_RETURNED`, model-emitted flags | Yes, with explicit confirmation |

`create_project_from_session(session_id, project_slug, *, override=False)`. When a user
overrides, write a `gates_overridden` audit event naming each candidate and its reason.

### 7. Error messages

`evaluate_production_gates` returns `dict[candidate_id, list[GateFlag]]` instead of a flat
list, so failures can name the candidate:

```
candidate-5 (Incredible Hulk Vol. 1 #277): verdict inconclusive
  - "cited URLs not present in raw evidence"
```

### 8. UI

In `ui/screens/s1_research_scout.py`:

- Replace `_general_review_content` and `_specific_review_content` with one
  `_candidate_review_content`: checkbox, verdict badge, and per-card re-verify.
- Delete the fallback `return gates[0] if len(gates) == 1 else {}` in `_candidate_gate()`.
  That line is why all ten cards displayed one candidate's verdict. Match strictly on
  `candidate_id`.
- Buttons: `Verify selected (n)`, then an override checkbox when needed, then
  `Approve & name project`.
- Leave the right-rail `STEP 1 OF 8` label alone. It counts pipeline stages, not the
  sub-steps inside Stage 1, so collapsing two review steps does not change it.

### 9. Micro mode

Micro shares these screens, so it collapses too: tick one, verify, create. This is simpler
than today and loses nothing. It is a consequence of the shared UI, not a separate goal.

### 10. Existing sessions

Map legacy states on load: `general_review` and `specific_review` both become
`candidate_review`. Legacy bare-object gate artifacts still parse through `_load_gates`
and remain valid for Micro.

## Testing

Test-driven throughout.

The essential one: **an integration test running `workflow` through `project_factory` for
QA.** Its absence is why this bug shipped.

Also:
- `verify_selected` writes a keyed collection with one entry per selected candidate.
- Merge on re-verify; prune when the selection changes.
- `reader_url` normalisation rejects prose, landing pages, and other domains.
- Override rules: duplicates and missing gates stay blocked; verdict and model flags pass.
- Failure messages name the candidate and the reason.
- One gate failing does not abort the others.

Files needing updates: `tests/test_research_scout_project_factory.py`,
`tests/test_s1_research_scout_ui.py`, `tests/test_ui_navigation.py`.

## Out of scope

Prompt and policy changes for the evidence gate; Stage 2+; anything touching the art
pipeline.

## Implementation notes

Work on the macOS clone at `~/Documents/code/comic-book-pipeline` — clean tree, synced with
`origin/main`. The Windows checkout at `D:\code\comic-book-pipeline` shows about 78k lines
of CRLF and filemode noise when read from WSL and is unsafe to commit from.
