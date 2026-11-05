"""python -m stages.mascot --project <slug> [--suggest] [--preview T] [--frame T] [--off]

Default: render final.mp4 from projects/<slug>/mascot.json (channel defaults when absent).
  --suggest     replace unlocked cues with auto suggestions, save mascot.json, then render
  --preview T   only render mascot/preview.mp4 around T seconds
  --frame T     only write the still frame at T and print its path
  --off         set enabled=false and restore the clean final.mp4
  --projects-root DIR   projects folder (default: the repo's config.PROJECTS_ROOT)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _projects_root(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    import config as repo_config      # the repo's top-level config.py, NOT stages/mascot/config.py
    root = getattr(repo_config, "PROJECTS_ROOT", None)
    if root is None:
        from .config import REPO_ROOT
        root = REPO_ROOT / "projects"
    return Path(root)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m stages.mascot", description=__doc__.split("\n\n")[0])
    p.add_argument("--project", required=True, help="project slug (folder under the projects root)")
    p.add_argument("--projects-root", default=None, help="projects folder (default: config.PROJECTS_ROOT)")
    p.add_argument("--suggest", action="store_true", help="replace unlocked cues with auto suggestions")
    p.add_argument("--preview", type=float, metavar="T", help="render mascot/preview.mp4 around T seconds")
    p.add_argument("--frame", type=float, metavar="T", help="write the clean still frame at T and print its path")
    p.add_argument("--off", action="store_true", help="disable the mascot and restore the clean final.mp4")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from . import render
    from .config import has_config, load_config, save_config

    project = _projects_root(args.projects_root) / args.project
    if not project.is_dir():
        print(f"project not found: {project}", file=sys.stderr)
        return 2

    if args.frame is not None:
        print(render.frame_at(project, args.frame))
        return 0

    cfg = load_config(project)
    if args.off:
        cfg.enabled = False
        render.render_final(project, cfg, log=print)
        print(f"mascot off: {project / 'final.mp4'}")
        return 0

    if args.suggest:
        from .kit import load_kit
        from .suggest import suggest_cues
        cfg.cues = suggest_cues(project, cfg, load_kit(cfg.kit))
        save_config(project, cfg)
    elif not has_config(project):
        print("no mascot.json: using the channel defaults (no cues, nothing will be drawn)")

    last = [-1.0]

    def progress(v: float) -> None:
        if v - last[0] >= 0.05 or v >= 1.0:
            last[0] = v
            print(f"\rprogress {v * 100:5.1f}%", end="" if v < 1.0 else "\n", flush=True)

    if args.preview is not None:
        out = render.render_preview(project, cfg, args.preview, progress=progress, log=print)
    else:
        out = render.render_final(project, cfg, progress=progress, log=print)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
