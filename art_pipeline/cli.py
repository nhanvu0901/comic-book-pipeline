"""CLI: python3 -m art_pipeline <command> <project> [options]

  fetch   <project> --ids 436535 [--mode painting_deep_dive] [--theme ""]
  regions <project> [--force]
  ground  <project>
  narrate <project> [--mode KEY]
  visuals <project> [--force]
  tts     <project> [--force]
  video   <project> [--force] [--no-music]
  all     <project> --ids 436535 [--mode KEY]
          (chain order: fetch → regions → ground → narrate → visuals → tts → video)
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
    p = add("regions"); p.add_argument("--force", action="store_true")
    add("ground")
    p = add("narrate"); p.add_argument("--mode", default=None)
    p = add("visuals"); p.add_argument("--force", action="store_true")
    p = add("tts"); p.add_argument("--force", action="store_true")
    p = add("video"); p.add_argument("--force", action="store_true")
    p.add_argument("--no-music", action="store_true")
    p = add("all")
    p.add_argument("--ids", required=True)
    p.add_argument("--mode", default="painting_deep_dive")
    p.add_argument("--theme", default="")

    a = ap.parse_args(argv)

    if a.cmd in ("fetch", "all"):
        from .fetch import fetch_artworks
        ids = [int(x) for x in a.ids.split(",") if x.strip()]
        fetch_artworks(a.project, ids, mode=a.mode, theme=a.theme)
    if a.cmd in ("regions", "all"):
        from .regions import process_artworks
        process_artworks(a.project, force=getattr(a, "force", False))
    if a.cmd in ("ground", "all"):
        from .grounding import build_art_context
        build_art_context(a.project)
    if a.cmd in ("narrate", "all"):
        from .narrate import write_narration
        write_narration(a.project, getattr(a, "mode", None))
    if a.cmd in ("visuals", "all"):
        from .visuals import enrich_visuals
        enrich_visuals(a.project, force=getattr(a, "force", False))
    if a.cmd in ("tts", "all"):
        from .tts import synthesize_art
        synthesize_art(a.project, force=getattr(a, "force", False))
    if a.cmd in ("video", "all"):
        from .video import assemble_art
        assemble_art(a.project, force=getattr(a, "force", False),
                     enable_music=not getattr(a, "no_music", False))
    return 0
