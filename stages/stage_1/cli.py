"""
CLI entry point for Stage 1: Comic Identification + Context Gathering.

Usage:
    python -m stages.stage_1
    python -m stages.stage_1 "The death of Gwen Stacy"
    python -m stages.stage_1 --project existing_project_name
"""
import sys
import textwrap
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL, get_project_dirs, GDRIVE_BASE

from .agent import ScriptAgent
from .storage import save_comic_context, save_conversation_log, slugify
from .ui import print_error, print_success, print_info, get_user_input


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
    args = parser.parse_args()

    initial_prompt = " ".join(args.prompt) if args.prompt else None

    if args.project:
        ctx_path = GDRIVE_BASE / args.project / "comic_context.json"
        if ctx_path.exists():
            print(f"  📂 Project '{args.project}' already has comic_context.json.")
            choice = get_user_input("Regenerate? [y/N]")
            if choice.lower() not in ("y", "yes"):
                print("  Keeping existing context.")
                return

    agent = ScriptAgent(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL, model=OPENROUTER_MODEL)
    ctx = agent.run(initial_prompt)

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
    ctx_path = save_comic_context(ctx, project_name, get_project_dirs)
    save_conversation_log(agent, project_name, get_project_dirs)

    print(f"\n{'═' * 70}")
    print_success("Stage 1 complete!")
    print_info("Project", project_name)
    print_info("Context", ctx_path)
    print_info("Title", ctx.get("title", "?"))
    print_info("Series", f"{ctx.get('series', '?')} {ctx.get('issues', '')}")
    print_info("batcave_url", ctx.get("batcave_url") or "(not found — set manually before Stage 2)")
    print_info("Plot text", f"{len(ctx.get('plot_summary', ''))} chars")
    print()
