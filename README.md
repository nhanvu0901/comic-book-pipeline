# Comic Book Video Pipeline

Turn comic book story prompts into short narrated videos.

## Current state

Stage 1 (script generator) works. Stages 2+ are being rebuilt from scratch.

```
Stage 1: Script Generator    [WORKING]
  OpenRouter agent (OpenAI-compatible chat/completions + native tool use)
  with web_search, fetch_wiki, analyze_story, paraphrase_query, sequential_thinking tools.
  Output: projects/<slug>/script.json (scene-by-scene narration plan).

Stage 2+: To be rebuilt
  Comic page download + OCR/VLM + TTS (Cartesia) + subtitles + video.
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env     # then fill in OPENROUTER_API_KEY and TAVILY_API_KEY
```

Get an OpenRouter key at https://openrouter.ai/keys. The default model is
`anthropic/claude-haiku-4-5`; override with `OPENROUTER_MODEL` in `.env` (see
`.env.example` for alternatives).

## Run Stage 1

```bash
python -m stages.stage_1 "The death of Gwen Stacy in Amazing Spider-Man"
```

Output lands in `projects/<slug>/script.json`.

## Project layout

```
comic-video-pipeline/
├── config.py                       # API keys, paths, scraper settings
├── requirements.txt
├── stages/
│   └── stage_1/                    # Script generation agent
│       ├── agent.py                # 6-phase agent loop
│       ├── llm.py                  # OpenAI SDK client + tool-call loop
│       ├── cli.py                  # Entry point
│       └── tools/                  # web_search, fetch_wiki, analyze_story, paraphrase_query, sequential_thinking
├── utils/
│   └── comic_scraper/              # batcave.biz page downloader (nodriver + CDP)
├── templates/
│   └── system_prompt.txt           # PanelNarrator persona + phase schemas
├── docs/                           # Reference docs (stage_1_flow, claude-tool-use)
└── projects/                       # Per-project output folders
```
