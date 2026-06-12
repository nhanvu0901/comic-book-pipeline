# Unify the LLM backend across comic + art (single FREE_MODEL switch, SDK-only)

**Date:** 2026-06-12 · **Branch:** `feat/art-v2` · **Status:** design approved by user (spec pending review)

## Problem

After the art long-form work, comic and art ended up with two parallel copies
of the same "which LLM backend?" mechanism:

- Comic `config.FREE_MODEL` + `stages/stage_3/_llm.py:call_with_chain`
  (uncommitted parallel work already routes EVERY text phase through the Claude
  SDK when `FREE_MODEL=False`, with an OpenRouter **fallback** on SDK failure).
- Art `art_pipeline/config.py:ART_FREE_MODEL` +
  `art_pipeline/_llm.py:art_complete` (SDK-only, **raises** on failure, no
  fallback — added 2026-06-12 at the user's request).

Everything else is already shared: the SDK client (`stages/_claude_sdk.py`:
`sdk_complete` / `sdk_complete_web` / `sdk_available`), OpenRouter keys, model
chains (`CREATIVE_LLM_MODELS`, `VLM_MODELS`), Cartesia, ffmpeg — art imports
these from comic config directly. So the ONLY genuine duplication is:

1. the backend flag (`FREE_MODEL` vs `ART_FREE_MODEL`), and
2. the routing entry point (`call_with_chain` vs `art_complete`).

The two also differ in ONE behaviour: SDK-failure → comic falls back to
OpenRouter, art raises.

## Goal

One backend switch, one routing function, for the whole repo — easier to
maintain and a single place to flip SDK↔OpenRouter later.

## User decisions (this brainstorm)

- **Comic code MAY be modified** for this refactor (the long-standing
  read-only rule is lifted for this task only).
- **Sequence:** commit the in-progress comic work FIRST (separate commit) so the
  refactor lands on a clean tree, not mixed with the feature.
- **Unify to ALWAYS RAISE — drop the OpenRouter fallback on both sides.**
  Both pipelines become "SDK-only when `FREE_MODEL=False`; raise on SDK
  failure". This is the single, intentional, user-approved FLOW CHANGE
  (comic loses its graceful fallback). Everything else stays byte-for-byte.
- **Single flag:** keep comic's `config.FREE_MODEL`; delete `ART_FREE_MODEL`.
- **VLM untouched** — region proposal always runs `VLM_MODELS` on OpenRouter.
- **Tests:** write throwaway verification tests for the refactor, run the full
  existing suite green, then DELETE the throwaway tests; also delete tests for
  removed code.

## Hard rule (with one named exception)

`DO NOT CHANGE THE LOGIC OR FLOW OF LOGIC` — except the ONE change the user
explicitly approved: removing the SDK→OpenRouter fallback so SDK failures raise.
No other observable behaviour (stage order, prompts, outputs, validators,
retries, VLM routing, OpenRouter-mode path) may change.

## Design

### Single switch
`config.FREE_MODEL` is the only backend flag. Remove `ART_FREE_MODEL` from
`art_pipeline/config.py` (and its env var). Default `False` = Claude SDK.

### `call_with_chain` (the one entry point)
In `stages/stage_3/_llm.py`:

- `FREE_MODEL == False` (default): route through the Claude SDK
  (`sdk_complete`). On success return `(text, "claude-sdk:<model>")`. On failure
  — empty output, inline rate-limit, or `validator` rejects — **raise
  `RuntimeError`** with an actionable message ("set FREE_MODEL=true to use the
  OpenRouter chain"). **Remove** the existing "falling back to OpenRouter chain"
  path for this case.
- `FREE_MODEL == True`: run the OpenRouter `chain` loop exactly as today
  (unchanged — this is now the only path that touches OpenRouter for text).
- The OpenRouter chain loop body is unchanged; it simply no longer runs as a
  fallback after an SDK failure.
- `sdk_available()` is False while `FREE_MODEL=False` → raise the same
  actionable `RuntimeError` (do not silently use OpenRouter).

### Art call sites
- Delete `art_pipeline/_llm.py` (`art_complete`, `_safe_validate`,
  `_SDK_ATTEMPTS`).
- `outline.py`, `narrate.py`, `narrate_longform.py`: re-import
  `from stages.stage_3._llm import call_with_chain` and call it directly with
  the same kwargs they already pass (`system`, `user`, `models`, `max_tokens`,
  `progress`, `label`, `validator`). No `allow_fallback` param is introduced —
  fallback no longer exists anywhere.
- Behaviour parity: art previously raised on SDK failure (no fallback) → still
  raises (now from `call_with_chain`). Art previously retried the SDK twice
  (`_SDK_ATTEMPTS=2`); `call_with_chain` makes one SDK attempt. This 2→1
  reduction is acceptable (the art callers already wrap each call in their own
  3-attempt validation loops). Documented, not hidden.

### Untouched
`stages/_claude_sdk.py`, VLM routing (`regions.py` + `VLM_MODELS`), Cartesia,
ffmpeg, all prompts, all validators, the OpenRouter-mode (`FREE_MODEL=True`)
path, grounding (`sdk_complete_web`) and hunt (`sdk_complete_web`).

## Error handling

- SDK failure when `FREE_MODEL=False` → `RuntimeError` propagates; the running
  stage fails loudly (intended). Escape hatch: `FREE_MODEL=true`.
- `FREE_MODEL=True` → unchanged: OpenRouter chain advances on rate-limit/
  timeout/validator-fail and raises only if every model fails.

## Testing

- Throwaway verification tests (deleted after the suite is green):
  - `FREE_MODEL=False`: `call_with_chain` calls `sdk_complete`, returns its
    output; the OpenRouter client is NEVER constructed/called.
  - `FREE_MODEL=False` + SDK returns None / validator rejects → `RuntimeError`,
    OpenRouter NOT called.
  - `FREE_MODEL=False` + `sdk_available()` False → `RuntimeError`.
  - `FREE_MODEL=True`: SDK NOT called; OpenRouter chain used.
- Permanent suite: full `pytest tests/ -q` must stay green. Update any test
  that asserted the removed fallback; delete `tests/art/test_llm.py` (covers the
  removed `art_complete`); update the one patch in
  `tests/art/test_narrate_longform.py` that monkeypatches `art_complete` back to
  `call_with_chain`.
- After green, DELETE the throwaway verification tests.

## Out of scope

- VLM backend selection (stays OpenRouter).
- Moving/renaming `stages/_claude_sdk.py` or merging the two config files into
  one physical file (art keeps its `ART_*` art-specific constants; only the
  duplicated `FREE_MODEL` flag is removed).
- Any prompt/validator/word-budget/variety change.
- The in-progress comic feature itself (only committed, not modified).
