"""
ScriptAgent — multi-turn conversational agent for comic book script generation.
Manages the flow: analysis → clarification → confirmation → generation.
"""
from .ui import Colors, print_agent, print_warning, print_error, print_list_item, get_user_input
from .llm import call_llm, extract_json, load_system_prompt, create_client
from .display import display_analysis, display_confirmation, display_script_summary
from . import tools as _tools


class ScriptAgent:
    """
    Multi-turn conversational agent for comic book script generation.
    Phases: start → analysis → clarification → confirmation → generation → done
    """

    def __init__(self, api_key: str, base_url: str, model: str):
        self.client = create_client(api_key, base_url)
        self.model = model
        self.system_prompt = load_system_prompt()
        self.messages = []
        self.phase = "start"
        self.script = None
        self.analysis = None
        self.outline = None
        # Wire tools that need LLM access (paraphrase_query) and reset thinking session
        _tools.init(self.client, self.model)

    def send_to_llm(self, user_message: str) -> str:
        """Send a user message and get the LLM response text."""
        self.messages.append({"role": "user", "content": user_message})
        response_text, self.messages = call_llm(
            self.client, self.messages, self.system_prompt, self.model
        )
        return response_text

    def run(self, initial_prompt: str | None = None):
        """Main agent loop."""
        from .ui import print_header, print_error as _print_error
        from .tools.sequential_thinking import reset_session

        # Always start each run with a clean thinking session
        reset_session()

        print_header("STAGE 1: PanelNarrator — Comic Book Script Agent")

        print_agent(
            "Welcome! I'm PanelNarrator, your comic book expert. "
            "Tell me about a comic book event, story arc, or character moment "
            "and I'll craft a dramatic narration script for your video."
        )

        if not initial_prompt:
            print()
            print(f"  {Colors.DIM}Examples:{Colors.END}")
            print(f"  {Colors.DIM}  • \"The death of Gwen Stacy\"{Colors.END}")
            print(f"  {Colors.DIM}  • \"Invincible vs Omni-Man first fight\"{Colors.END}")
            print(f"  {Colors.DIM}  • \"One Piece Marineford War\"{Colors.END}")
            print(f"  {Colors.DIM}  • \"Batman Knightfall Bane breaks the bat\"{Colors.END}")
            initial_prompt = get_user_input("Describe a comic book event or story")

        if not initial_prompt:
            _print_error("No prompt provided.")
            return None

        # ─── Phase 1: Analysis ──────────────────────────────────────────────
        self.phase = "analysis"
        print(f"\n  {Colors.DIM}🧠 Analyzing your prompt...{Colors.END}")

        raw = self.send_to_llm(initial_prompt)
        data = extract_json(raw)

        if not data:
            print_agent(raw)
            return self._handle_freeform_response(raw)

        phase = data.get("phase", "")

        if phase == "analysis":
            self.analysis = data
            display_analysis(data)

            questions = data.get("questions_for_user", [])

            # Always verify details via web search before proceeding
            print(f"\n  {Colors.DIM}🌐 Searching for more details...{Colors.END}")
            search_prompt = (
                "You MUST use web_search now to do ALL of the following:\n"
                "1. Verify and enrich details — issue numbers, writer/artist credits, "
                "exact plot points, year, and specific page numbers where key moments occur.\n"
                "2. CRITICAL: Search for the batcave.biz URL for this comic. "
                "Search 'batcave.biz [series name]' and look at the URLs in the results "
                "for a pattern like batcave.biz/{id}-{slug}.html. "
                "Copy the full URL and include it as 'batcave_url' in your analysis.\n"
                "Search even if you're confident. Then respond with your updated analysis."
            )
            raw = self.send_to_llm(search_prompt)
            data = extract_json(raw)
            if data:
                phase = data.get("phase", "")
                if phase == "analysis":
                    self.analysis = data
                    display_analysis(data)
                    questions = data.get("questions_for_user", [])

            if questions:
                self._clarification_loop(questions)
            else:
                self._request_confirmation()

        elif phase == "confirmation":
            self.outline = data
            display_confirmation(data)
            self._confirmation_prompt()

        elif phase == "script":
            print_warning("Agent jumped directly to script generation. Showing result...")
            self.script = data
            display_script_summary(data)
            self.phase = "done"

        else:
            print_agent(raw)
            self._handle_freeform_response(raw)

        return self.script

    def _clarification_loop(self, initial_questions: list):
        """Handle the back-and-forth clarification with the user."""
        self.phase = "clarification"
        questions = initial_questions
        max_rounds = 5
        round_num = 0

        while questions and round_num < max_rounds:
            round_num += 1

            print(f"\n  {Colors.BOLD}❓ I need some details:{Colors.END}")
            for i, q in enumerate(questions, 1):
                print(f"     {Colors.BOLD}[{i}]{Colors.END} {q}")

            answer = get_user_input("Your answer (or 'skip' to let me decide)")

            if answer.lower() == "skip":
                answer = "Use your best judgment for all questions. Pick the most iconic/well-known version."

            print(f"\n  {Colors.DIM}🧠 Processing your answer...{Colors.END}")
            raw = self.send_to_llm(answer)
            data = extract_json(raw)

            if not data:
                print_agent(raw)
                more_input = get_user_input("Anything else to add? (or press Enter to continue)")
                if more_input:
                    raw = self.send_to_llm(more_input)
                    data = extract_json(raw)

            if data:
                phase = data.get("phase", "")

                if phase == "analysis":
                    self.analysis = data
                    display_analysis(data)
                    questions = data.get("questions_for_user", [])
                    if not questions:
                        self._request_confirmation()
                        return

                elif phase == "confirmation":
                    self.outline = data
                    display_confirmation(data)
                    self._confirmation_prompt()
                    return

                elif phase == "script":
                    self.script = data
                    display_script_summary(data)
                    self.phase = "done"
                    return

                else:
                    questions = data.get("questions_for_user", [])
                    if not questions:
                        self._request_confirmation()
                        return
            else:
                self._request_confirmation()
                return

        self._request_confirmation()

    def _request_confirmation(self):
        """Ask the LLM to produce the outline for confirmation."""
        self.phase = "confirmation"
        print(f"\n  {Colors.DIM}📋 Preparing story outline...{Colors.END}")

        raw = self.send_to_llm(
            "Great, I think you have enough information now. Please present the "
            "PHASE 2 CONFIRMATION outline with the scene breakdown so I can review "
            "it before you write the full script."
        )

        data = extract_json(raw)
        if data and data.get("phase") == "confirmation":
            self.outline = data
            display_confirmation(data)
            self._confirmation_prompt()
        elif data and data.get("phase") == "script":
            self.script = data
            display_script_summary(data)
            self._offer_revision()
        else:
            print_agent(raw)
            self._confirmation_prompt_fallback()

    def _confirmation_prompt(self):
        """Ask user to confirm, adjust, or regenerate the outline."""
        print(f"\n  {Colors.BOLD}What would you like to do?{Colors.END}")
        print_list_item(1, "Looks good — generate the full script")
        print_list_item(2, "Adjust something (add/remove scenes, change tone, etc.)")
        print_list_item(3, "Start over with a different event")

        choice = get_user_input("Choose [1/2/3] or type your feedback")

        if choice == "1" or choice.lower() in ("yes", "y", "ok", "good", "go", "confirm", "looks good"):
            self._generate_script()

        elif choice == "3" or choice.lower() in ("start over", "restart", "new"):
            print_agent("No problem! Let's start fresh.")
            self.messages = []
            new_prompt = get_user_input("Describe a different comic book event")
            self.run(new_prompt)

        else:
            feedback = choice if choice != "2" else get_user_input("What would you like to change?")
            print(f"\n  {Colors.DIM}📋 Adjusting outline...{Colors.END}")

            raw = self.send_to_llm(
                f"The user wants to adjust the outline. Their feedback: {feedback}\n\n"
                "Please present an updated PHASE 2 CONFIRMATION outline."
            )
            data = extract_json(raw)
            if data:
                if data.get("phase") == "confirmation":
                    self.outline = data
                    display_confirmation(data)
                elif data.get("phase") == "script":
                    self.script = data
                    display_script_summary(data)
                    self._offer_revision()
                    return
            else:
                print_agent(raw)

            self._confirmation_prompt()

    def _confirmation_prompt_fallback(self):
        """Fallback when outline wasn't proper JSON."""
        print(f"\n  {Colors.BOLD}Proceed with script generation?{Colors.END}")
        print_list_item(1, "Yes, generate the full script")
        print_list_item(2, "I want to adjust something first")

        choice = get_user_input("Choose [1/2] or type your feedback")

        if choice == "1" or choice.lower() in ("yes", "y", "ok"):
            self._generate_script()
        else:
            feedback = choice if choice != "2" else get_user_input("What should be different?")
            raw = self.send_to_llm(feedback)
            data = extract_json(raw)
            if data and data.get("phase") == "confirmation":
                self.outline = data
                display_confirmation(data)
                self._confirmation_prompt()
            else:
                print_agent(raw)
                self._generate_script()

    def _generate_script(self):
        """Ask the LLM to generate the final script."""
        self.phase = "generation"
        print(f"\n  {Colors.DIM}✍️  Generating full narration script...{Colors.END}")
        print(f"  {Colors.DIM}   This may take a moment...{Colors.END}")

        raw = self.send_to_llm(
            "The outline is confirmed. Please generate the PHASE 3 SCRIPT with full "
            "narration text, image search queries, visual descriptions, moods, and "
            "effects for every scene. IMPORTANT:\n"
            "1. For each scene, you MUST include 'source_issue' (e.g. '#121') and "
            "'source_page' (integer or array of integers for the exact page number(s) "
            "where this moment occurs in that issue).\n"
            "2. In comic_source, you MUST include 'batcave_url' — the full URL to "
            "the series page on batcave.biz (e.g. 'https://batcave.biz/6587-what-if-dark-venom-2023.html'). "
            "If you found it during web search, use that. Otherwise set it to empty string.\n"
            "Use web_search if needed to find precise page numbers. "
            "Return ONLY the JSON."
        )

        data = extract_json(raw)
        if data and ("scenes" in data or data.get("phase") == "script"):
            self.script = data
            self.script["status"] = "ready"
            if "phase" in self.script:
                del self.script["phase"]
            display_script_summary(data)
            self._offer_revision()
        else:
            print_warning("Couldn't parse the script JSON. Trying once more...")
            raw = self.send_to_llm(
                "Please output ONLY the JSON script with no other text. "
                "Every scene must have 'source_issue' and 'source_page' fields. "
                "Start with { and end with }."
            )
            data = extract_json(raw)
            if data:
                self.script = data
                self.script["status"] = "ready"
                display_script_summary(data)
                self._offer_revision()
            else:
                print_error("Failed to parse script after retry.")
                print(f"  {Colors.DIM}Raw response:{Colors.END}")
                print(raw[:500])
                self.script = {"status": "error", "message": "JSON parse failure", "raw": raw}

    def _offer_revision(self):
        """After script is generated, offer to revise."""
        self.phase = "done"
        print(f"\n  {Colors.BOLD}Happy with the script?{Colors.END}")
        print_list_item(1, "Yes — save and proceed to Stage 2")
        print_list_item(2, "Revise some scenes (change narration, tone, etc.)")

        choice = get_user_input("Choose [1/2] or type specific feedback")

        if choice == "1" or choice.lower() in ("yes", "y", "save", "ok", "done", "good"):
            return
        else:
            feedback = choice if choice != "2" else get_user_input("What should be revised?")
            print(f"\n  {Colors.DIM}✍️  Revising script...{Colors.END}")

            raw = self.send_to_llm(
                f"Please revise the script based on this feedback: {feedback}\n\n"
                "Return the complete updated PHASE 3 SCRIPT JSON. Remember to include "
                "'source_issue' and 'source_page' for every scene."
            )
            data = extract_json(raw)
            if data and "scenes" in data:
                self.script = data
                self.script["status"] = "ready"
                display_script_summary(data)
            else:
                print_agent(raw)

            self._offer_revision()

    def _handle_freeform_response(self, raw: str):
        """Handle cases where the LLM responds with plain text instead of JSON."""
        answer = get_user_input("Your response")
        if answer:
            raw2 = self.send_to_llm(answer)
            data = extract_json(raw2)
            if data:
                phase = data.get("phase", "")
                if phase == "analysis":
                    self.analysis = data
                    display_analysis(data)
                    questions = data.get("questions_for_user", [])
                    if questions:
                        self._clarification_loop(questions)
                    else:
                        self._request_confirmation()
                elif phase == "confirmation":
                    self.outline = data
                    display_confirmation(data)
                    self._confirmation_prompt()
                elif phase == "script":
                    self.script = data
                    display_script_summary(data)
                    self._offer_revision()
            else:
                print_agent(raw2)
                self._handle_freeform_response(raw2)

        return self.script
