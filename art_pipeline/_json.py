"""Shared JSON extraction for LLM/VLM responses that may wrap the object in
prose or markdown fences."""
import json
import re


def extract_json(raw: str) -> dict | None:
    """Return the first {...} object parsed from `raw`, or None if absent/invalid."""
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None
