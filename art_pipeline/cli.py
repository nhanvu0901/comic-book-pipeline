"""CLI: python3 -m art_pipeline <command> <project> [options]

  fetch   <project> --ids 436535 [--mode painting_deep_dive] [--theme ""] [--length short|longform]
  regions <project> [--force]
  ground  <project>
  outline <project> [--mode KEY] [--force]
  narrate <project> [--mode KEY]
  hunt    <project> [--force]
  tts     <project> [--force]
  video   <project> [--force] [--no-music]
  all     <project> --ids 436535 [--mode KEY] [--length short|longform]
          (chain order: fetch → regions → ground → [outline] → narrate → hunt → tts → video)
"""
import argparse


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="art_pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name):
        p = sub.add_parser(name)
        p.add_argument("project")
        return p

    p = add("fetch")
    p.add_argument("--ids", required=True, help="comma-separated Met objectIDs")
    p.add_argument("--mode", default="painting_deep_dive")
    p.add_argument("--theme", default="")
    p.add_argument("--length", default="short", choices=["short", "longform"])
    p = add("regions"); p.add_argument("--force", action="store_true")
    add("ground")
    p = add("outline"); p.add_argument("--mode", default=None)
    p.add_argument("--force", action="store_true")
    p = add("narrate"); p.add_argument("--mode", default=None)
    p = add("hunt"); p.add_argument("--force", action="store_true")
    p = add("tts"); p.add_argument("--force", action="store_true")
    p = add("video"); p.add_argument("--force", action="store_true")
    p.add_argument("--no-music", action="store_true")
    p = add("all")
    p.add_argument("--ids", required=True)
    p.add_argument("--mode", default="painting_deep_dive")
    p.add_argument("--theme", default="")
    p.add_argument("--length", default="short", choices=["short", "longform"])

    a = ap.parse_args(argv)

    def _length(project: str) -> str:
        import json as _json
        from .config import get_art_project_path
        sel = get_art_project_path(project) / "selection.json"
        if sel.exists():
            return _json.loads(sel.read_text()).get("length", "short")
        return "short"

    if a.cmd in ("fetch", "all"):
        from .fetch import fetch_artworks
        ids = [int(x) for x in a.ids.split(",") if x.strip()]
        fetch_artworks(a.project, ids, mode=a.mode, theme=a.theme,
                       length=getattr(a, "length", "short"))
    if a.cmd in ("regions", "all"):
        from .regions import process_artworks
        process_artworks(a.project, force=getattr(a, "force", False))
    if a.cmd in ("ground", "all"):
        from .grounding import build_art_context
        build_art_context(a.project)
    if a.cmd == "outline":
        from .outline import write_outline
        write_outline(a.project, getattr(a, "mode", None),
                      force=getattr(a, "force", False))
    if a.cmd in ("narrate", "all"):
        if _length(a.project) == "longform":
            from .outline import write_outline
            from .narrate_longform import write_longform_narration
            write_outline(a.project, getattr(a, "mode", None))  # reuses if exists
            write_longform_narration(a.project)
        else:
            from .narrate import write_narration
            write_narration(a.project, getattr(a, "mode", None))
    if a.cmd in ("hunt", "all"):
        from .hunt import hunt_visuals
        hunt_visuals(a.project, force=getattr(a, "force", False))
    if a.cmd in ("tts", "all"):
        if _length(a.project) == "longform":
            from .longform_tts import synthesize_longform
            synthesize_longform(a.project, force=getattr(a, "force", False))
        else:
            from .tts import synthesize_art
            synthesize_art(a.project, force=getattr(a, "force", False))
    if a.cmd in ("video", "all"):
        from .video import assemble_art
        assemble_art(a.project, force=getattr(a, "force", False),
                     enable_music=not getattr(a, "no_music", False))
    return 0
