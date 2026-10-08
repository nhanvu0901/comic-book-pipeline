"""Capture the P0 byte-identity baseline FOR THIS PLATFORM by running a checkout of commit 6788212.

    git worktree add /tmp/p0 6788212          # or any checkout of 6788212
    python scripts/p0_baseline_capture.py --repo /tmp/p0 --out tests/fixtures/p0_baseline.json

It runs tests/p0_scenarios.py (copied unchanged from THIS tree) with the checkout's own stages/ on
sys.path — never HEAD's — and merges the hashes into the --out fixture under platform_key()
(sys.platform | ffmpeg | x264 build). Run it once per platform (Mac, the Windows server) with the ffmpeg
that platform renders with; tests/test_p0_baseline.py then compares HEAD (flag OFF) against the entry
for the platform it runs on, like with like."""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="checkout of the P0 baseline commit (6788212)")
    ap.add_argument("--out", required=True, help="fixture json to create/merge into")
    ap.add_argument("--expect-commit", default="6788212")
    args = ap.parse_args()
    repo = Path(args.repo).resolve()
    out = Path(args.out).resolve()          # before the chdir below: a relative --out is relative to HERE
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if not head.startswith(args.expect_commit):
        print(f"refusing: {repo} is at {head[:12]}, not {args.expect_commit} — a baseline must come from the baseline commit",
              file=sys.stderr)
        return 2
    dirty = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--", "stages", "config.py", "utils"],
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        print(f"refusing: the baseline checkout has local changes:\n{dirty}", file=sys.stderr)
        return 2
    os.chdir(repo)                       # scenario paths are cwd-relative, as in the tests
    sys.path.insert(0, str(repo))
    spec = importlib.util.spec_from_file_location("p0_scenarios", HERE / "tests" / "p0_scenarios.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    import stages.stage_5.pipeline as p
    assert Path(p.__file__).resolve().is_relative_to(repo), f"stages imported from {p.__file__}, not the checkout"
    key = mod.platform_key()
    values = mod.run_all(Path("projects"))
    doc = json.loads(out.read_text()) if out.exists() else {}
    doc.setdefault("platforms", {})[key] = {
        "commit": head, "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "scenarios": values,
    }
    doc["note"] = ("P0 byte-identity baseline, one entry per platform, each captured by running commit 6788212 "
                   "ON that platform (scripts/p0_baseline_capture.py) — never derived from HEAD.")
    out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: values}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
