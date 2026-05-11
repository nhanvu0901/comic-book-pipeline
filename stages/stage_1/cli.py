"""
CLI entry point for Stage 1: Comic Identification + Context Gathering.

Usage:
    python -m stages.stage_1
    python -m stages.stage_1 "The death of Gwen Stacy"
    python -m stages.stage_1 --project existing_project_name
"""
import json
import sys
import textwrap
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL, get_project_dirs, PROJECTS_ROOT

from .agent import ScriptAgent, PhaseResult, PhaseDecision
from .storage import save_comic_context, save_conversation_log, slugify
from .ui import Colors, print_error, print_success, print_info, get_user_input


def _display_phase_result(result: PhaseResult):
    """Pretty-print a phase result in the terminal."""
    data = result.data or {}
    phase = result.phase

    print(f"\n  {Colors.BOLD}{Colors.CYAN}--- {phase.upper()} (attempt {result.attempt}/{result.max_attempts}) ---{Colors.END}")

    if phase == "plan":
        entities = data.get("entities", {})
        print_info("Characters", ", ".join(entities.get("characters", ["?"])))
        print_info("Publisher hint", entities.get("publisher_hint", "?"))
        print_info("Era hint", entities.get("era_hint") or "?")
        print_info("Story type", entities.get("story_type", "?"))
        queries = data.get("search_queries", [])
        if queries:
            print(f"  {Colors.BOLD}Search queries:{Colors.END}")
            for i, q in enumerate(queries, 1):
                print(f"    {i}. {q}")
        for a in data.get("ambiguities", []):
            print(f"  {Colors.YELLOW}? {a}{Colors.END}")

    elif phase == "search":
        print_info("Title", data.get("title", "?"))
        print_info("Series", f"{data.get('series', '?')} {data.get('issues', '')}".strip())
        print_info("Year", str(data.get("year", "?")))
        print_info("Writer", data.get("writer", "?"))
        print_info("Artist", data.get("artist", "?"))
        print_info("Publisher", data.get("publisher", "?"))
        print_info("Characters", ", ".join(data.get("characters", ["?"])))
        print_info("Confidence", data.get("confidence", "?"))
        batcave = data.get("batcave_url", "")
        if batcave:
            print_info("batcave_url", batcave)
        for a in data.get("ambiguities", []):
            print(f"  {Colors.YELLOW}? {a}{Colors.END}")

    elif phase == "wiki":
        print_info("Wiki URL", data.get("wiki_url", "(none)"))
        print_info("Plot length", f"{data.get('plot_length', 0)} chars")
        plot = data.get("wiki_plot", "")
        if plot:
            print(f"  {Colors.DIM}{plot[:300]}{'...' if len(plot) > 300 else ''}{Colors.END}")

    elif phase == "confirm":
        print_info("Title", data.get("title", "?"))
        print_info("Series", f"{data.get('series', '?')} {data.get('issues', '')}".strip())
        print_info("Year", str(data.get("year", "?")))
        print_info("Writer", data.get("writer", "?"))
        print_info("Publisher", data.get("publisher", "?"))
        print_info("Characters", ", ".join(data.get("characters", []) or ["?"]))
        print_info("batcave_url", data.get("batcave_url") or "(missing)")
        plot = data.get("plot_summary", "")
        if plot:
            print(f"  {Colors.DIM}Plot: {len(plot)} chars{Colors.END}")

    if result.raw_text:
        trimmed = result.raw_text[:200] + ("..." if len(result.raw_text) > 200 else "")
        print(f"  {Colors.DIM}LLM: {trimmed}{Colors.END}")


def _cli_phase_decision(result: PhaseResult) -> PhaseDecision:
    """Terminal-based approve/reject for a phase result."""
    _display_phase_result(result)

    print(f"\n  {Colors.BOLD}[1] Approve  [2] Revise{Colors.END}")
    choice = get_user_input("Choose [1/2]")

    if choice in ("2", "no", "n", "revise", "reject"):
        feedback = get_user_input("What should change?")
        return PhaseDecision(approved=False, feedback=feedback)

    return PhaseDecision(approved=True)


def main():
    parser = argparse.ArgumentParser(
        description="Stage 1: PanelNarrator — Comic Identification & Context Gathering",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Examples:
          python -m stages.stage_1
          python -m stages.stage_1 "The death of Gwen Stacy"
          python -m stages.stage_1 "Invincible vs Omni-Man"
          python -m stages.stage_1 --project death_of_gwen_stacy
        """),
    )
    parser.add_argument("prompt", nargs="*", help="Comic event description (optional)")
    parser.add_argument("--project", type=str, help="Resume an existing project")
    parser.add_argument("--mode", type=str, default="narrate_1_comic",
                        help="Pipeline mode (default: narrate_1_comic)")
    args = parser.parse_args()

    initial_prompt = " ".join(args.prompt) if args.prompt else None

    if args.project:
        ctx_path = PROJECTS_ROOT / args.project / "comic_context.json"
        if ctx_path.exists():
            print(f"  Project '{args.project}' already has comic_context.json.")
            choice = get_user_input("Regenerate? [y/N]")
            if choice.lower() not in ("y", "yes"):
                print("  Keeping existing context.")
                return

    if not initial_prompt:
        initial_prompt = get_user_input("Describe a comic book event or story")

    if not initial_prompt:
        print_error("No prompt provided.")
        sys.exit(1)

    agent = ScriptAgent(
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        model=OPENROUTER_MODEL,
        mode=args.mode,
    )

    def _log(msg: str):
        print(f"  {Colors.DIM}{msg}{Colors.END}")

    ctx = agent.run_interactive(
        initial_prompt=initial_prompt,
        on_phase_result=_cli_phase_decision,
        on_log=_log,
    )

    if not ctx or ctx.get("status") != "ready":
        print_error("Comic context generation failed.")
        sys.exit(1)

    if args.project:
        project_name = args.project
    elif initial_prompt:
        project_name = slugify(initial_prompt)
    else:
        project_name = slugify(ctx.get("title", "untitled_project"))

    print()
    from .tools.summarize_context import enrich_with_summary
    enrich_with_summary(ctx, progress=_log)
    ctx_path = save_comic_context(ctx, project_name, get_project_dirs)
    agent.save_session(get_project_dirs(project_name)["root"])

    print(f"\n{'=' * 70}")
    print_success("Stage 1 complete!")
    print_info("Project", project_name)
    print_info("Context", str(ctx_path))
    print_info("Title", ctx.get("title", "?"))
    print_info("Series", f"{ctx.get('series', '?')} {ctx.get('issues', '')}")
    print_info("batcave_url", ctx.get("batcave_url") or "(not found)")
    print_info("Plot text", f"{len(ctx.get('plot_summary', ''))} chars")
    print()
