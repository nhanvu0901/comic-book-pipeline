# Documentation

Start with the [project README](../README.md) for installation, supported formats, and pipeline commands. Paths in prose and code blocks are relative to the repository root unless stated otherwise.

## Guides

- [Scout rejected questions and ledger behavior](guides/scout-rejected-questions.md)
- [Background music assets](../assets/bgm/README.md)
- [Fonts](../fonts/README.md)

## Architecture and contracts

These documents preserve design decisions and schema contracts. Some opening status notes describe the original implementation date; consult the current code and project README for today's behavior.

- [Q&A / Explore Answer design](architecture/explore-answer-design.md)
- [Micro-moment v2 specification](architecture/micro-moment-v2-spec.md)
- [Research planner design](architecture/research-planner-design.md)

## Working data and executable prompts

These files stay in their existing locations because scouts and the pipeline read them directly:

- [Question backlog](../qa_question_bank.md) and [question banlist](../qa_question_banlist.md)
- [Writer templates](../prompts/)
- [Research prompt versions](../research_prompts/)
- [Project agent instructions](../AGENTS.md)

## Historical research and implementation records

The [archive index](archive/README.md) contains dated competitor audits, implementation plans, handoffs, and verification reports. They document the evidence and decisions at the time; they are not current setup instructions or additional work queues.

## Adding documentation

Keep setup and first-run instructions in the project README. Put maintained usage guides in `docs/guides/`, design and schema references in `docs/architecture/`, and completed investigations or handoffs in the appropriate `docs/archive/` subfolder. Add a navigation link here or in the archive index. Keep runtime prompts and state files at the paths expected by their callers.
