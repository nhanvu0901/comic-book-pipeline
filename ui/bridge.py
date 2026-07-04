"""
Async wrappers around the synchronous stage pipelines so the UI can run
them off the event loop via page.run_task().
"""
import asyncio
import json
import queue
import re
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from config import PROJECTS_ROOT


_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')

def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


async def run_blocking(fn: Callable[..., Any], *args, **kwargs) -> Any:
    """Run a blocking callable in a worker thread."""
    return await asyncio.to_thread(fn, *args, **kwargs)


# ─── Phase approval bridge (new interactive agent flow) ────────────────────

@dataclass
class PhaseApprovalBridge:
    """
    Thread-safe channel for the per-phase approve/reject loop.

    The agent worker thread calls submit_result() → blocks until the UI
    calls approve() or reject(feedback). Tokens stream via on_token callback.
    """
    on_phase_result: Callable | None = None
    on_token: Callable[[str], None] | None = None
    on_log: Callable[[str], None] | None = None

    def __post_init__(self):
        self._decision_q: queue.Queue[tuple[bool, str]] = queue.Queue()
        self._cancelled = False

    def submit_result(self, phase_result) -> tuple[bool, str]:
        """Called from worker thread. Blocks until UI decides."""
        if self._cancelled:
            return False, ""
        if self.on_phase_result:
            try:
                self.on_phase_result(phase_result)
            except Exception:
                pass
        try:
            return self._decision_q.get(timeout=600)
        except queue.Empty:
            return False, ""

    def approve(self) -> None:
        self._decision_q.put((True, ""))

    def reject(self, feedback: str) -> None:
        self._decision_q.put((False, feedback))

    def cancel(self) -> None:
        self._cancelled = True
        try:
            self._decision_q.put_nowait((False, ""))
        except queue.Full:
            pass

    def log(self, msg: str) -> None:
        if self.on_log:
            self.on_log(msg)

    def token(self, t: str) -> None:
        if self.on_token:
            self.on_token(t)


# ─── Stage 1 ────────────────────────────────────────────────────────────────

def run_stage_1(
    prompt: str,
    bridge: PhaseApprovalBridge,
    mode: str = "narrate_1_comic",
) -> tuple[dict, str]:
    """
    Invoke Stage 1 with interactive per-phase approval.
    Returns (comic_context, project_name).
    """
    from stages.stage_1.agent import ScriptAgent, PhaseResult, PhaseDecision
    from stages.stage_1.storage import save_comic_context, slugify
    from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL, get_project_dirs

    def _on_phase_result(result: PhaseResult) -> PhaseDecision:
        approved, feedback = bridge.submit_result(result)
        return PhaseDecision(approved=approved, feedback=feedback)

    agent = ScriptAgent(
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        model=OPENROUTER_MODEL,
        mode=mode,
    )
    ctx = agent.run_interactive(
        initial_prompt=prompt,
        on_phase_result=_on_phase_result,
        on_token=bridge.token,
        on_log=bridge.log,
    )

    if not ctx:
        raise RuntimeError("Stage 1 returned no comic_context (agent aborted).")

    project_name = slugify(prompt or ctx.get("title", "untitled_project"))
    from stages.stage_1.tools.summarize_context import enrich_with_summary
    enrich_with_summary(ctx, progress=bridge.log)
    save_comic_context(ctx, project_name, get_project_dirs)
    agent.save_session(get_project_dirs(project_name)["root"])

    return ctx, project_name


# ─── Stage 2: Download ─────────────────────────────────────────────────────

def run_stage_download(project_name: str, log: Callable[[str], None]) -> list[dict]:
    from stages.stage_2.download import download_comic
    return download_comic(project_name, progress=log)


def run_stage_download_from_url(
    project_name: str,
    raw_input: str,
    issues: str,
    enrich: bool,
    log: Callable[[str], None],
) -> list[dict]:
    """URL-direct download: skip Stage 1. raw_input can be either a series URL
    or one-or-more reader URLs (whitespace/newline/comma separated)."""
    from stages.stage_2.url_mode import (
        classify_url, download_from_readers, download_from_series,
    )
    from stages.stage_2.download import load_manifest

    tokens = [t.strip() for t in raw_input.replace(",", "\n").split() if t.strip()]
    if not tokens:
        raise ValueError("No URL provided")

    kinds = {classify_url(t) for t in tokens}
    if len(tokens) == 1 and "series" in kinds:
        download_from_series(project_name, tokens[0], issues, enrich=enrich, progress=log)
    elif kinds == {"reader"}:
        download_from_readers(project_name, tokens, enrich=enrich, progress=log)
    else:
        raise ValueError(
            f"Mixed or unknown URL forms: {tokens!r}. "
            "Use either a single series URL (with --issues), or N reader URLs."
        )
    return load_manifest(project_name)


def run_stage_download_saga(
    project_name: str,
    raw_input: str,
    max_issues: int,
    log: Callable[[str], None],
) -> list[dict]:
    """Crossover-saga download: builds a PER-ISSUE arc context (is_arc/issues[])
    then downloads. raw_input is either ONE series URL (→ download_saga, auto
    ≤max_issues) or N reader URLs (→ download_saga_from_readers, one issue each).
    N==1 collapses to today's single-comic shape."""
    from stages.stage_2.url_mode import (
        classify_url, download_saga, download_saga_from_readers,
    )
    from stages.stage_2.download import load_manifest

    tokens = [t.strip() for t in raw_input.replace(",", "\n").split() if t.strip()]
    if not tokens:
        raise ValueError("No URL provided")
    kinds = {classify_url(t) for t in tokens}
    if len(tokens) == 1 and "series" in kinds:
        download_saga(project_name, tokens[0], max_issues=max_issues, progress=log)
    elif kinds == {"reader"}:
        download_saga_from_readers(project_name, tokens, progress=log)
    else:
        raise ValueError(
            f"Saga mode needs ONE series URL or N reader URLs, got: {tokens!r}")
    return load_manifest(project_name)


def load_raw_pages(project_name: str) -> list[dict]:
    """Load the download manifest for thumbnail display."""
    from stages.stage_2.download import load_manifest
    return load_manifest(project_name)


# ─── Stage 3: Preprocess ──────────────────────────────────────────────────

def run_stage_2(project_name: str, log: Callable[[str], None]) -> list[dict]:
    from stages.stage_2 import preprocess_project
    return preprocess_project(project_name, progress=log, force_refresh=False)


def load_preprocessed(project_name: str) -> list[dict]:
    prep = PROJECTS_ROOT / project_name / "preprocessed"
    if not prep.exists():
        return []
    out: list[dict] = []
    for p in sorted(prep.glob("page_*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except json.JSONDecodeError:
            continue
    return out


# ─── Stage 3 ────────────────────────────────────────────────────────────────

def run_stage_3_propose(project_name: str, log: Callable[[str], None]) -> list[dict]:
    from stages.stage_3.pipeline import propose_modes
    proposals = propose_modes(project_name, n=3, progress=log)
    return [p.to_dict() for p in proposals]


def run_stage_3_write(
    project_name: str,
    mode: str,
    hook_hint: str,
    log: Callable[[str], None],
) -> dict:
    from stages.stage_3.pipeline import write_script, save_narration
    narration = write_script(project_name, mode, hook_hint=hook_hint, progress=log)
    save_narration(narration, project_name, progress=log)
    return narration.to_dict()


def load_narration(project_name: str) -> dict | None:
    p = PROJECTS_ROOT / project_name / "narration.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def save_narration_edits(project_name: str, narration: dict) -> None:
    p = PROJECTS_ROOT / project_name / "narration.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(narration, indent=2, ensure_ascii=False))


# ─── Stage 4 ────────────────────────────────────────────────────────────────

def run_stage_4(
    project_name: str,
    voice_id: str | None,
    model: str | None,
    log: Callable[[str], None],
) -> dict:
    from stages.stage_4.pipeline import synthesize_project

    # Route prints to log
    import builtins
    original = print
    builtins.print = lambda *a, **k: log(_strip_ansi(" ".join(str(x) for x in a)))
    try:
        result = synthesize_project(
            project_name,
            voice_id=voice_id or None,
            model=model or None,
            force=True,
        )
    finally:
        builtins.print = original

    return result.to_dict()


# ─── Stage 5 ────────────────────────────────────────────────────────────────

def run_stage_5(project_name: str, log: Callable[[str], None]) -> str:
    from stages.stage_5.pipeline import assemble_project

    import builtins
    original = print
    builtins.print = lambda *a, **k: log(_strip_ansi(" ".join(str(x) for x in a)))
    try:
        final = assemble_project(project_name, force=True)
    finally:
        builtins.print = original
    return str(final)


# ─── Stage 6: Review & Edit (storyboard) ───────────────────────────────────

THUMB_WIDTH = 420


def _pages_by_number(project_name: str) -> dict[int, dict]:
    """Preprocessed pages keyed by page_number."""
    out: dict[int, dict] = {}
    for page in load_preprocessed(project_name):
        pn = int(page.get("page_number", 0) or 0)
        if pn:
            out[pn] = page
    return out


def page_numbers(project_name: str) -> list[int]:
    """Sorted story page numbers from preprocessed/."""
    return sorted(_pages_by_number(project_name))


def _shots(project_name: str) -> list[dict]:
    p = PROJECTS_ROOT / project_name / "shots.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return []


def _first_shot_for_scene(project_name: str, scene_id: int) -> dict | None:
    for s in _shots(project_name):
        if int(s.get("scene_id") or 0) == scene_id:
            return s
    return None


def ensure_panel_thumbs(project_name: str) -> dict[tuple[int, int], str]:
    """Crop a ~420px-wide thumbnail (plain PIL crop — NO inpaint/mirror, so the
    picker shows the real art) for every (page, panel_index) plus a whole-page thumb
    (index -1) into projects/<name>/_panel_thumbs/. A crop is skipped when its PNG
    already exists and is newer than the source page. Returns {(page, idx): abs_path}
    (idx=-1 → whole page). Degrades gracefully: returns whatever it managed if PIL is
    missing or a page image can't be read."""
    thumbs: dict[tuple[int, int], str] = {}
    try:
        from PIL import Image
    except ImportError:
        return thumbs
    tdir = PROJECTS_ROOT / project_name / "_panel_thumbs"
    tdir.mkdir(parents=True, exist_ok=True)
    for pn, page in _pages_by_number(project_name).items():
        src_path = Path(page.get("source_image") or "")
        if not src_path.exists():
            continue
        src_mtime = src_path.stat().st_mtime
        # (idx, bbox): -1 = whole page, then one per panel.
        specs: list[tuple[int, dict]] = [(-1, {})]
        for idx, panel in enumerate(page.get("panels") or []):
            specs.append((idx, panel.get("bbox") or {}))
        im = None
        for idx, bbox in specs:
            name = f"p{pn:03d}_full.png" if idx < 0 else f"p{pn:03d}_{idx}.png"
            out = tdir / name
            if out.exists() and out.stat().st_mtime >= src_mtime:
                thumbs[(pn, idx)] = str(out)
                continue
            try:
                if im is None:
                    im = Image.open(src_path).convert("RGB")
                x, y = int(bbox.get("x", 0)), int(bbox.get("y", 0))
                w, h = int(bbox.get("w", 0)), int(bbox.get("h", 0))
                crop = im.copy() if (idx < 0 or w <= 0 or h <= 0) else im.crop((x, y, x + w, y + h))
                crop.thumbnail((THUMB_WIDTH, 100000), Image.LANCZOS)
                crop.save(out, "PNG")
                thumbs[(pn, idx)] = str(out)
            except (OSError, ValueError):
                continue
        if im is not None:
            im.close()
    return thumbs


def panels_for_page(project_name: str, page: int) -> list[dict]:
    """[{index, bbox, description, thumb_path}] for the panel picker grid."""
    thumbs = ensure_panel_thumbs(project_name)
    page_data = _pages_by_number(project_name).get(int(page)) or {}
    out: list[dict] = []
    for idx, panel in enumerate(page_data.get("panels") or []):
        out.append({
            "index": idx,
            "bbox": panel.get("bbox") or {},
            "description": str(panel.get("description") or ""),
            "thumb_path": thumbs.get((int(page), idx), ""),
        })
    return out


def render_scene_panel_path(project_name: str, scene: dict) -> str:
    """Thumb path for a scene's CURRENT panel. Prefer the panel actually rendered
    (shots.json bbox → matching panel index), else the scene's panel_ref index, else
    the whole-page thumb."""
    thumbs = ensure_panel_thumbs(project_name)
    pages = _pages_by_number(project_name)

    def _index_by_bbox(page: int, bbox: dict) -> int | None:
        for idx, panel in enumerate((pages.get(int(page)) or {}).get("panels") or []):
            pb = panel.get("bbox") or {}
            if all(int(pb.get(k, 0)) == int(bbox.get(k, -1)) for k in ("x", "y", "w", "h")):
                return idx
        return None

    # 1. Prefer shots.json — the panel actually rendered for this scene.
    shot = _first_shot_for_scene(project_name, int(scene.get("scene_id") or 0))
    if shot:
        page = int(shot.get("page") or 0)
        idx = _index_by_bbox(page, shot.get("panel_bbox") or {})
        if idx is not None and (page, idx) in thumbs:
            return thumbs[(page, idx)]
    # 2. By the scene's panel_ref index.
    page = int(scene.get("page_ref") or 0)
    pref = scene.get("panel_ref")
    pref = int(pref) if pref is not None else -1
    if pref >= 0 and (page, pref) in thumbs:
        return thumbs[(page, pref)]
    # 3. Whole page.
    return thumbs.get((page, -1), "")


def run_stage6_render(project_name: str, log: Callable[[str], None]) -> str:
    """Re-render the ACCEPTED recipe as SUBPROCESSES (cannot run in-process: the Stage 5
    PANEL_* knobs are module-level constants read at import, and Stage 4 must use
    atempo 1.35). Streams each subprocess's stdout+stderr to `log`.
      A: stage_4 --force --atempo 1.35
      B (only if A exits 0): stage_5 --force with PANEL_RERANK=0 PANEL_COS_FLOOR=0.2
         PANEL_ANCHOR_BONUS=8 (and CLAUDE_SDK_MODEL unset).
    Returns the final.mp4 path on success; raises on a non-zero exit."""
    import os
    import subprocess
    import sys

    repo_root = PROJECTS_ROOT.parent
    _py = repo_root / ".venv" / "bin" / "python"
    py = str(_py) if _py.exists() else sys.executable

    def _run(cmd: list[str], env: dict) -> None:
        log("$ " + " ".join(cmd))
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=env, cwd=str(repo_root),
        )
        for line in proc.stdout or ():
            log(_strip_ansi(line.rstrip()))
        code = proc.wait()
        if code != 0:
            raise RuntimeError(f"{cmd[2]} exited with code {code}")

    # Step A — Stage 4 TTS at atempo 1.35 (Carl voice runs slow at the default).
    _run([py, "-m", "stages.stage_4", "--project", project_name,
          "--force", "--atempo", "1.35"], dict(os.environ))

    # Step B — Stage 5 render with the proven panel knobs; drop CLAUDE_SDK_MODEL.
    env = {**os.environ, "PANEL_RERANK": "0", "PANEL_COS_FLOOR": "0.2",
           "PANEL_ANCHOR_BONUS": "8"}
    env.pop("CLAUDE_SDK_MODEL", None)
    _run([py, "-m", "stages.stage_5", "--project", project_name, "--force"], env)

    final = PROJECTS_ROOT / project_name / "final.mp4"
    if not final.exists():
        raise RuntimeError("Stage 5 finished but final.mp4 is missing")
    return str(final)


# ─── Error formatting ──────────────────────────────────────────────────────

def format_exception(e: BaseException) -> str:
    tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
    return tb[-2000:]
