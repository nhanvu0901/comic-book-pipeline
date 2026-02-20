"""
CLI entry point for Stage 1: Script Generator.

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
from config import GLM_API_KEY, GLM_BASE_URL, GLM_MODEL, get_project_dirs, GDRIVE_BASE

from .agent import ScriptAgent
from .storage import save_script, save_conversation_log, slugify
from .ui import print_error, print_success, print_info, get_user_input, Colors


def main():
    parser = argparse.ArgumentParser(
        description="Stage 1: PanelNarrator — Interactive Comic Book Script Agent",
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
        script_path = GDRIVE_BASE / args.project / "script.json"
        if script_path.exists():
            print(f"  📂 Project '{args.project}' already has a script.")
            choice = get_user_input("Regenerate script? [y/N]")
            if choice.lower() not in ("y", "yes"):
                print("  Keeping existing script.")
                return

    agent = ScriptAgent(api_key=GLM_API_KEY, base_url=GLM_BASE_URL, model=GLM_MODEL)
    script = agent.run(initial_prompt)

    if not script or script.get("status") == "error":
        print_error("Script generation failed.")
        sys.exit(1)

    if args.project:
        project_name = args.project
    elif initial_prompt:
        project_name = slugify(initial_prompt)
    else:
        title = script.get("title", "untitled_project")
        project_name = slugify(title)

    print()
    script_path = save_script(script, project_name, get_project_dirs)
    save_conversation_log(agent, project_name, get_project_dirs)

    print(f"\n{'═' * 70}")
    print_success("Stage 1 complete!")
    print_info("Project", project_name)
    print_info("Script", script_path)
    print_info("Scenes", len(script.get("scenes", [])))

    total_words = sum(len(s.get("narration", "").split()) for s in script.get("scenes", []))
    print_info("Est. duration", f"~{round(total_words / 3)}s")

    print(f"\n  {Colors.BOLD}👉 Next step:{Colors.END}")
    print(f"     streamlit run stages/stage_2/...")
    print()
