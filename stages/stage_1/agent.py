"""
ScriptAgent — multi-turn conversational agent for comic identification + wiki context gathering.

Scope (trimmed 2026-04-19): Stage 1 no longer writes scene-by-scene narration.
It only identifies the comic and gathers wiki plot context. Downstream stages
(preprocess -> narration synthesis -> TTS -> video) consume the output
`comic_context.json` and produce their own artifacts.

Phases:
  1. IDENTIFY  — sequential_thinking only; LLM picks the comic from the prompt
  2. SEARCH    — web_search / paraphrase_query; verify details + find batcave_url
  3. QUESTIONS — no tools; ask the user to disambiguate if needed
  4. WIKI      — fetch_wiki only; pull verified plot text
  Then: build comic_context dict, show it, ask y/n, return it.
"""
from .ui import Colors, print_agent, print_warning, print_error, print_list_item
from . import ui as _stage1_ui
from .llm import call_llm, extract_json, load_system_prompt, create_client
from .display import display_analysis, display_comic_context
from . import tools as _tools


class ScriptAgent:
    """
    Multi-turn conversational agent that produces comic_context.json.
    Phases: start → identify → search → questions → wiki → done
    """

    def __init__(self, api_key: str, base_url: str, model: str):
        self.client = create_client(api_key, base_url)
        self.model = model
        self.system_prompt = load_system_prompt()
        self.messages = []
        self.phase = "start"
        self.analysis = None
        self.wiki_plot = None
        self.wiki_url = None
        self.user_prompt = ""
        self.comic_context = None
        _tools.init(self.client, self.model)

    def send_to_llm(self, user_message: str, tools: list | None = None) -> str:
        self.messages.append({"role": "user", "content": user_message})
        response_text, self.messages = call_llm(
            self.client, self.messages, self.system_prompt, self.model, tools=tools
        )
        return response_text

    def run(self, initial_prompt: str | None = None) -> dict | None:
        from .ui import print_header, print_error as _print_error
        from .tools.sequential_thinking import reset_session

        reset_session()
        print_header("STAGE 1: PanelNarrator — Comic Identification & Context")

        print_agent(
            "Welcome! Tell me about a comic book event, story arc, or character moment "
            "and I'll identify the comic, find it on batcave.biz, and pull the verified "
            "plot context. Later stages will read the pages and produce the narration."
        )

        if not initial_prompt:
            print()
            print(f"  {Colors.DIM}Examples:{Colors.END}")
            print(f"  {Colors.DIM}  • \"The death of Gwen Stacy\"{Colors.END}")
            print(f"  {Colors.DIM}  • \"Invincible vs Omni-Man first fight\"{Colors.END}")
            print(f"  {Colors.DIM}  • \"Batman Knightfall Bane breaks the bat\"{Colors.END}")
            initial_prompt = _stage1_ui.get_user_input("Describe a comic book event or story")

        if not initial_prompt:
            _print_error("No prompt provided.")
            return None

        self.user_prompt = initial_prompt

        # ─── Phase 1: IDENTIFY ────────────────────────────────────────────
        self.phase = "identify"
        print(f"\n  {Colors.DIM}🧠 Phase 1: Identifying the comic...{Colors.END}")

        from .tools import SEQUENTIAL_THINKING_TOOL
        identify_tools = [SEQUENTIAL_THINKING_TOOL]

        identify_prompt = (
            f"{initial_prompt}\n\n"
            "IMPORTANT: This is the IDENTIFICATION phase only. Analyze what comic this "
            "refers to and provide your analysis JSON. Do NOT use web_search or fetch_wiki "
            "yet — those come in later phases. Do NOT include questions_for_user yet — "
            "we will ask questions AFTER web search when you have real information."
        )
        raw = self.send_to_llm(identify_prompt, tools=identify_tools)
        data = extract_json(raw)

        if not data or data.get("phase") != "analysis":
            print_agent(raw)
            print_error("Couldn't parse identification. Aborting.")
            return None

        self.analysis = data
        display_analysis(data)

        # ─── Phase 2: WEB SEARCH ──────────────────────────────────────────
        self.phase = "search"
        print(f"\n  {Colors.DIM}🌐 Phase 2: Searching for verified details...{Colors.END}")

        from .tools import WEB_SEARCH_TOOL, PARAPHRASE_QUERY_TOOL
        search_tools = [WEB_SEARCH_TOOL, PARAPHRASE_QUERY_TOOL]

        search_prompt = (
            "You MUST use web_search now to do ALL of the following:\n"
            "1. Verify and enrich details — issue numbers, writer/artist credits, "
            "exact plot points, year.\n"
            "2. CRITICAL: Search for the batcave.biz URL for this comic. "
            "Search 'batcave.biz [series name]' and look at the URLs in the results "
            "for a pattern like batcave.biz/{id}-{slug}.html. "
            "Copy the full URL and include it as 'batcave_url' in your analysis.\n"
            "Search even if you're confident. Then respond with your updated analysis JSON. "
            "Do NOT include questions_for_user yet."
        )
        raw = self.send_to_llm(search_prompt, tools=search_tools)
        data = extract_json(raw)
        if data and data.get("phase") == "analysis":
            self.analysis = data
            display_analysis(data)

        # ─── Phase 3: QUESTIONS ───────────────────────────────────────────
        self.phase = "questions"
        print(f"\n  {Colors.DIM}❓ Phase 3: Clarifying details...{Colors.END}")
        questions_prompt = (
            "Now that you have real information from web search, ask the user "
            "any meaningful questions you have. Consider:\n"
            "- Is there ambiguity about which version/run of this comic?\n"
            "- Are there specific moments the user wants to focus on?\n"
            "Include your questions in the 'questions_for_user' field of your analysis JSON. "
            "If you have NO questions (everything is clear), respond with an empty questions_for_user array."
        )
        raw = self.send_to_llm(questions_prompt, tools=[])
        data = extract_json(raw)
        if data and data.get("phase") == "analysis":
            self.analysis = data
            questions = data.get("questions_for_user", [])

            if questions:
                print(f"\n  {Colors.BOLD}❓ I need some details:{Colors.END}")
                for i, q in enumerate(questions, 1):
                    print(f"     {Colors.BOLD}[{i}]{Colors.END} {q}")
                answer = _stage1_ui.get_user_input("Your answer (or 'skip' to let me decide)")
                if answer.lower() == "skip":
                    answer = "Use your best judgment for all questions. Pick the most iconic/well-known version."
                print(f"\n  {Colors.DIM}🧠 Processing your answer...{Colors.END}")
                raw = self.send_to_llm(answer)
                data = extract_json(raw)
                if data and data.get("phase") == "analysis":
                    self.analysis = data
                    display_analysis(data)

        # ─── Phase 4: WIKI FETCH ──────────────────────────────────────────
        self.phase = "wiki"
        print(f"\n  {Colors.DIM}📚 Phase 4: Fetching verified plot from wiki...{Colors.END}")

        comic_title = self._get_comic_title()
        publisher = self.analysis.get("parsed_input", {}).get("publisher_guess", "") if self.analysis else ""

        from .tools import FETCH_WIKI_TOOL
        wiki_tools = [FETCH_WIKI_TOOL]

        wiki_prompt = (
            f"You MUST call fetch_wiki now to get the verified plot text. Use these details:\n"
            f"- query: \"{comic_title} wiki plot synopsis\"\n"
            f"- publisher: \"{publisher}\"\n"
            "If you already found a wiki URL during web search, pass it as wiki_url.\n"
            "After getting the plot text, respond with a short acknowledgement — "
            "no need to restate the plot, the system will read the tool result directly."
        )
        raw = self.send_to_llm(wiki_prompt, tools=wiki_tools)
        self._extract_wiki_data_from_messages()

        if not self.wiki_plot:
            print_warning("Wiki fetch returned no plot text. Continuing without it — "
                          "downstream stages will rely on VLM page reading.")

        # ─── Build comic_context and confirm ──────────────────────────────
        self.phase = "build_context"
        self.comic_context = self._build_comic_context()
        display_comic_context(self.comic_context)

        print(f"\n  {Colors.BOLD}Does this look right?{Colors.END}")
        print_list_item(1, "Yes — save context and proceed to Stage 2 (page preprocessing)")
        print_list_item(2, "No — start over with a different prompt")
        choice = _stage1_ui.get_user_input("Choose [1/2]")

        if choice == "2" or choice.lower() in ("no", "n", "start over", "restart"):
            print_agent("No problem. Let's start fresh.")
            self.messages = []
            self.analysis = None
            self.wiki_plot = None
            self.wiki_url = None
            self.comic_context = None
            new_prompt = _stage1_ui.get_user_input("Describe a different comic book event")
            return self.run(new_prompt)

        self.phase = "done"
        return self.comic_context

    def _build_comic_context(self) -> dict:
        """Assemble the final comic_context.json payload from collected data."""
        analysis = self.analysis or {}
        parsed = analysis.get("parsed_input", {})
        matches = analysis.get("potential_matches", [])
        best = matches[0] if matches else {}

        return {
            "status": "ready",
            "user_prompt": self.user_prompt,
            "title": best.get("title", "") or parsed.get("event_or_story", "Unknown"),
            "series": best.get("series", "") or parsed.get("series_guess", ""),
            "issues": best.get("issues", ""),
            "year": best.get("year", ""),
            "writer": best.get("writer", ""),
            "artist": best.get("artist", ""),
            "publisher": parsed.get("publisher_guess", ""),
            "era": parsed.get("era_guess", ""),
            "characters": parsed.get("characters_identified", []),
            "batcave_url": analysis.get("batcave_url", "") or best.get("batcave_url", ""),
            "wiki_url": self.wiki_url or "",
            "plot_summary": self.wiki_plot or "",
            "confidence": parsed.get("confidence", "low"),
            "raw_analysis": analysis,
        }

    def _get_comic_title(self) -> str:
        if not self.analysis:
            return "unknown comic"
        matches = self.analysis.get("potential_matches", [])
        if matches:
            return matches[0].get("title", "") or matches[0].get("series", "")
        parsed = self.analysis.get("parsed_input", {})
        return parsed.get("event_or_story", "") or parsed.get("series_guess", "unknown comic")

    def _extract_wiki_data_from_messages(self):
        """Scan conversation messages for fetch_wiki tool results (OpenAI role=tool format)."""
        import json
        for msg in reversed(self.messages):
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
