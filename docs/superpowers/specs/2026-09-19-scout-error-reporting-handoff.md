# Handoff — the scout screen cannot show an error

Date: 2026-09-19
Branch: `feat/scout-single-selection-flow`, head `a6efd7b`, working tree clean, 16 commits
ahead of `main`, pushed to `origin`.

Written for whoever implements this next. It assumes no prior context on the branch.

---

## 1. Where things stand

The evidence gate was starving: it was told to check "every cited URL" against evidence
that never contained those URLs, so nothing ever reached `confirmed`. That is fixed and
**verified working on live data** — session `ef542ff1` on the Windows box, question *"What
is the hidden cost of Deadpool's healing factor?"*:

| candidate | verdict | the gate's own reason |
|---|---|---|
| candidate-2 | `confirmed` | "The CBR article corroborates ... No contradictions or spin found." |
| candidate-5 | `confirmed` | "confirmed by the cited CBR article (fetched), which explicitly states ..." |
| candidate-6 | `confirmed` | "The cited CBR article explicitly states ..." |
| candidate-4 | `rejected` | "the cited CBR article covers *Deadpool: Invisible Touch #4*, **not that issue** ... the issue attribution is factually incorrect" |
| candidate-9 | `rejected` | "describes Deadpool losing his healing factor ... which is **the opposite** of the claim" |

Before the fix the same question produced zero `confirmed` and five variations of "the
cited article is not present in the raw evidence". The two rejections above are correct
ones: a misattributed issue number, and a source that says the opposite of the claim.

**So the pipeline now works. What does not work is telling the user about it.**

## 2. The symptom

With three confirmed and two rejected, the user approved the selection and pressed
**Create project**. `create_project_from_session` refused — correctly, because two
candidates are rejected and `override` was not set.

What appeared on screen was a Python traceback, rendered in red, running off the right
edge of the window. The sentence that matters — which candidates failed and why — was
past the edge and unreadable. Screenshot evidence: the visible fragment ends mid-word at
the viewport boundary on every line.

Three separate defects combine to produce that.

---

## 3. Defect A — a deliberate message is rendered as a traceback

`ui/bridge.py:960`

```python
def format_exception(e: BaseException) -> str:
    tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
    return tb[-2000:]
```

Every failure in this screen goes through here, via `_run_busy`'s except branch.

But `stages/research_scout/project_factory.py:164-172` raises a message that was written
to be read by a person:

```python
raise ValueError(
    "production gates failed:\n"
    + "\n".join(_describe(r) for r in reports)
    + "\n(override to create the project anyway)"
)
```

`_describe` (`project_factory.py:119`) formats one candidate per block: a label line, then
the gate's reason quoted beneath. That is already the right output. Wrapping it in a stack
trace buries it, and `tb[-2000:]` keeps the *last* 2000 characters, so on a long trace the
message survives only by luck of length.

This codebase raises `ValueError` for user-facing conditions throughout — `production
gates failed`, `QA requires three to five selected candidates`
(`project_factory.py:208`), `MICRO requires exactly one selected candidate`,
`InvalidTransition` (a `ValueError` subclass, `workflow.py`). An unexpected crash is
anything else.

**Direction.** Show `str(e)` alone for the deliberate kinds, and keep the traceback for
genuinely unexpected exceptions. Do not delete the traceback path — when something really
breaks, the stack is the only clue there is.

Whoever does this should decide how to tell the two apart and say why in the code. A
marker class (`ScoutUserError(ValueError)`) that the workflow and factory raise is more
honest than type-sniffing `ValueError`, but it touches more files; type-sniffing
`ValueError` is smaller and will misclassify a genuine `ValueError` bug as a tidy message.
Either is defensible. Pick one and write the reasoning down.

## 4. Defect B — the error bubble does not wrap

`ui/screens/s1_research_scout.py:283`

```python
def _system_bubble(text: str, *, danger: bool = False) -> ft.Control:
    return ft.Row([
        ft.Text(text, size=11, color=DANGER if danger else TEXT_MUTED,
                text_align=ft.TextAlign.CENTER),
    ], alignment=ft.MainAxisAlignment.CENTER)
```

Compare its siblings, `_user_bubble:254` and `_scout_bubble:267`. Both wrap their content
in a `ft.Container(..., width=_BUBBLE_WIDTH)` — `_BUBBLE_WIDTH = 640`, line 58.
`_system_bubble` sets no width at all, so a long single line has nothing to wrap against
and extends past the viewport.

This is why the text ran off the edge. It would do the same for any long system message,
not just errors — this one is simply the first message long enough to expose it.

**Direction.** Give `_system_bubble` the same width constraint as the other two. Multi-line
error text should also keep its newlines: `_describe` emits one block per candidate and
that structure is worth preserving on screen. Left-align long text rather than centring it;
centred multi-line paragraphs are hard to read.

## 5. Defect C — the error names a control the screen does not have

The message ends with `(override to create the project anyway)`. At that moment the
session is in `PRODUCTION_GATES` and the screen is `_production_gates_bubble`
(`s1_research_scout.py:554`), which offers a project-name field, **Create project**, and
**← Back to candidates**. There is no override control on it.

The override checkbox lives in `_candidate_review_content` behind `_needs_override`
(defined line 136, used line 515). `override_holder[0]` is read at line 995 when creating.

So a user following the instruction has to work out for themselves: go back to candidates,
tick a box, approve again, then create. The message tells them to do something the screen
in front of them cannot do.

**Direction.** Either put the override checkbox on the production-gates bubble too — it is
the screen where the consequence lands — or change the message so it names the actual
route. The first is better for the user; the second is smaller. Note that
`override_holder[0]` already survives the hop between the two screens (see the comment at
line 409-411), so surfacing the same holder on the second screen should not need new state.

**Also worth considering, but ask before doing it:** in this session the right answer was
not to override at all. Three candidates were confirmed and two were correctly rejected;
QA accepts three to five. Dropping the two rejected ones yields a clean project. Nothing on
screen suggests that. A line along the lines of "3 of 5 confirmed — you can drop the other
2 and continue" would have been more useful than an override prompt. That is a product
decision, not a bug fix.

---

## 6. How to reproduce

1. On the Windows box: `D:\code\comic-book-pipeline`, branch `feat/scout-single-selection-flow`.
2. `run_ui_lan.bat` (or `.venv\Scripts\python.exe -m ui --lan --port 8550`).
3. The finished session is already on disk: `research_sessions/ef542ff1ca714066a347b295d784d6ca`,
   state `production_gates`, five selected, three confirmed and two rejected. Resume it and
   press **Create project**.

No API spend is needed — the gates are already written.

## 7. Verification

`python3 -m pytest` from the repo root. Do **not** use `.venv-chatterbox`.

Baseline at `a6efd7b`: **3 failed, 1484 passed, 11 skipped**. The three failures are
pre-existing and ffmpeg-driven, unrelated to any of this:

- `tests/art/test_assemble.py::test_render_chapter_card`
- `tests/art/test_assemble.py::test_overlay_chapter_cards_preserves_duration`
- `tests/test_outro_card.py::test_build_outro_card_makes_clip_of_right_duration`

Do not try to fix them. Do report if the count moves.

Tests worth writing for the above:

- A `production gates failed` message reaches the screen as its own text, with no
  `Traceback (most recent call last)` in it.
- An unexpected exception still surfaces a traceback.
- `_system_bubble` constrains its width the way `_user_bubble` and `_scout_bubble` do.
- The multi-line shape of `_describe`'s output survives rendering.
- Whatever route Defect C takes, a user in `PRODUCTION_GATES` with overridable failures can
  reach the override without guessing.

The UI tests are headless against `StrictFakePage` — see `tests/test_s1_research_scout_ui.py`
for the existing patterns.

---

## 8. Smaller things left open on this branch

These came out of earlier work on the same branch and are not blocked by anything above.

**Nothing stops a test from opening a socket.** Two test files were silently making real
network calls once gating started fetching cited URLs — they still passed, just slowly, on
DNS failure. It was caught from a jump in run time, not a red test. A module-scoped autouse
fixture now closes `cited_sources.urllib.request.urlopen` in four files, but that is a
patch per file. A global guard in `conftest.py` that fails any test opening a socket would
end the whole class of leak.

**Fetched pages are not written to the session artifact.** `search.<id>.v1.json` records
only the You.com call. A verdict that turned on a fetched CBR article cannot be
reconstructed from disk the way a search-based one can. The audit trail has a hole in it.

**`stages/youcom_scout.py` hardcodes its own 8-domain list** including `reddit.com`, around
line 41. `reddit.com` was removed from `specific_web_search` in
`research_policies/source_profiles.v1.json` because it is not a primary source for
confirming an issue and year, and with `count=8` it crowded everything else out. That
standalone script was left alone deliberately — it is legacy and not part of the scout
workflow's policy — but it is now inconsistent with the policy file.

**The branch has not been reviewed.** Sixteen commits, several thousand lines. A
`/code-review` pass before merging to `main` is worth the time.
