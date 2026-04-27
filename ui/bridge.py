"""
Async wrappers around the synchronous stage pipelines so the UI can run
them off the event loop via page.run_task().
"""
import asyncio
import json
import queue
import re
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from config import GDRIVE_BASE


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


# ─── Stage 1 ────────────────────────────────────────────────────────────────

def run_stage_1(
    prompt: str,
    log: Callable[[str], None],
    ask_user: Callable[[str], str] | None = None,
) -> tuple[dict, str]:
    """
    Invoke Stage 1. Returns (comic_context, project_name).

    The agent occasionally pauses for input (clarifying questions, final
    confirm). If `ask_user` is provided, each such prompt is routed to it
    (synchronously — ask_user blocks the worker thread until the user
    answers). If not provided, we default to "skip"/"1" for non-interactive
    runs.
    """
    from stages.stage_1.agent import ScriptAgent
    from stages.stage_1.storage import save_comic_context, slugify
    from stages.stage_1 import ui as _ui
    from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL, get_project_dirs

    # Fallback for when the caller didn't hand us a live input channel
    _fallback = iter(["skip", "1"])
    def _default_ask(p: str) -> str:
        try:
            return next(_fallback)
        except StopIteration:
            return ""

    _asker = ask_user or _default_ask

    def _routed_input(prompt_text: str = "") -> str:
        log(f"❓ {prompt_text}")
        ans = _asker(prompt_text) or ""
        if ans:
            log(f"   > {ans}")
        return ans

    # agent.py now calls `_stage1_ui.get_user_input(...)` (module-ref lookup),
    # so patching the attribute on the ui module is sufficient.
    original_ui_get = _ui.get_user_input
    _ui.get_user_input = _routed_input

    import builtins
    original_print = builtins.print
    def _log_print(*a, **kw):
        log(_strip_ansi(" ".join(str(x) for x in a)))

    try:
        builtins.print = _log_print
        agent = ScriptAgent(
            api_key=OPENROUTER_API_KEY,
            base_url=OPENROUTER_BASE_URL,
            model=OPENROUTER_MODEL,
        )
        ctx = agent.run(prompt)
    finally:
        builtins.print = original_print
        _ui.get_user_input = original_ui_get

    if not ctx:
        raise RuntimeError("Stage 1 returned no comic_context (agent aborted).")

    project_name = slugify(prompt or ctx.get("title", "untitled_project"))
    save_comic_context(ctx, project_name, get_project_dirs)

    return ctx, project_name


# ─── Stage 2 ────────────────────────────────────────────────────────────────

def run_stage_2(project_name: str, log: Callable[[str], None]) -> list[dict]:
    from stages.stage_2 import preprocess_project
    return preprocess_project(project_name, progress=log, force_refresh=False)


def load_preprocessed(project_name: str) -> list[dict]:
    prep = GDRIVE_BASE / project_name / "preprocessed"
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
    log("[stage3] asking LLM to propose 3 narration modes…")
    proposals = propose_modes(project_name, n=3)
    return [p.to_dict() for p in proposals]


def run_stage_3_write(
    project_name: str,
    mode: str,
    hook_hint: str,
    log: Callable[[str], None],
) -> dict:
    from stages.stage_3.pipeline import write_script, save_narration
    log(f"[stage3] writing narration in mode={mode}…")
    narration = write_script(project_name, mode, hook_hint=hook_hint)
    save_narration(narration, project_name)
    return narration.to_dict()


def load_narration(project_name: str) -> dict | None:
    p = GDRIVE_BASE / project_name / "narration.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def save_narration_edits(project_name: str, narration: dict) -> None:
    p = GDRIVE_BASE / project_name / "narration.json"
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
