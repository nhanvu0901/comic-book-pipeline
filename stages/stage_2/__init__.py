"""
Stage 2: Download + Page preprocessing.

Download stage: resolve chapters from batcave.biz, download page images.
Preprocess stage: Magi panel detect + VLM enrich each downloaded page.

Writes one JSON per page to projects/<slug>/preprocessed/page_NN.json, keyed
by SHA-256 of the image bytes for idempotent re-runs.
"""
from .download import download_comic, load_manifest
from .pipeline import preprocess_project
from .cli import main

__all__ = ["download_comic", "load_manifest", "preprocess_project", "main"]
