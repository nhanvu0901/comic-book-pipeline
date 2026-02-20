"""
Stage 1: Script Generator — Interactive Comic Book Expert Agent

A multi-turn conversational agent powered by GLM-4.7 that:
1. ANALYZES the user's prompt (identifies event, characters, era, ambiguities)
2. SEARCHES the web if needed (recent comics, issue verification)
3. ASKS clarifying questions (vague prompts, multiple matches, missing details)
4. PRESENTS an outline for CONFIRMATION before writing
5. GENERATES the final structured script only after user approval
"""
from .agent import ScriptAgent
from .cli import main

__all__ = ["ScriptAgent", "main"]
