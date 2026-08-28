"""
Async wrappers around the synchronous stage pipelines so the UI can run
them off the event loop via page.run_task().
"""
import asyncio
import json
import os
import queue
import re
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from config import PROJECTS_ROOT, RESEARCH_SESSIONS_ROOT
from utils.atomic_json import write_json_atomic   # re-exported: ui.state / ui.custom_image import it from here


def _quarantine_corrupt(path: Path, what: str) -> None:
    """Move a corrupt JSON file aside instead of letting the next save overwrite it.

    The old code swallowed JSONDecodeError and returned an EMPTY doc, so the UI drew
    every beat as unselected and the first click wrote that emptiness to disk —
    turning a recoverable file into permanent loss. Renaming keeps the bytes around."""
    try:
        dead = path.with_name(path.name + ".corrupt")
        os.replace(path, dead)
        print(f"[ui] {what} is corrupt — moved to {dead.name}. Selections could not be loaded.")
    except OSError as exc:
        print(f"[ui] {what} is corrupt and could not be set aside ({exc}).")


_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')

def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


async def run_blocking(fn: Callable[..., Any], *args, **kwargs) -> Any:
    """Run a blocking callable in a worker thread."""
    return await asyncio.to_thread(fn, *args, **kwargs)


# ─── Stage 1: Research Scout bridge ────────────────────────────────────────

def _scout_store(root: Path | None = None):
    from stages.research_scout.storage import SessionStore

    return SessionStore(root or RESEARCH_SESSIONS_ROOT)


def _scout_workflow(root: Path | None = None):
    from stages.research_scout.workflow import ScoutWorkflow
    from stages.youcom_scout import build_scouted_digest

    # Without the digest the prompt renders "SCOUTED DIGEST:" empty and the scout
    # happily re-proposes produced/banned questions (found 2026-08-21).
    return ScoutWorkflow(store=_scout_store(root), digest=build_scouted_digest())


def start_scout_session(mode: str, user_intent: str):
    from stages.research_scout.bank_fallback import bank_suggestions_for_mode
    from stages.research_scout.models import ScoutMode

    scout_mode = ScoutMode(mode)
    intent = str(user_intent or "").strip()
    workflow = _scout_workflow()
    if not intent:
        # Two-tier empty-intent fallback (Master 2026-08-22), replacing the old
        # "raise ValueError" here: Tier A prefers a real, already-vetted
        # still-open qa_question_bank.md question (zero API cost, QA only —
        # micro has no bank file); Tier B discovers a real question along the
        # next angle in research_policies/general_angles.v1.json when the bank
        # has nothing usable (Master 2026-08-28: a bare angle isn't a question,
        # so it must be turned into one before it reaches the general-research
        # prompt — see ScoutWorkflow.discover_question), so a normal
        # general-research round always has something to research instead of
        # erroring out. This stays the documented behaviour for any
        # programmatic (non-UI) caller — the Stage 1 UI itself no longer calls
        # this with an empty intent; see discover_intent() below for why.
        suggestions = bank_suggestions_for_mode(scout_mode)
        intent = suggestions[0]["question"] if suggestions else workflow.discover_question(scout_mode)
    return workflow.start(scout_mode, intent)


def discover_intent(mode: str) -> str:
    """Tier B on its own, WITHOUT starting a session — the human-review step
    that stages/youcom_scout.py::is_burned's docstring deliberately relies on.
    is_burned lets synonym re-skins of an already-REJECTED qa_question_bank.md
    row slip through "by choice", saying "The Master-review step after
    discover is the catch for those". A second empty Send used to pipe a
    freshly discovered question straight into start_scout_session +
    run_scout_general — spending a SECOND research call enumerating answers to
    a dud lane before any human saw the question at all. This returns just the
    discovered question/moment so the UI can drop it into the intent box for a
    human to read, edit, or delete; pressing Send again then researches it
    normally. Never writes to SessionStore — see
    stages.research_scout.workflow.ScoutWorkflow.discover_question for the
    session-free discovery itself."""
    from stages.research_scout.models import ScoutMode

    return _scout_workflow().discover_question(ScoutMode(mode))


def list_bank_suggestions(mode: str) -> list[dict]:
    """Tier A candidates for the UI to show BEFORE spending any API budget —
    see start_scout_session's fallback and stages/research_scout/bank_fallback.py."""
    from stages.research_scout.bank_fallback import bank_suggestions_for_mode
    from stages.research_scout.models import ScoutMode

    return bank_suggestions_for_mode(ScoutMode(mode))


def run_scout_general(session_id: str):
    return _scout_workflow().run_general(session_id)


def run_scout_specific(session_id: str, feedback: str = ""):
    return _scout_workflow().research_specific(session_id, feedback)


def rerun_scout_general(session_id: str, feedback: str = ""):
    # One bridge call per user action: the UI never orchestrates a rerun + run pair
    # itself, so both workflow calls happen on the SAME workflow instance here.
    workflow = _scout_workflow()
    workflow.rerun_general(session_id, feedback)
    return workflow.run_general(session_id)


def back_scout_general(session_id: str):
    return _scout_workflow().back_general(session_id)


def approve_scout_general(session_id: str, candidate_id: str):
    return _scout_workflow().approve_general(session_id, candidate_id)


def approve_scout_specific(
    session_id: str,
    candidate_ids: list[str] | tuple[str, ...],
    feedback: str = "",
):
    return _scout_workflow().decide_specific(session_id, candidate_ids, feedback=feedback)


def archive_scout_session(session_id: str, reason: str = "Research restarted"):
    workflow = _scout_workflow()
    try:
        return workflow.archive(session_id, reason)
    except Exception as exc:
        # A newly created GENERAL_DRAFT has no normal review transition yet,
        # but starting over must still archive it rather than delete its data.
        from stages.research_scout.models import SessionState
        from stages.research_scout.workflow import InvalidTransition

        if not isinstance(exc, InvalidTransition):
            raise
        session = workflow.store.load(session_id)
        if session.state is not SessionState.GENERAL_DRAFT:
            raise
        session.state = SessionState.ARCHIVED
        return workflow.store.save(
            session,
            event="session_archived",
            detail={"reason": reason},
        )


def create_scout_project(session_id: str, project_slug: str) -> str:
    from stages.research_scout.project_factory import create_project_from_session

    return create_project_from_session(session_id, project_slug)


def list_scout_sessions(root: Path | None = None) -> list[Any]:
    """Return unfinished sessions, including sessions with no project yet."""
    from stages.research_scout.models import SessionState

    store = _scout_store(root)
    sessions = []
    for session_dir in sorted(store.root.iterdir()):
        if not session_dir.is_dir() or not (session_dir / "session.json").exists():
            continue
        try:
            session = store.load(session_dir.name)
        except Exception:
            continue
        if session.state not in {SessionState.ARCHIVED, SessionState.COMPLETE}:
            sessions.append(session)
    return sessions


def load_scout_session(session_id: str, root: Path | None = None):
    return _scout_store(root).load(session_id)


def load_scout_candidates(session_id: str, root: Path | None = None) -> list[dict]:
    store = _scout_store(root)
    path = store.artifact_path(session_id, "general/candidates.v1.json")
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    candidates = data.get("candidates", []) if isinstance(data, dict) else []
    return [dict(candidate) for candidate in candidates if isinstance(candidate, dict)]


def load_scout_audit(session_id: str, root: Path | None = None) -> list[dict]:
    """Parse audit.jsonl into one dict per line, skipping corrupt lines silently."""
    store = _scout_store(root)
    path = store.artifact_path(session_id, "audit.jsonl")
    if not path.exists():
        return []
    events: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            events.append(record)
    return events


def load_scout_candidates_rev(
    session_id: str, revision: int, root: Path | None = None
) -> list[dict]:
    store = _scout_store(root)
    path = store.artifact_path(session_id, f"general/candidates.rev{revision}.v1.json")
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    candidates = data.get("candidates", []) if isinstance(data, dict) else []
    return [dict(candidate) for candidate in candidates if isinstance(candidate, dict)]


def load_scout_gates(session_id: str, root: Path | None = None) -> list[dict]:
    store = _scout_store(root)
    path = store.artifact_path(session_id, "specific/evidence_gate.v1.json")
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [dict(gate) for gate in data if isinstance(gate, dict)]
    if isinstance(data, dict) and isinstance(data.get("gates"), list):
        return [dict(gate) for gate in data["gates"] if isinstance(gate, dict)]
    return [data] if isinstance(data, dict) else []


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
    write_json_atomic(PROJECTS_ROOT / project_name / "narration.json", narration)


def is_answer_project(project_name: str) -> bool:
    """True for a Q&A (answer_research) project — Stage 7's single-panel-per-scene
    storyboard editor doesn't apply since Q&A renders multiple panels per scene
    (sub-shots chosen in Review Beats instead). Mirrors stages/review_gate.py's
    own _plot_source check; keyed on comic_context.plot_source, not a mode name."""
    p = PROJECTS_ROOT / project_name / "comic_context.json"
    if not p.exists():
        return False
    try:
        ctx = json.loads(p.read_text())
    except json.JSONDecodeError:
        return False
    return str(ctx.get("plot_source", "") or "") == "answer_research"


# ─── Review Gate: pre-TTS beat/panel review ────────────────────────────────
# Reads/writes review/candidates.json and review/locks.json directly (JSON
# contract shared with stages/review_gate.py, which produces candidates.json;
# this UI never imports that module).

def list_review_projects() -> list[str]:
    """Projects that have a review/candidates.json ready to review."""
    if not PROJECTS_ROOT.exists():
        return []
    return sorted(
        d.name for d in PROJECTS_ROOT.iterdir()
        if d.is_dir() and (d / "review" / "candidates.json").exists()
    )


def load_review_candidates(project_name: str) -> dict | None:
    p = PROJECTS_ROOT / project_name / "review" / "candidates.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def load_review_locks(project_name: str) -> dict:
    p = PROJECTS_ROOT / project_name / "review" / "locks.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            # Do NOT fall through silently: the empty doc below reads as "nothing was
            # ever picked", and the next click would save that over the damaged file.
            _quarantine_corrupt(p, "review/locks.json")
    return {"approved": False, "approved_at": None, "narration_sha1": None, "locks": {}}


def save_review_locks(project_name: str, locks_doc: dict) -> None:
    write_json_atomic(PROJECTS_ROOT / project_name / "review" / "locks.json", locks_doc)


def load_hidden_panels(project_name: str) -> dict:
    """review/hidden_panels.json — the project-wide "never show me this panel again"
    blacklist ({"hidden": [{"page","panel"}, ...]}). A missing file means nothing is
    hidden. It is a UI FILTER layer only: candidates.json (the matcher's export) is
    never rewritten, so clearing this file restores every panel."""
    p = PROJECTS_ROOT / project_name / "review" / "hidden_panels.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            pass
    return {"hidden": []}


def save_hidden_panels(project_name: str, doc: dict) -> None:
    write_json_atomic(PROJECTS_ROOT / project_name / "review" / "hidden_panels.json", doc)


def load_music_config(project_name: str) -> dict:
    """music.json — the project's music brief ({"genre": str}). A missing file means the
    genre was never chosen for this project, and the caller falls back to config.MUSIC_GENRE.
    Kept at the project root rather than under review/ because it outlives the review step:
    the render reads it, and a later cue map will sit beside it."""
    p = PROJECTS_ROOT / project_name / "music.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def save_music_config(project_name: str, doc: dict) -> None:
    write_json_atomic(PROJECTS_ROOT / project_name / "music.json", doc)


def narration_sha1(project_name: str) -> str | None:
    p = PROJECTS_ROOT / project_name / "narration.json"
    if not p.exists():
        return None
    import hashlib
    return hashlib.sha1(p.read_bytes()).hexdigest()


# Set once at launch by `python -m ui --lan`. Desktop stays False, so its behaviour — and
# every path below — is byte-identical to before this existed.
WEB_MODE = False


def set_web_mode(on: bool) -> None:
    global WEB_MODE
    WEB_MODE = bool(on)


def asset_src(abs_path: str | Path) -> str:
    """An `ft.Image(src=...)` value that works in whichever mode the UI is running in.

    DESKTOP: the absolute path. The Flutter client reads it off disk lazily as the ListView
    scrolls, which is the whole reason this screen does not use base64 — embedding every tile
    up front (100+ per beat × 25 beats) once killed the client outright.

    WEB: a path RELATIVE to PROJECTS_ROOT, which `--lan` hands to flet as its assets_dir. The
    browser then fetches each thumb over HTTP, lazily, for the same reason. A local absolute
    path as src is simply unreachable from another device — that is why the review screen
    showed no images at all over the LAN.
    """
    p = Path(abs_path or "")
    if not p.exists():
        return ""
    if not WEB_MODE:
        return str(p)
    try:
        return "/" + p.resolve().relative_to(PROJECTS_ROOT.resolve()).as_posix()
    except ValueError:
        return str(p)          # outside projects/ — nothing to serve it from


def review_thumb_path(project_name: str, rel_path: str) -> str:
    """Resolve a candidate's "review/thumbs/..." path to something ft.Image can load,
    or "" if missing. Mode-aware — see asset_src."""
    if not rel_path:
        return ""
    full = PROJECTS_ROOT / project_name / rel_path
    return asset_src(full)



def image_b64(abs_path: str) -> str:
    """Base64 of an image file at an ABSOLUTE path (for ft.Image src), or "" if
    missing — used to preview an imported intro image whose jpg lives outside
    review/thumbs."""
    import base64
    p = Path(abs_path or "")
    return base64.b64encode(p.read_bytes()).decode() if p.exists() else ""


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
            post_atempo=1.35,  # explicit: the UI must never fall back to a slower pace
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
