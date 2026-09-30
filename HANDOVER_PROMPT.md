# AGENT HANDOFF PROMPT — STAGE REORDER & GEMINI PROMPT GROUNDING FIX

> **Purpose:** Handover document & system prompt for an incoming AI agent picking up the `comic-book-pipeline` codebase.
> **Date:** 2026-09-30
> **Repository:** `comic-book-pipeline` (Branch: `feat/scout-single-selection-flow`)
> **Live Environment:** Windows Server `winbox-lan` (`192.168.2.63:8550`)

---

## 1. Context & Global Operating Rules

1. **User Communication Language:**
   - **ALWAYS respond in Vietnamese** to Master, regardless of what language Master uses.
2. **Core Architectural Principle (Don't Overfit):**
   - See `AGENTS.md`. Never hardcode a specific comic's name, issue number, or character in the shared code logic (`RULE[AGENTS.md]`).
   - Build general mechanisms: aspect-ratio rules, thresholds, data-driven schemas, score functions.
3. **Environment Setup:**
   - **Local Dev (Mac):** Workspace at `/Users/nhanvu/Documents/code/comic-book-pipeline`.
   - **Render / Execution Box (Windows):** `winbox-lan` (`192.168.2.63`), repo at `D:\code\comic-book-pipeline`.
   - **Python Environment on Windows:** `D:\code\comic-book-pipeline\.venv\Scripts\python.exe`.
   - **UI Server:** Flet web app serving on port `8550` (`run_ui_lan.bat`).

---

## 2. What Was Accomplished (The Situation Just Fixed)

### A. Stage Reordering (2, 3, 4)
Master requested moving Narration Script to happen **before** downloading and preprocessing comic pages:
- **Old Order:** Stage 1 (Scout) → Stage 2 (Download) → Stage 3 (Preprocess) → Stage 4 (Narration Script) → Stage 5 (Review Beats)
- **New Order (Current):**
  - **Stage 1 (Step 1):** Research Scout (`ui/screens/s1_research_scout.py`)
  - **Stage 2 (Step 2):** Narration Script (`ui/screens/s3_narrate.py`)
  - **Stage 3 (Step 3):** Download Comic (`ui/screens/s2_download.py`)
  - **Stage 4 (Step 4):** Preprocess Pages (`ui/screens/s2_preprocess.py`)
  - **Stage 5 (Step 5):** Review Beats (`ui/screens/s_review_gate.py`)
  - **Stage 6 (Step 6):** TTS Audio (`ui/screens/s4_tts.py`)
  - **Stage 7 (Step 7):** Review & Edit (`ui/screens/s6_review.py`)
  - **Stage 8 (Step 8):** Final Video (`ui/screens/s5_video.py`)

### B. Gemini Writer Prompt Bug ("Nonsense Prompt")
- **The Issue:** When clicking "Copy Gemini Prompt" in Stage 2 for a single-moment comic project (such as `batman_5_2026`), the app dumped `comic_context.json` into `prompts/gemini_qa_writer_template.md` (a Q&A multi-item countdown prompt). It instructed Gemini to write a countdown listicle ("THE QUESTION: Batman #5", `item_number -> 1`, "1 paragraph per item in download order"). Pasting that into Gemini generated absurd listicle scripts.
- **The Grounded Fix:**
  - `stages/stage_3/gemini_prompt.py::generate_gemini_writer_prompt()` now checks `pipeline_mode`:
    - If `micro_moment`: Uses `prompts/gemini_micro_moment_writer.md`.
    - If `explore_answer` (or multi-item Q&A): Uses `prompts/gemini_qa_writer_template.md`.
  - Added `_find_micro_scout_candidate()`: Extracts candidate research data (character, what visibly happens, summary, claim citation quote, evidence source URL, verdict) from `scout_candidate.json` or `research_sessions/<session_id>/session.json`, and fills into `{{MOMENT}}` and `{{SCOUT_JSON}}`.
  - Updated `stages/research_scout/project_factory.py::_create_micro_project()` to immediately persist `scout_candidate.json` and embed it in `comic_context.json` when the project is created in Stage 1.
  - Updated `ui/screens/s3_narrate.py`: The Right Column "PROJECT CONTEXT" box now displays Mode, Comic & Issue, Character, and Target Moment even when `answer_context.json` is not present.

### C. Re-anchor Script to Pages (`reanchor_narration_to_pages`)
- **The Issue:** In the new stage order, the script is drafted in Stage 2 when comic pages have not yet been downloaded. Scenes and beats initially default `page_ref` to 1.
- **The Fix:**
  - At the end of Stage 4 (Preprocess Pages), `reanchor_narration_to_pages(project_name)` is invoked before transitioning to Stage 5.
  - Also added a defensive re-anchor call at the start of `stages/review_gate.py::build_candidates()` so candidates are always scored against the re-anchored story pages.
  - In `micro_moment` and `recap` modes: filters `is_story_page` (skipping cover/ads), anchors intro to first story page, outro to last story page, and distributes body scenes across story pages. Pairs `zip(beats, scenes)` directly so all beats match their scenes instead of being stuck on page 1.
  - In `_read_script()`: fixed a bug where brainstorm hook options in the preamble triggered `ScriptMappingError` even when `FINAL SCRIPT` was present.

---

## 3. Files Modified & Added

| File | Changes Made |
|---|---|
| `stages/stage_3/gemini_prompt.py` | Prompt dispatcher (`micro_moment` vs `explore_answer`), `_find_micro_scout_candidate`, `reanchor_narration_to_pages` beat pairing, `_read_script` preamble fix. |
| `stages/research_scout/project_factory.py` | Immediate caching of `scout_candidate.json` and fixing `characters` list during project creation. |
| `stages/review_gate.py` | Added defensive `reanchor_narration_to_pages` call before building review candidates. |
| `ui/screens/s3_narrate.py` | Fallback in right column to display comic context and target moment when `answer_context.json` is missing. |
| `prompts/gemini_qa_writer_template.md` | Minor copy polish reflecting that narration is written before download. |
| `tests/test_gemini_micro_moment_prompt.py` | **NEW**: Unit tests for micro-moment prompt generation and narration re-anchoring. |

---

## 4. Verification & Health Checks

Run these commands to verify pipeline integrity:

```bash
# 1. Run all Gemini narration & prompt tests
pytest tests/test_gemini_micro_moment_prompt.py tests/test_gemini_script_order_lock.py -v

# 2. Run UI stage navigation & download screen tests
pytest tests/test_s2_download_ui.py tests/test_ui_navigation.py -v

# 3. Run Stage 5 Review Gate tests
pytest tests/test_review_gate.py -v
```

All suites are passing (31/31 in script order lock, 2/2 in micro prompt, 26/26 in navigation/download, 45/45 in review gate).

---

## 5. How to Operate & Restart the Windows Server

When deploying changes to `winbox-lan`:

```bash
# 1. SSH and pull latest code
ssh winbox-lan "cd /d D:\\code\\comic-book-pipeline && git pull"

# 2. Terminate existing UI server
ssh winbox-lan "powershell -Command \"Get-Process python* -ErrorAction SilentlyContinue | Where-Object { \$_.CommandLine -like '*ui*' } | Stop-Process -Force\""

# 3. Launch UI server persistently via WMI (avoids OpenSSH session teardown)
ssh winbox-lan "powershell -Command \"Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='cmd.exe /c D:\\code\\comic-book-pipeline\\run_ui_lan.bat'; CurrentDirectory='D:\\code\\comic-book-pipeline'}\""

# 4. Verify HTTP 200 OK from Mac
curl -I http://192.168.2.63:8550/
```

---

## 6. What the Next Agent Should Keep in Mind

- **Stage Order Contract:** Always maintain `1: Scout -> 2: Narration -> 3: Download -> 4: Preprocess -> 5: Review Beats -> 6: TTS -> 7: Review -> 8: Video`.
- **Script Parsing Robustness:** Users copy/paste varying markdown from Gemini (with or without `FINAL SCRIPT`, with or without `HOOK OPTIONS`, with or without word count tables). Do not break lenient parsing in `_read_script()`.
- **Windows / Mac path handling:** Always use `pathlib.Path` or `os.path`. Do not hardcode `/` or `\` separators.
