"""
Pretty-print handlers for each conversation phase.
"""
from .ui import Colors, print_phase, print_info


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
                print(f"       {Colors.DIM}{brief}{Colors.END}")

    if data.get("needs_web_search"):
        print(f"\n  {Colors.DIM}🌐 Web search recommended: {data.get('search_reason', 'More info needed')}{Colors.END}")


def display_comic_context(ctx: dict):
    """Pretty-print the final comic_context before save."""
    print_phase("COMIC CONTEXT", "📘")

    print_info("Title", ctx.get("title", "?"))
    print_info("Series", f"{ctx.get('series', '?')} {ctx.get('issues', '')}".strip())
    print_info("Year", ctx.get("year", "?"))

    chars = ctx.get("characters", [])
    if chars:
        print_info("Characters", ", ".join(chars))

    batcave = ctx.get("batcave_url", "")
    if batcave:
        print_info("batcave_url", batcave)
    else:
        print(f"  {Colors.YELLOW}⚠️  batcave_url missing — set it manually before Stage 2{Colors.END}")

    wiki = ctx.get("wiki_url", "")
    if wiki:
        print_info("wiki_url", wiki)

    plot = ctx.get("plot_summary", "")
    if plot:
        print(f"\n  {Colors.BOLD}📖 Plot summary ({len(plot)} chars):{Colors.END}")
        preview = plot[:400] + ("..." if len(plot) > 400 else "")
        print(f"     {Colors.DIM}{preview}{Colors.END}")
    else:
        print(f"\n  {Colors.YELLOW}⚠️  No plot summary — downstream will rely on VLM page reading only{Colors.END}")
