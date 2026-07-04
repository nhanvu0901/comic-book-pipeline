# Comic-book-pipeline — project notes

## Design principle: don't overfit the pipeline to one specific case
- When fixing a problem found on ONE comic, make the fix GENERAL, not a special-case for that comic.
- Prefer general mechanisms (aspect-ratio rules, thresholds, score functions, data enrichment from real sources) over hard-coding a comic's title, page number, character name, or magic constant.
- Before committing a fix, ask: "Does this generalize to other comics, or am I just patching this one example?" If it only helps the current comic, rethink it.
- Examples of GENERAL fixes done right (keep this style): reveal-order rule + dedupe guard (Stage 3), small-panel 30% padding `_pad_pct_for` (Stage 5), landscape→contain+blur in `_prepare_panel_frame` (Stage 5), SDK plot prompt reference-source list + emotional-spine capture (Stage 1). None name a specific comic.
- Data-level fixes (editing one project's `comic_context.json` / `narration.json`) ARE fine and expected per-comic — that's not overfitting. Overfitting = baking case-specific logic into the shared CODE.

## Model delegation (multi-agent routing)
For difficult tasks — or whenever Master asks — operate as a multi-agent team:
- **Fable 5 = deep-planner + orchestrator.** Fable holds the plan and delegates; it does the thinking about *what* to do and coordinates the workers, but hands the heavy lifting to the pinned models below.
- **`deep-reasoner` — pinned to Opus 4.8.** Use for reasoning-heavy phases: architecture, debugging complex issues, algorithm design. Think thoroughly, return a concise conclusion the orchestrator can act on.
- **`fast-worker` — pinned to Sonnet.** Use for mechanical tasks: boilerplate, tests, formatting, simple edits. Execute efficiently.
Spawn subagents with the matching model per task type (Agent tool `model:` override); reserve Opus for genuinely hard reasoning and route grunt work to Sonnet.
