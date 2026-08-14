"""Generate the background music bed for a project.

Two steps, both optional and both soft — a project with no bed renders narration-only,
exactly as it did before any of this existed:

  1. BRIEF   an LLM reads the finished narration and writes the ACE-Step tag list
             (genre + bpm + prompt). Master's genre pick in the Review Beats UI, if any,
             is binding; otherwise the model chooses. Saved to projects/<slug>/music.json.
  2. BED     ACE-Step generates one track of exactly the video's length, into
             projects/<slug>/bgm.wav, where stage_5._resolve_bgm already looks for it.

Run AFTER Stage 4, not after Stage 3: the length has to be the RENDERED audio duration.
On cap-shield-broken the Stage-3 estimate and the real audio differ by 11 seconds (50.3 vs
61.2 — see _WORDS_PER_SEC vs POST_ATEMPO), and generating to the estimate would leave the
tail of the video silent.

CLI:
  python -m stages.music_bed --project X                # brief + generate
  python -m stages.music_bed --project X --brief-only   # just choose the genre
  python -m stages.music_bed --project X --force        # regenerate over an existing bed
"""
from __future__ import annotations

import argparse
import json
import os
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from config import (ACESTEP_GUIDANCE, ACESTEP_SEED, ACESTEP_STEPS, MUSIC_BRIEF_MODELS,
                    MUSIC_GENRE, PROJECTS_ROOT)

_WORKER = Path(__file__).resolve().parent / "_acestep_worker.py"


def _venv_python(venv_dir=None) -> Path:
    """The ACE-Step venv's interpreter. Same probe as chatterbox_tts._venv_python — POSIX
    lays it out as bin/python, Windows as Scripts/python.exe."""
    d = Path(venv_dir) if venv_dir else Path(
        os.getenv("ACESTEP_VENV", _REPO_ROOT / ".venv-acestep"))
    for rel in (("bin", "python"), ("Scripts", "python.exe")):
        p = d.joinpath(*rel)
        if p.exists():
            return p
    return d / "bin" / "python"


def available() -> bool:
    return _venv_python().exists()


# ─── 1. the brief ────────────────────────────────────────────────────────────

_BRIEF_SYSTEM = """You are a music supervisor scoring a YouTube Short. You write the prompt \
that goes STRAIGHT into a text-to-music generator — nobody edits it afterwards.

THE GENERATOR (ACE-Step, open-weight):
- Prompt = a COMMA-SEPARATED TAG LIST, never a sentence. Tags are genre, instruments, mood, \
production adjectives, and a bpm tag. Example shape: "dark trap, 808 sub bass, rolling \
hi-hats, minor strings, brooding, instrumental, 140 bpm"
- It returns ONE finished mixed track. No stems.
- It WILL add singing unless the tag list rules it out.
- The bpm tag is a hint, not a lock, so state it clearly and keep the tag list consistent \
with that tempo.

THE VIDEO: a narrated comic-book Short. The music plays UNDER a continuous voice-over that \
barely pauses, so it is a score, not the lead — but the genre is your call.

Return STRICT JSON ONLY:
{"genre": "<short genre name>",
 "bpm": <integer>,
 "prompt": "<the tag list, ready to send verbatim, including the bpm tag and an \
instrumental/no-vocals tag>",
 "reasoning": "<2-3 sentences: why this genre and tempo for THIS story>"}"""


def _script_of(narration: dict) -> str:
    return "\n".join(str(s.get("text", "")).strip()
                     for s in (narration.get("scenes") or []) if s.get("text"))


def _load_json(path: Path) -> dict:
    """Never raise on a project file. A malformed music.json or narration.json must degrade
    to "no bed", the same as an absent one — this module's whole contract is that nothing it
    does can fail a render."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def audio_seconds(root: Path) -> float:
    """The video's real length. Falls back to Stage 3's estimate before TTS has run."""
    wav = root / "audio.wav"
    probe = shutil.which("ffprobe")
    if wav.exists() and probe:
        try:
            out = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration",
                                  "-of", "default=nw=1:nk=1", str(wav)],
                                 capture_output=True, text=True).stdout.strip()
            return float(out)
        except (OSError, ValueError):
            pass          # unprobeable → fall through to the Stage 3 estimate
    return float(_load_json(root / "narration.json").get("estimated_duration_seconds") or 0.0)


def build_brief(project: str, *, log=print) -> dict | None:
    """Ask the LLM for genre + bpm + prompt; merge with Master's UI choice; persist.

    Returns None (and writes nothing) on any LLM failure — no music is a valid render.
    """
    root = PROJECTS_ROOT / project
    narration = _load_json(root / "narration.json")
    if not narration.get("scenes"):
        log("[music] no narration to read — run Stage 3 first")
        return None
    mpath = root / "music.json"
    existing = _load_json(mpath)

    length = audio_seconds(root)
    chosen = str(existing.get("genre") or "").strip()
    # A genre present in music.json was PICKED — the file only exists because the Review Beats
    # dropdown wrote it — so it binds even when it happens to equal MUSIC_GENRE. Comparing
    # against the default was wrong twice over: "minimal dark cinematic" is both the dropdown's
    # initial value AND the one register measured to survive under narration, so choosing the
    # best option was the one case that silently stopped binding.
    steer = (f"\n\nREQUIRED GENRE: {chosen}. Write the tag list for that genre — do not "
             f"substitute another." if chosen else "")
    user = (f"Mode: {narration.get('mode')}\nTitle: {narration.get('title', '')}\n"
            f"Exact length: {length:.0f} seconds\n\nFULL NARRATION:\n"
            f"{_script_of(narration)}{steer}")

    try:
        from .stage_3._llm import _client, _call_with_deadline
        client = _client()
    except Exception as exc:  # noqa: BLE001
        log(f"[music] LLM client unavailable ({exc}) — no bed")
        return None

    brief = None
    for model in MUSIC_BRIEF_MODELS:
        try:
            raw = _call_with_deadline(client, model, _BRIEF_SYSTEM, user, 900)
        except Exception as exc:  # noqa: BLE001
            log(f"[music]   {model} failed: {exc}")
            continue
        m = re.search(r"\{.*\}", raw or "", re.S)
        if not m:
            log(f"[music]   {model}: unparseable → next")
            continue
        try:
            brief = json.loads(m.group(0))
        except json.JSONDecodeError:
            log(f"[music]   {model}: bad JSON → next")
            continue
        if brief.get("prompt"):
            log(f"[music]   brief via {model}")
            break
        brief = None
    if not brief:
        log("[music] no usable brief — no bed")
        return None

    prompt = str(brief["prompt"]).strip()
    # Guard the one failure that ruins a render silently: without an instrumental tag the
    # generator sings, and a vocal track under a voice-over is unusable.
    if "instrumental" not in prompt.lower() and "no vocals" not in prompt.lower():
        prompt += ", instrumental, no vocals"
        log("[music]   added missing instrumental tag")

    # Master's pick wins the label too. Letting the model's `genre` overwrite it meant a
    # dropdown choice could come back renamed, so the UI would show something Master never set.
    doc = {**existing, "genre": chosen or str(brief.get("genre") or MUSIC_GENRE),
           "bpm": brief.get("bpm"), "prompt": prompt,
           "reasoning": str(brief.get("reasoning") or ""),
           "length_seconds": round(length, 2)}
    mpath.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"[music] {doc['genre']} @ {doc['bpm']} bpm, {length:.0f}s")
    log(f"[music] prompt: {prompt}")
    return doc


# ─── 2. the bed ──────────────────────────────────────────────────────────────

def generate_bed(project: str, *, force: bool = False, log=print) -> Path | None:
    """Run ACE-Step for this project's brief → projects/<slug>/bgm.wav. None on any failure."""
    root = PROJECTS_ROOT / project
    out = root / "bgm.wav"
    stamp = root / "bgm.prompt.sha1"

    doc = _load_json(root / "music.json")
    prompt = str(doc.get("prompt") or "").strip()
    if not prompt:
        log("[music] no usable music.json prompt — run the brief first")
        return None
    # GENRE is in the hash as well as the prompt. The Review Beats dropdown writes only
    # `genre` and leaves the old `prompt` in place, so hashing the prompt alone meant picking
    # a new genre did NOT invalidate the bed — and if the brief call then failed (the model
    # chain is free-tier and config.py already warns it returns empty), the run happily logged
    # "reusing bgm.wav (same brief)" and shipped the previous genre's music.
    want = hashlib.sha1(
        f"{doc.get('genre') or ''}|{prompt}|{ACESTEP_STEPS}|{ACESTEP_GUIDANCE}|{ACESTEP_SEED}"
        .encode("utf-8")).hexdigest()

    # Reuse only when the bed was built from THIS brief. A bare exists() check meant changing
    # the genre in the review UI and re-running quietly shipped the previous genre's bed — the
    # dropdown looked like it worked and the video did not change. Keyed on the generation
    # inputs, so a prompt, step-count, guidance or seed change all invalidate it.
    if out.exists() and not force:
        if stamp.exists() and stamp.read_text(encoding="utf-8").strip() == want:
            log(f"[music] reusing {out.name} (same brief)")
            return out
        log("[music] brief changed since bgm.wav was generated — regenerating")
    # +1s so looping/tail handling never runs the bed short of the video.
    seconds = float(doc.get("length_seconds") or audio_seconds(root)) + 1.0
    if seconds < 2:
        log("[music] project length unknown — no bed")
        return None

    py = _venv_python()
    if not py.exists():
        log(f"[music] ACE-Step venv missing at {py.parent.parent} — no bed")
        return None

    job = Path(tempfile.mkdtemp(prefix="acestep_")) / "job.json"
    job.write_text(json.dumps({
        "prompt": prompt, "seconds": round(seconds, 2), "out": str(out),
        "steps": ACESTEP_STEPS, "guidance": ACESTEP_GUIDANCE, "seed": ACESTEP_SEED,
    }), encoding="utf-8")

    log(f"[music] generating {seconds:.0f}s bed (~{seconds * 2.4 / 60:.1f} min on CPU)…")
    # Clear the old bed and its stamp BEFORE generating. Otherwise a worker that dies without
    # printing (OOM, a DLL that fails to load — see _acestep_worker's own note on torchcodec)
    # leaves the previous genre's wav sitting there, `out.exists()` reads as success, and it
    # gets stamped with the NEW brief — wrong music, locked in, reported as "reusing".
    out.unlink(missing_ok=True)
    stamp.unlink(missing_ok=True)

    res = subprocess.run([str(py), str(_WORKER), str(job)], capture_output=True, text=True)
    produced = False
    for line in (res.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("error"):
            log(f"[music] generation failed: {msg['error']} — no bed")
            out.unlink(missing_ok=True)
            return None
        if msg.get("ok"):
            produced = True
            log(f"[music] bed written in {msg.get('elapsed')}s → {out.name}")
    # BOTH conditions: the worker has to have SAID it finished and the wav has to be there.
    # Trusting the file alone let a crashed run inherit whatever was on disk.
    if not (produced and out.exists()):
        log(f"[music] worker produced nothing (exit {res.returncode}) — no bed")
        out.unlink(missing_ok=True)
        return None
    # Stamp LAST, once the wav is really on disk: a stamp beside a missing or half-written bed
    # would make the next run trust a file that was never finished.
    stamp.write_text(want, encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m stages.music_bed",
                                 description="Write the music brief and generate the bed.")
    ap.add_argument("--project", required=True)
    ap.add_argument("--brief-only", action="store_true", help="Choose the genre, generate nothing.")
    ap.add_argument("--skip-brief", action="store_true", help="Reuse the existing music.json.")
    ap.add_argument("--force", action="store_true", help="Regenerate over an existing bgm.wav.")
    a = ap.parse_args(argv)

    if not a.skip_brief and build_brief(a.project) is None and not (
            PROJECTS_ROOT / a.project / "music.json").exists():
        return 1
    if a.brief_only:
        return 0
    return 0 if generate_bed(a.project, force=a.force) else 1


if __name__ == "__main__":
    sys.exit(main())
