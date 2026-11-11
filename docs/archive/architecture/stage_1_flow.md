# Stage 1 — PanelNarrator: Full Flow Documentation

## What Stage 1 Does

Takes a plain-text comic event description from the user and produces a structured
`script.json` file containing narration text, image search queries, source page
references, and batcave.biz URL — ready for Stage 2 to pick images from.

---

## Entry Points

```
python -m stages.stage_1                          # interactive, no prompt
python -m stages.stage_1 "Invincible vs Omni-Man" # with initial prompt
python -m stages.stage_1 --project my_project     # resume existing project
```

**Files involved at this layer:**

```
stages/stage_1/
├── __main__.py      → calls cli.main()
├── cli.py           → argument parsing, calls ScriptAgent, saves output
├── agent.py         → the full conversation loop (phases)
├── llm.py           → HTTP calls to GLM API + tool-use loop
├── storage.py       → saves script.json + conversation_log.json
├── display.py       → pretty-prints analysis / outline / script in terminal
├── ui.py            → colored terminal input/output helpers
└── tools/
    ├── __init__.py          → TOOLS list + dispatch_tool()
    ├── web_search.py        → DuckDuckGo search
    ├── sequential_thinking.py → structured multi-step reasoning
    └── paraphrase_query.py  → generates N query variants via sub-LLM call
```

---

## High-Level Flow

```
User runs CLI
      │
      ▼
 cli.py: parse args, create ScriptAgent
      │
      ▼
 agent.run(initial_prompt)
      │
      ├──► PHASE 1: ANALYSIS
      │         │
      │         ├── send prompt to LLM
      │         ├── LLM uses tools (think → paraphrase → search)
      │         ├── LLM returns analysis JSON
      │         │
      │         ├──► Web Search round (always runs)
      │         │         LLM searches batcave.biz + comic details
      │         │
      │         └──► CLARIFICATION LOOP (if LLM has questions)
      │                   └── ask user → send to LLM → repeat
      │
      ├──► PHASE 2: CONFIRMATION
      │         │
      │         ├── LLM produces scene outline (8-14 scenes)
      │         ├── display to user
      │         └── user says: confirm / adjust / restart
      │
      ├──► PHASE 3: SCRIPT GENERATION
      │         │
      │         ├── LLM generates full script JSON
      │         └── user can revise
      │
      └──► cli.py: save script.json + conversation_log.json
```

---

## Phase 1: Analysis — Detailed

```
User input: "Invincible vs Omni-Man"
                    │
                    ▼
          agent.send_to_llm(prompt)
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│                    call_llm() in llm.py                 │
│                                                         │
│  client.messages.create(                                │
│      model=GLM,                                         │
│      system=system_prompt.txt,   ← PanelNarrator rules  │
│      tools=[                                            │
│          web_search,                                    │
│          sequential_thinking,                           │
│          paraphrase_query,                              │
│      ],                                                 │
│      messages=[{role:user, content: prompt}]            │
│  )                                                      │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼
         LLM stop_reason == "tool_use"?
                    │
          ┌─────────┴──────────────────┐
         YES                          NO
          │                            │
          ▼                            ▼
   ┌─────────────┐            extract text block
   │ TOOL LOOP   │            return to agent
   │ (≤15 iters) │
   └─────────────┘
          │
          │  LLM may batch multiple tool calls in one response:
          │  e.g. [sequential_thinking, paraphrase_query, web_search]
          │  all at once → executed in sequence → all results returned
          │
          ▼
   dispatch_tool(name, inputs)
          │
          ├── "sequential_thinking" ──► think()
          │       Records one reasoning step (narrative context /
          │       character motivation / emotional core / etc.)
          │       Returns: guidance for next step or full chain summary
          │
          ├── "paraphrase_query" ──────► paraphrase_query()
          │       Makes a MINI LLM call (max_tokens=300) asking the
          │       model to rephrase the search query N ways.
          │       e.g. "Invincible vs Omni-Man" →
          │         ["Invincible #12 fight scene",
          │          "Omni-Man kills Invincible Kirkman",
          │          "Image Comics Invincible father son battle"]
          │       Returns: {paraphrases: [...]}
          │
          └── "web_search" ───────────► web_search()
                  DuckDuckGo text search, max 10 results
                  Returns: [{title, url, snippet}, ...]

          tool results appended to messages as {role: user}
          → another client.messages.create() call
          → repeat until stop_reason == "end_turn"
```

**What the LLM typically does in Phase 1:**

```
Step 1: sequential_thinking (narrative context)
Step 2: sequential_thinking (character motivations)
Step 3: sequential_thinking (emotional impact)
Step 4: paraphrase_query("Invincible vs Omni-Man", n=3)
           → 3 search variants
Step 5: web_search("Invincible #12 Omni-Man fight Kirkman")
Step 6: web_search("Image Comics Invincible father son battle issue")
Step 7: web_search("Invincible vol 1 Omni-Man reveal issue number")
Step 8: sequential_thinking (is_final=true) → synthesize
─────────────────────────────────────────────────────────
→ returns analysis JSON
```

**Output JSON from Phase 1:**

```json
{
  "phase": "analysis",
  "parsed_input": {
    "event_or_story": "Invincible vs Omni-Man first fight",
    "characters_identified": ["Invincible", "Omni-Man"],
    "publisher_guess": "Image Comics",
    "series_guess": "Invincible",
    "era_guess": "2000s Modern",
    "confidence": "high"
  },
  "potential_matches": [...],
  "questions_for_user": ["Which fight specifically — #12 or the season finale?"]
}
```

---

## Forced Web Search (always runs after Phase 1 analysis)

Even if the LLM returns analysis without questions, the agent **always**
sends a second message forcing a web search round:

```python
search_prompt = (
    "1. Verify issue numbers, writer/artist, exact plot points via web_search\n"
    "2. CRITICAL: Search 'batcave.biz [series name]' and find the URL\n"
    "   pattern: batcave.biz/{id}-{slug}.html → store as 'batcave_url'\n"
    "Search even if you're confident. Respond with updated analysis."
)
```

This guarantees:
- `batcave_url` is always looked up
- Issue numbers and credits are verified against live web data

---

## Phase 1b: Clarification Loop

```
analysis has questions_for_user?
          │
         YES
          │
          ▼
  print questions to user
          │
          ▼
  get_user_input("Your answer or 'skip'")
          │
          ├── "skip" → sends "use your best judgment"
          │
          └── user answer → send_to_llm(answer)
                    │
                    ▼
            LLM responds with one of:
              "analysis"     → more questions → loop again (max 5 rounds)
              "confirmation" → jump to Phase 2 display
              "script"       → LLM skipped ahead (rare)
```

---

## Phase 2: Confirmation

```
agent._request_confirmation()
          │
          ▼
  send_to_llm("Present the PHASE 2 CONFIRMATION outline...")
          │
          ▼
  LLM returns:
  {
    "phase": "confirmation",
    "confirmed_source": { series, issues, year, writer, ... },
    "scene_outline": [
      { "scene_id": 1, "beat": "Setup...", "estimated_seconds": 8 },
      { "scene_id": 2, "beat": "Rising tension...", "estimated_seconds": 10 },
      ...  (8-14 scenes total)
    ],
    "total_scenes": 11,
    "estimated_duration_seconds": 90,
    "tone": "Dark, brutal, emotionally devastating",
    "message_to_user": "Here's my plan..."
  }
          │
          ▼
  display_confirmation() → prints outline in terminal
          │
          ▼
  _confirmation_prompt()
          │
  User chooses:
    [1] Looks good → _generate_script()
    [2] Adjust     → send feedback → updated outline → loop
    [3] Start over → clear messages, restart
```

---

## Phase 3: Script Generation

```
agent._generate_script()
          │
          ▼
  send_to_llm("Generate PHASE 3 SCRIPT JSON...")
  (prompt also reminds: include source_issue, source_page, batcave_url)
          │
          │  LLM may call web_search again here to find exact page numbers
          │
          ▼
  LLM returns full script JSON:

  {
    "phase": "script",
    "title": "Invincible vs Omni-Man: A Son's Shattered World",
    "comic_source": {
      "publisher": "Image Comics",
      "series": "Invincible",
      "issues": "#12-13",
      "year": 2004,
      "writer": "Robert Kirkman",
      "artist": "Cory Walker",
      "batcave_url": "https://batcave.biz/456-invincible-2003.html"
    },
    "scenes": [
      {
        "scene_id": 1,
        "narration": "Before the truth destroyed everything...",
        "source_issue": "#12",
        "source_page": 15,
        "image_search_queries": [
          "Invincible #12 Omni-Man Cory Walker comic panel fight",
          "Invincible Mark Grayson father reveal Image Comics",
          "Invincible 2004 Omni-Man brutality comic page"
        ],
        "visual_description": "Omni-Man standing over a battered Mark",
        "mood": "devastating",
        "effect": "slow_zoom_in"
      },
      ...
    ],
    "total_estimated_duration_seconds": 88
  }
          │
          ▼
  extract_json() → parse + validate
          │
  script["status"] = "ready"
          │
          ▼
  display_script_summary() → print scenes in terminal
          │
          ▼
  _offer_revision()
          │
  User: [1] Save → done
         [2] Revise → send feedback → LLM rewrites → loop
```

---

## Tool-Use Loop Detail (llm.py)

```
call_llm(client, messages, system, model)
          │
          ▼
  First API call
          │
          ▼
  ┌──────────────────────────────────────────────┐
  │   while stop_reason == "tool_use"            │
  │   AND iteration < 15:                        │
  │                                              │
  │     collect ALL tool_use blocks              │
  │     (LLM can batch multiple in one response) │
  │                                              │
  │     for each tool_use:                       │
  │         dispatch_tool(name, inputs)          │
  │         → collect result                     │
  │                                              │
  │     append to messages:                      │
  │       {role: assistant, content: tool_uses}  │
  │       {role: user, content: tool_results}    │
  │                                              │
  │     next API call                            │
  │     iteration += 1                           │
  └──────────────────────────────────────────────┘
          │
          ├── stop_reason == "end_turn"
          │       → extract text block → return
          │
          └── iteration >= 15 AND still tool_use
                  → "budget exhausted" tool results
                  → final API call WITHOUT tools param
                  → LLM forced to return text
                  → return
```

**Why the budget exhaustion fallback matters:**
The LLM sometimes gets on a search spiral — calling web_search 9+ times chasing
deeper details. Without the fallback, the loop exits and returns an empty string
(the last response has no text block, only tool_use blocks). The fallback forces
the LLM to write its final answer from whatever it already learned.

---

## Tools Detail

### web_search
```
Input:  { query: str, max_results: int (default 5, max 10) }
Engine: DuckDuckGo (ddgs library) — no API key required
Output: { results: [{title, url, snippet}, ...] }
```

### sequential_thinking
```
Input:  { thought: str, step_number: int, total_steps: int,
          branch: str, is_final: bool }

Each call → records one reasoning step in module-level _steps[]
  Non-final → returns: next aspect suggestion + "N steps remaining"
  Final     → returns: full chain as structured summary
              + "synthesize into your JSON response now"

Aspects cycled through:
  1. narrative context and story setup
  2. character motivations and relationships
  3. emotional core and central themes
  4. visual storytelling and iconic moments
  5. historical significance and era context
  6. cultural impact and lasting legacy
  7. pacing and scene structure

Purpose: forces the LLM to think from 7 angles before writing narration,
producing richer emotional beats than single-shot generation.
```

### paraphrase_query
```
Input:  { query: str, n: int (2-5), focus: str }
Output: { original: str, paraphrases: [str, ...], count: int }

Makes a MINI LLM call (max_tokens=300) to generate N diverse phrasings.
The LLM then calls web_search separately for each paraphrase.

Example:
  Input:  "death of Gwen Stacy"
  Output: [
    "Amazing Spider-Man 121 bridge scene Gil Kane",
    "Spider-Man girlfriend killed Green Goblin 1973",
    "Gwen Stacy George Washington Bridge comic death"
  ]

Why: A single query finds one slice of the web. Different phrasings
surface different pages, maximizing information coverage.
```

---

## Output Files

```
projects/{project_name}/
├── script.json           ← the main output (used by Stage 2)
└── conversation_log.json ← full LLM conversation for debugging
```

**project_name** is derived from:
1. `--project` flag if provided
2. `slugify(initial_prompt)` if prompt was given on CLI
3. `slugify(script["title"])` from the generated script title

`slugify()` → lowercase, spaces→underscores, strips special chars, max 60 chars
Example: `"Invincible vs Omni-Man"` → `"invincible_vs_omni-man"`

---

## JSON Parsing (extract_json)

The LLM sometimes wraps JSON in markdown code blocks or makes minor errors.
`extract_json()` handles this with three fallback strategies:

```
Strategy 1: find ```json ... ``` block → parse inner text
Strategy 2: find ``` ... ``` block     → parse inner text
Strategy 3: parse the whole raw string as JSON
Strategy 4: find first { ... last }    → parse that substring

Each strategy also attempts _fix_json() which repairs:
  - Bare year ranges:  2009-2010  →  "2009-2010"
  - Trailing commas:   [1, 2,]    →  [1, 2]
  - Python None:       None       →  null
```

---

## System Prompt Summary (templates/system_prompt.txt)

The system prompt defines the LLM's identity and ALL rules:

| Section | Purpose |
|---|---|
| Identity | PanelNarrator — comic historian + dramatic scriptwriter |
| Phase 1 rules | Analysis JSON schema, when to ask questions, confidence levels |
| Phase 2 rules | Confirmation outline schema, 8-14 scenes, 60-120s duration |
| Phase 3 rules | Script JSON schema with all fields |
| Series name rules | FULL unabbreviated official title always |
| batcave_url rules | Must search "batcave.biz [series]" and store full URL |
| Narration rules | Morgan Freeman voice, present tense, 15-30 words per scene |
| Source page rules | source_issue + source_page required per scene |
| Image query rules | Must include: series + issue# + artist + "comic panel" + visual element |
| Effect rules | Ken Burns effect per scene mood |
| Web search guidelines | When to search, good vs bad queries |
