#!/usr/bin/env python3
"""
Comic Video Pipeline — Main Orchestrator

Runs all local stages in sequence and guides you through the Colab steps.

Usage:
    python run_pipeline.py "The death of Gwen Stacy in Amazing Spider-Man"
    python run_pipeline.py "Invincible vs Omni-Man" --bgm epic_music.mp3
    python run_pipeline.py --resume my_project_name
"""
import argparse
import json
import os
import sys
import re
from pathlib import Path

from config import GDRIVE_BASE, get_project_dirs


class Colors:
    DIM = "\033[2m"
    END = "\033[0m"


def slugify(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[\s]+", "_", slug)
    return slug[:60]


def check_prerequisites():
    """Check that all dependencies are available."""
    print("🔍 Checking prerequisites...")
    
    issues = []
    
    # Check Google Drive path
    gdrive_env = os.environ.get("GDRIVE_BASE", "")
    if gdrive_env and not GDRIVE_BASE.exists():
        issues.append(
            f"GDRIVE_BASE is set but path not found: {GDRIVE_BASE}\n"
            "  → Install Google Drive for Desktop or comment out GDRIVE_BASE in .env to use local storage"
        )
    elif not gdrive_env:
        print(f"  {Colors.DIM}ℹ️  No GDRIVE_BASE set — using local: {GDRIVE_BASE}{Colors.END}")
    
    # Check API key
    from config import GLM_API_KEY
    if not GLM_API_KEY:
        issues.append("GLM_API_KEY not set in .env file")
    
    # Check Python packages
    required = ["anthropic", "streamlit", "moviepy", "PIL", "pydub", "ddgs"]
    for pkg in required:
        try:
            __import__(pkg)
        except ImportError:
            issues.append(f"Missing package: {pkg} → pip install -r requirements.txt")
    
    if issues:
        print("\n❌ Issues found:")
        for issue in issues:
            print(f"   • {issue}")
        print("\nPlease fix these issues before running the pipeline.")
        return False
    
    print("   ✅ All prerequisites met\n")
    return True


def run_stage1(prompt: str, project_name: str) -> dict | None:
    """Run Stage 1: Interactive Script Generation Agent."""
    from stages.stage1_script_generator import ScriptAgent, save_script, save_conversation_log

    agent = ScriptAgent()
    script = agent.run(prompt)

    if not script or script.get("status") == "error":
        print(f"\n❌ Script generation failed.")
        return None

    save_script(script, project_name)
    save_conversation_log(agent, project_name)

    return script


def run_stage2_prompt(project_name: str):
    """Prompt user to run Stage 2."""
    print("\n" + "=" * 70)
    print("STAGE 2: Image Selection")
    print("=" * 70)
    print(f"\n🖼️  Open the Image Picker in a new terminal:")
    print(f"   streamlit run stages/stage2_image_picker.py")
    print(f"\n   Select project: {project_name}")
    print(f"   Pick the best comic panel for each scene.")
    print(f"\n   Press ENTER when done...")
    input()


def run_colab_prompt(project_name: str):
    """Guide user through Colab steps."""
    print("\n" + "=" * 70)
    print("STAGE 3 & 4: TTS + SRT (Google Colab)")
    print("=" * 70)
    print(f"""
🎙️  Now switch to Google Colab:

1. Open: colab/tts_and_srt.ipynb in Google Colab
2. Set PROJECT_NAME = '{project_name}'
3. Upload your voice sample WAV file
4. Run all cells
5. Wait for audio files to sync back to Google Drive

📁 Files will appear in:
   {GDRIVE_BASE / project_name / 'audio'}/

Press ENTER when the Colab notebook has finished and files are synced...
""")
    input()
    
    # Verify audio files exist
    audio_dir = GDRIVE_BASE / project_name / "audio"
    audio_files = list(audio_dir.glob("scene_*.wav")) if audio_dir.exists() else []
    
    if audio_files:
        print(f"   ✅ Found {len(audio_files)} audio files")
    else:
        print(f"   ⚠️  No audio files found in {audio_dir}")
        print(f"   The video will be created without narration.")
        print(f"   You can add audio later and re-run Stage 5.")


def run_stage5(project_name: str, bgm_path: str | None = None):
    """Run Stage 5: Video Assembly."""
    print("\n" + "=" * 70)
    print("STAGE 5: Video Assembly")
    print("=" * 70)
    
    from stages.stage5_video_assembler import assemble_video
    
    output = assemble_video(
        project_name=project_name,
        bgm_path=bgm_path,
        include_subtitles=True,
    )
    
    return output


def main():
    parser = argparse.ArgumentParser(
        description="Comic Book Video Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_pipeline.py "The death of Gwen Stacy"
  python run_pipeline.py "Batman Knightfall" --bgm epic.mp3
  python run_pipeline.py --resume death_of_gwen_stacy
  python run_pipeline.py --resume death_of_gwen_stacy --stage 5
        """
    )
    parser.add_argument("prompt", nargs="?", help="Comic event/story description")
    parser.add_argument("--resume", type=str, help="Resume existing project by name")
    parser.add_argument("--bgm", type=str, help="Path to background music file")
    parser.add_argument("--stage", type=int, help="Start from specific stage (2, 5)")
    args = parser.parse_args()

    print("\n" + "🦸 " * 20)
    print("  COMIC BOOK VIDEO PIPELINE")
    print("🦸 " * 20 + "\n")

    if not check_prerequisites():
        sys.exit(1)

    # Determine project name
    if args.resume:
        project_name = args.resume
        script_path = GDRIVE_BASE / project_name / "script.json"
        if not script_path.exists():
            print(f"❌ Project not found: {project_name}")
            print(f"   Expected: {script_path}")
            sys.exit(1)
        with open(script_path) as f:
            script = json.load(f)
        print(f"📂 Resuming project: {project_name}")
        print(f"   Title: {script.get('title', 'N/A')}\n")
    elif args.prompt:
        project_name = slugify(args.prompt)
        script = None
    else:
        parser.print_help()
        sys.exit(1)

    start_stage = args.stage or (1 if not args.resume else 2)

    # ─── Stage 1 ────────────────────────────────────────────────────────────
    if start_stage <= 1 and script is None:
        script = run_stage1(args.prompt, project_name)
        if script is None:
            sys.exit(1)

    # ─── Stage 2 ────────────────────────────────────────────────────────────
    if start_stage <= 2:
        run_stage2_prompt(project_name)

    # ─── Stage 3 & 4 ───────────────────────────────────────────────────────
    if start_stage <= 4:
        run_colab_prompt(project_name)

    # ─── Stage 5 ────────────────────────────────────────────────────────────
    if start_stage <= 5:
        output = run_stage5(project_name, bgm_path=args.bgm)

    # ─── Done ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("🎉 PIPELINE COMPLETE!")
    print("=" * 70)
    
    dirs = get_project_dirs(project_name)
    print(f"\n📁 Project: {dirs['root']}")
    print(f"📹 Video:   {dirs['output'] / 'final_video.mp4'}")
    print(f"📝 SRT:     {dirs['output'] / 'subtitles.srt'}")
    print(f"\n💡 To re-render with different BGM:")
    print(f'   python run_pipeline.py --resume "{project_name}" --stage 5 --bgm your_music.mp3')


if __name__ == "__main__":
    main()
