# Q&A writer contract implementation plan

> For agentic workers: use superpowers:subagent-driven-development. One scoped task; user explicitly requested Terra implementation with root orchestration.

**Goal:** Correct the active Q&A writer's format, length, and answer-type contracts using the competitor research findings.
**Architecture:** Keep the existing two-phase writer and item-to-paragraph importer. Update the active template, adding prompt-generation code only if needed to propagate authoritative existing format/budget data. Preserve source fidelity and issue identification.
**Tech Stack:** Python, Markdown prompt templates, pytest.
**Spec:** `/Users/nhanvu/Documents/code/comic-book-pipeline/docs/archive/competitor-writer-analysis-2026-11-03.md`, especially the three confirmed active-template issues.

## Global Constraints

- Scope: active Q&A template `prompts/gemini_qa_writer_template.md`, directly related prompt-generation logic in `stages/stage_3/gemini_prompt.py` only if necessary, focused tests.
- Preserve English spoken output, verified-only facts, item order, one paragraph per item, existing importer compatibility, comic + issue immediately after the hook, and comic + issue for later items.
- Preserve micro-moment behavior. Do not edit the inactive topic template as a substitute for the active template.
- Respect the current project's explicit `format` where provided and the existing general question-format detection otherwise. Multi-item direct explanations are not automatically lists; How-many/example-list questions remain lists.
- Remove conflicting 40–60 words per item instruction. Use one coherent total script target (existing 110–160 words inclusive of hook as normal target), distribute by item/source complexity rather than a per-item target. Account honestly for importer floors and larger item counts: clarity and sourced completeness must not be padded or impossible to satisfy; a justified longer script may exceed soft target rather than omit items/invent facts. No hard universal 60-second claim.
- Generalize construct/use-it language to the actual question's answer: person, action, mechanism, decision, limitation, result. No hard-coded comic names/pages/characters in logic.
- No changes to research files or production narration data. No dependency installs or secrets. No subagents from implementer. You are not alone; do not revert others' work.

## Review Focus

- Multi-issue Why/How retains a single explanation and final answer while preserving item order.
- How-many/list questions retain list framing even though the text starts with How.
- Explicit format overrides a superficially different question shape.
- One, four, and many items do not receive mutually impossible word quotas; placeholder substitution and final script import survive.
- Missing context is not silently filled with invented chronology, motives, or source facts.

### Task 1: Correct active writer contract

**Files:** active template; optional directly needed generator changes; focused new/update tests under `tests/`.
**Interfaces:** consumes existing answer_context/scout metadata and existing question-archetype helpers; produces the same saved writer prompt and script import layout. No schema changes to narration output.

- [x] Inspect active template, generator context/format selection, and existing prompt/importer tests. Establish focused baseline.
- [x] Add meaningful regressions exercising rendered prompts with direct multi-item, list/how-many, and explicit format inputs; check source/order/issue/importer contracts and coherent budget. Avoid tests that only pin large prose blocks.
- [x] Run the new tests before implementation; record the expected failures.
- [x] Implement the smallest coherent fix to the active template and any necessary generator propagation. Remove the contradictory/construct-specific clauses rather than appending more competing instructions.
- [x] Run focused tests plus relevant existing Gemini-prompt/importer/archetype tests. Record exact commands and results; distinguish prompt-contract tests from live model quality evaluation.
- [x] Self-review the complete diff, commit only task files on the isolated branch, and write the required report with changed paths, test evidence, concerns and commit SHA. Root will review and apply the verified diff to the original workspace.
