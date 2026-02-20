"""
Pretty-print handlers for each conversation phase.
"""
import textwrap
from .ui import Colors, print_phase, print_info, print_agent


def display_analysis(data: dict):
    """Pretty-print the analysis phase output."""
    print_phase("ANALYSIS", "🔍")

    parsed = data.get("parsed_input", {})
    print_info("Event identified", parsed.get("event_or_story", "Unknown"))
    print_info("Characters", ", ".join(parsed.get("characters_identified", ["?"])))
    print_info("Publisher", parsed.get("publisher_guess", "Unknown"))
    print_info("Series", parsed.get("series_guess", "Unknown"))
    print_info("Era", parsed.get("era_guess", "Unknown"))

    confidence = parsed.get("confidence", "low")
    conf_color = (
        Colors.GREEN if confidence == "high"
        else Colors.YELLOW if confidence == "medium"
        else Colors.RED
    )
    print(f"  {Colors.BOLD}Confidence:{Colors.END} {conf_color}{confidence.upper()}{Colors.END}")

    ambiguities = data.get("ambiguities", [])
    if ambiguities:
        print(f"\n  {Colors.YELLOW}⚠️  Ambiguities detected:{Colors.END}")
        for a in ambiguities:
            print(f"     • {a}")

    matches = data.get("potential_matches", [])
    if matches:
        print(f"\n  {Colors.BOLD}📚 Potential matches:{Colors.END}")
        for i, m in enumerate(matches, 1):
            title = m.get("title", "?")
            series = m.get("series", "?")
            issues = m.get("issues", "?")
            year = m.get("year", "?")
            writer = m.get("writer", "?")
            brief = m.get("brief", "")
            print(f"\n  {Colors.BOLD}  [{i}] {title}{Colors.END}")
            print(f"       {series} {issues} ({year})")
            print(f"       Writer: {writer}")
            if brief:
                wrapped = textwrap.fill(brief, width=55, initial_indent="       ", subsequent_indent="       ")
                print(f"{Colors.DIM}{wrapped}{Colors.END}")

    if data.get("needs_web_search"):
        print(f"\n  {Colors.DIM}🌐 Web search recommended: {data.get('search_reason', 'More info needed')}{Colors.END}")


def display_confirmation(data: dict):
    """Pretty-print the confirmation/outline phase output."""
    print_phase("STORY OUTLINE", "📋")

    source = data.get("confirmed_source", {})
    print_info("Title", source.get("title", "?"))
    print_info("Series", f"{source.get('series', '?')} {source.get('issues', '')}")
    print_info("Year", source.get("year", "?"))
    print_info("Writer", source.get("writer", "?"))
    print_info("Artist", source.get("artist", "?"))
    print_info("Era", source.get("era", "?"))

    summary = data.get("story_summary", "")
    if summary:
        print(f"\n  {Colors.BOLD}📖 Summary:{Colors.END}")
        wrapped = textwrap.fill(summary, width=64, initial_indent="     ", subsequent_indent="     ")
        print(wrapped)

    outline = data.get("scene_outline", [])
    if outline:
        print(f"\n  {Colors.BOLD}🎬 Scene Breakdown ({len(outline)} scenes):{Colors.END}")
        for s in outline:
            sid = s.get("scene_id", "?")
            beat = s.get("beat", "?")
            dur = s.get("estimated_seconds", "?")
            print(f"     {Colors.BOLD}[{sid:>2}]{Colors.END} {beat} {Colors.DIM}({dur}s){Colors.END}")

    total = data.get("estimated_duration_seconds", "?")
    tone = data.get("tone", "?")
    style = data.get("narrator_style", "?")

    print(f"\n  {Colors.BOLD}⏱️  Estimated duration:{Colors.END} {total}s")
    print(f"  {Colors.BOLD}🎭 Tone:{Colors.END} {tone}")
    print(f"  {Colors.BOLD}🎙️  Narrator style:{Colors.END} {style}")

    msg = data.get("message_to_user", "")
    if msg:
        print()
        print_agent(msg)


def display_script_summary(data: dict):
    """Pretty-print a summary of the generated script."""
    print_phase("SCRIPT GENERATED", "✅")

    print_info("Title", data.get("title", "?"))
    source = data.get("comic_source", {})
    print_info("Source", f"{source.get('series', '?')} {source.get('issues', '')} ({source.get('year', '?')})")
    print_info("Writer/Artist", f"{source.get('writer', '?')} / {source.get('artist', '?')}")

    scenes = data.get("scenes", [])
    print(f"\n  {Colors.BOLD}🎬 {len(scenes)} Scenes:{Colors.END}")

    total_words = 0
    for s in scenes:
        sid = s.get("scene_id", "?")
        narration = s.get("narration", "")
        effect = s.get("effect", "?")
        mood = s.get("mood", "?")
        words = len(narration.split())
        total_words += words
        duration_est = round(words / 3.0, 1)

        print(f"\n     {Colors.BOLD}Scene {sid}{Colors.END} [{effect}] [{mood}]")
        wrapped = textwrap.fill(
            f'"{narration}"',
            width=60,
            initial_indent="       ",
            subsequent_indent="       ",
        )
        print(f"{Colors.DIM}{wrapped}{Colors.END}")
        print(f"       {Colors.DIM}~{duration_est}s ({words} words){Colors.END}")

    total_dur = round(total_words / 3.0)
    print(f"\n  {Colors.BOLD}📊 Total:{Colors.END} {len(scenes)} scenes, ~{total_words} words, ~{total_dur}s narration")
