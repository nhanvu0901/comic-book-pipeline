"""
Stage 2: Page preprocessing.

Given a project's comic_context.json, scrape the referenced comic pages and
enrich each one with: panel bounding boxes (YOLO), text extraction + speaker
attribution + panel descriptions + page summary + story-page classification (VLM).

Writes one JSON per page to projects/<slug>/preprocessed/page_NN.json, keyed
by SHA-256 of the image bytes for idempotent re-runs.
"""
from .pipeline import preprocess_project
from .cli import main

__all__ = ["preprocess_project", "main"]
