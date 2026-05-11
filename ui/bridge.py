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


# ─── Stage 1 interactive I/O bridge ────────────────────────────────────────

@dataclass
class InputBridge:
    """
    Thread-safe Q/A channel between the agent (running in a worker thread
    via asyncio.to_thread) and the Flet UI thread.

      - worker calls `ask(prompt)` → blocks until UI calls `answer(text)`
      - UI provides `on_question` which is called (on the worker thread)
        whenever the worker wants input; the UI marshals the prompt to
        its input field inside that callback.

    The UI may also call `cancel()` to unblock pending asks (e.g. user
    aborts). On cancel, ask() returns "" and the agent treats that as skip.
    """
    on_question: Callable[[str], None] | None = None

    def __post_init__(self):
        self._q: queue.Queue[str] = queue.Queue()
        self._pending: str | None = None
        self._cancelled = False

    def ask(self, prompt: str) -> str:
        if self._cancelled:
            return ""
        self._pending = prompt
        if self.on_question:
            try:
                self.on_question(prompt)
            except Exception:
                pass
        try:
            return self._q.get(timeout=600)   # 10 min safety cap
        except queue.Empty:
            return ""

    def answer(self, text: str) -> None:
        self._pending = None
        self._q.put(text)

    def cancel(self) -> None:
        self._cancelled = True
        try:
            self._q.put_nowait("")
        except queue.Full:
            pass

    def pending(self) -> str | None:
        return self._pending


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
        final = assemble_project(project_name, keep_intermediates=False)
    finally:
        builtins.print = original
    return str(final)


# ─── Error formatting ──────────────────────────────────────────────────────

def format_exception(e: BaseException) -> str:
    tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
    return tb[-2000:]
