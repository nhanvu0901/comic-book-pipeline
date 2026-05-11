"""
Stage 1: Comic Identification + Wiki Context Gathering

A multi-turn conversational agent (OpenRouter-backed) that:
1. ANALYZES the user's prompt (identifies event, characters, era, ambiguities)
2. SEARCHES the web to verify details and find the batcave.biz URL
3. ASKS clarifying questions if the prompt is ambiguous
4. FETCHES verified plot text from fandom wikis

Output: comic_context.json containing title, series, issues, year, characters,
batcave_url, wiki_url, plot_summary, confidence.

Downstream stages consume comic_context.json:
  Stage 2 uses batcave_url to scrape pages, then VLM-preprocesses each page
  Stage 3 uses plot_summary + preprocessed pages to write ≤58s narration
  Stage 4 synthesizes TTS audio
  Stage 5 assembles the 9:16 video
"""
from .agent import ScriptAgent
from .cli import main

__all__ = ["ScriptAgent", "main"]
