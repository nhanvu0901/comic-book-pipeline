"""
ScriptAgent — interactive multi-phase agent for comic identification + context.

Mode: narrate_1_comic (default).
Finds one specific comic issue, gathers wiki context, produces comic_context.json.

Phases (user approves/rejects each one):
  1. PLAN     — extract entities + generate search queries (no guessing)
  2. SEARCH   — web search to identify the comic + find batcave_url
  3. WIKI     — fetch verified plot text from fandom wiki
  4. CONFIRM  — build final comic_context, user gives final approval

Each phase runs in a loop: run → show result → user approves or rejects with
feedback. Failed attempts stay in conversation history so the LLM can improve.
Max retries per phase is configurable via MAX_PHASE_RETRIES.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from config import MAX_PHASE_RETRIES
from utils.session_history import SessionHistory
from .llm import call_llm, extract_json, load_system_prompt, create_client
from . import tools as _tools


@dataclass
class PhaseResult:
    """Yielded to the UI after each phase attempt."""
    phase: str
    attempt: int
    max_attempts: int
    data: dict | None
    raw_text: str
    is_final_confirm: bool = False


@dataclass
class PhaseDecision:
    """Returned by the UI after user reviews a PhaseResult."""
    approved: bool
    feedback: str = ""


class ScriptAgent:
    """
    Interactive agent that produces comic_context.json.

    The caller drives the interaction loop:
      1. Call run_phase() which returns a PhaseResult
      2. Show it to the user, get approve/reject
      3. Call submit_decision() with the PhaseDecision
      4. Repeat until all phases complete or user aborts

    Or use run_interactive() with callbacks for the full flow.
    """

    PHASES = ["plan", "search", "wiki", "confirm"]

    def __init__(self, api_key: str, base_url: str, model: str, mode: str = "narrate_1_comic"):
        self.client = create_client(api_key, base_url)
        self.model = model
        self.mode = mode
        self.system_prompt = load_system_prompt()
        self.history = SessionHistory(context_limit=128_000)
        self.phase_index = 0
        self.query_plan: dict | None = None
        self.search_result: dict | None = None
        self.wiki_plot: str = ""
        self.wiki_url: str = ""
        self.user_prompt: str = ""
        self.comic_context: dict | None = None
        self._on_token: Callable[[str], None] | None = None
        _tools.init(self.client, self.model)

    @property
    def current_phase(self) -> str:
        if self.phase_index >= len(self.PHASES):
            return "done"
        return self.PHASES[self.phase_index]

    def send_to_llm(self, user_message: str, tools: list | None = None) -> str:
        self.history.add("user", user_message)
        response_text, updated = call_llm(
            self.client,
            self.history.messages,
            self.system_prompt,
            self.model,
            tools=tools,
            on_token=self._on_token,
        )
        self.history.replace_from(updated)
        return response_text

    def run_interactive(
        self,
        initial_prompt: str,
        on_phase_result: Callable[[PhaseResult], PhaseDecision],
        on_token: Callable[[str], None] | None = None,
        on_log: Callable[[str], None] | None = None,
    ) -> dict | None:
        """
        Run the full pipeline with callback-driven user interaction.

        on_phase_result: called after each phase attempt. Must return a PhaseDecision.
        on_token:        called per streamed token for the final text response.
        on_log:          called with status messages during processing.
        """
        self.user_prompt = initial_prompt
        self._on_token = on_token
        log = on_log or (lambda s: None)

        from .tools.sequential_thinking import reset_session
        reset_session()

        for phase_name in self.PHASES:
            self.phase_index = self.PHASES.index(phase_name)
            result = self._run_phase_loop(phase_name, log, on_phase_result)
            if result is None:
                return None

        return self.comic_context

    def _run_phase_loop(
        self,
        phase_name: str,
        log: Callable[[str], None],
        on_phase_result: Callable[[PhaseResult], PhaseDecision],
    ) -> dict | None:
        """Run one phase with retry loop. Returns result dict or None if aborted."""
        for attempt in range(1, MAX_PHASE_RETRIES + 1):
            log(f"Phase: {phase_name} (attempt {attempt}/{MAX_PHASE_RETRIES})")

            phase_result = self._execute_phase(phase_name, attempt, log)
            decision = on_phase_result(phase_result)

            if decision.approved:
                self.history.mark_phase(phase_name, approved=True)
                return phase_result.data or {}
            else:
                self.history.mark_phase(phase_name, approved=False)
                if attempt < MAX_PHASE_RETRIES:
                    feedback_msg = (
                        f"The user REJECTED the {phase_name} result and gave this feedback:\n"
                        f"{decision.feedback}\n\n"
                        f"Please redo the {phase_name} phase, addressing the user's concerns. "
                        f"This is attempt {attempt + 1} of {MAX_PHASE_RETRIES}."
                    )
                    self.history.add("user", feedback_msg)
                else:
                    log(f"Max retries ({MAX_PHASE_RETRIES}) reached for {phase_name}.")
                    return None

        return None

    def _execute_phase(
        self,
        phase_name: str,
        attempt: int,
        log: Callable[[str], None],
    ) -> PhaseResult:
        """Execute a single attempt of a phase."""
        handler = {
            "plan": self._phase_plan,
            "search": self._phase_search,
            "wiki": self._phase_wiki,
            "confirm": self._phase_confirm,
        }[phase_name]
        return handler(attempt, log)

    def _phase_plan(self, attempt: int, log: Callable[[str], None]) -> PhaseResult:
        from .tools import SEQUENTIAL_THINKING_TOOL

        if attempt == 1:
            prompt = (
                f"{self.user_prompt}\n\n"
                "IMPORTANT: This is the QUERY PLANNING phase. Extract entities from the "
                "user's request and generate 2-3 targeted search queries. Do NOT try to "
                "identify the exact comic yet — that happens in the search phase.\n"
                "Do NOT use web_search or fetch_wiki — those come in later phases.\n"
                "If anything is unclear, include it in the 'ambiguities' field — "
                "the user will review and can give feedback before we proceed."
            )
        else:
            prompt = (
                "Please provide an updated query plan based on the feedback above. "
                "Respond with your query_plan JSON."
            )

        raw = self.send_to_llm(prompt, tools=[SEQUENTIAL_THINKING_TOOL])
        data = extract_json(raw)
        if data and data.get("phase") == "query_plan":
            self.query_plan = data

        return PhaseResult(
            phase="plan",
            attempt=attempt,
            max_attempts=MAX_PHASE_RETRIES,
            data=self.query_plan,
            raw_text=raw,
        )

    def _phase_search(self, attempt: int, log: Callable[[str], None]) -> PhaseResult:
        from .tools import WEB_SEARCH_TOOL, PARAPHRASE_QUERY_TOOL

        queries_hint = ""
        if self.query_plan and self.query_plan.get("search_queries"):
            queries = self.query_plan["search_queries"]
            queries_hint = "Suggested search queries from the planning phase:\n"
            for i, q in enumerate(queries, 1):
                queries_hint += f"  {i}. {q}\n"
            queries_hint += "\nUse these as starting points — adapt or add more as needed.\n\n"

        if attempt == 1:
            prompt = (
                f"{queries_hint}"
                "You MUST use web_search now to IDENTIFY the comic and gather ALL details:\n"
                "1. Find the exact comic — title, series, issue numbers, writer, artist, "
                "year, publisher.\n"
                "2. CRITICAL: Search for the batcave.biz URL for this comic. "
                "Search 'batcave.biz [series name]' and look at the URLs in the results "
                "for a pattern like batcave.biz/{id}-{slug}.html. "
                "Copy the full URL and include it as 'batcave_url'.\n"
                "3. If anything is ambiguous or could refer to multiple comics, "
                "note it clearly so the user can clarify.\n\n"
                "After searching, respond with your search_result JSON containing all "
                "verified details."
            )
        else:
            prompt = (
                "Please redo the web search based on the feedback above. "
                "Respond with your updated search_result JSON including batcave_url."
            )

        raw = self.send_to_llm(prompt, tools=[WEB_SEARCH_TOOL, PARAPHRASE_QUERY_TOOL])
        data = extract_json(raw)
        if data and data.get("phase") == "search_result":
            self.search_result = data

        return PhaseResult(
            phase="search",
            attempt=attempt,
            max_attempts=MAX_PHASE_RETRIES,
            data=self.search_result,
            raw_text=raw,
        )

    def _phase_wiki(self, attempt: int, log: Callable[[str], None]) -> PhaseResult:
        from .tools import FETCH_WIKI_TOOL

        comic_title = self._get_comic_title()
        publisher = ""
        if self.search_result:
            publisher = self.search_result.get("publisher", "")

        if attempt == 1:
            prompt = (
                f"You MUST call fetch_wiki now to get the verified plot text. Use these details:\n"
                f"- query: \"{comic_title} wiki plot synopsis\"\n"
                f"- publisher: \"{publisher}\"\n"
                "If you already found a wiki URL during web search, pass it as wiki_url.\n"
                "After getting the plot text, respond with a brief summary of what you found "
                "so the user can verify it's the right comic."
            )
        else:
            prompt = (
                "Please try fetching the wiki plot again based on the feedback above. "
                "Try a different query or wiki source if the previous one was wrong."
            )

        raw = self.send_to_llm(prompt, tools=[FETCH_WIKI_TOOL])
        self._extract_wiki_data_from_messages()

        return PhaseResult(
            phase="wiki",
            attempt=attempt,
            max_attempts=MAX_PHASE_RETRIES,
            data={
                "wiki_plot": self.wiki_plot[:500] + ("..." if len(self.wiki_plot) > 500 else ""),
                "wiki_url": self.wiki_url,
                "plot_length": len(self.wiki_plot),
            } if self.wiki_plot else {"wiki_plot": "", "wiki_url": "", "plot_length": 0},
            raw_text=raw,
        )

    def _phase_confirm(self, attempt: int, log: Callable[[str], None]) -> PhaseResult:
        self.comic_context = self._build_comic_context()
        return PhaseResult(
            phase="confirm",
            attempt=attempt,
            max_attempts=MAX_PHASE_RETRIES,
            data=self.comic_context,
            raw_text="",
            is_final_confirm=True,
        )

    def _build_comic_context(self) -> dict:
        sr = self.search_result or {}
        return {
            "status": "ready",
            "pipeline_mode": self.mode,
            "user_prompt": self.user_prompt,
            "title": sr.get("title", "Unknown"),
            "series": sr.get("series", ""),
            "issues": sr.get("issues", ""),
            "year": sr.get("year", ""),
            "writer": sr.get("writer", ""),
            "artist": sr.get("artist", ""),
            "publisher": sr.get("publisher", ""),
            "characters": sr.get("characters", []),
            "batcave_url": sr.get("batcave_url", ""),
            "wiki_url": self.wiki_url or "",
            "plot_summary": self.wiki_plot or "",
            "confidence": sr.get("confidence", "low"),
        }

    def _get_comic_title(self) -> str:
        if self.search_result:
            return (
                self.search_result.get("title", "")
                or self.search_result.get("series", "unknown comic")
            )
        if self.query_plan:
            entities = self.query_plan.get("entities", {})
            chars = entities.get("characters", [])
            if chars:
                return " ".join(chars)
        return self.user_prompt or "unknown comic"

    def _extract_wiki_data_from_messages(self):
        import json
        for msg in reversed(self.history.messages):
            if msg.get("role") != "tool":
                continue
            content = msg.get("content", "")
            if not isinstance(content, str):
                continue
            try:
                result = json.loads(content)
            except json.JSONDecodeError:
                continue
            if isinstance(result, dict) and result.get("plot_text"):
                self.wiki_plot = result["plot_text"]
                self.wiki_url = result.get("wiki_url", "")
                return

    def save_session(self, project_path) -> None:
        from pathlib import Path
        self.history.save(Path(project_path) / "session_history.json")

    @classmethod
    def load_session(cls, project_path, api_key: str, base_url: str, model: str) -> SessionHistory:
        from pathlib import Path
        return SessionHistory.load(Path(project_path) / "session_history.json")
