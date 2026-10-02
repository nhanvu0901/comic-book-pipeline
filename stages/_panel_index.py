"""Dialog accessors shared by Stage 2, Stage 3, Stage 5 and the review gate.

A page's dialog comes in two shapes. The current schema nests it under each panel
(`panel["dialog"]`); old cached pages carry a flat page-level `text_blocks` list whose entries
point at their panel through `panel_index`. `panel_dialog` and `page_dialog` read either shape,
so a caller never branches on the schema.

DIALOG_TRUTH names the contract the dialog readers follow: Magi's pixel-OCR (the `ocr` field
on each block) is the authoritative text, and the batch VLM's `text` field is the fallback.
"""
from __future__ import annotations

import os


# DIALOG_TRUTH: Magi's pixel-OCR (the `ocr` field on each dialog block) is the AUTHORITATIVE
# dialog. The batch VLM fabricates the `text` field from story flow (real case: doom-rocket-
# raccoon p28 panel 1 pixels read "SO NOW WHAT DO WE DO?" but the VLM wrote "WE'VE REACHED THE
# BIG BANG"). The readers that need the words (Stage 2's page checks, Stage 3's dialog blocks,
# the review gate) therefore take `ocr` first and fall back to `text` only for a block with no
# OCR (old cached pages carry none); Stage 5 reads the same blocks for their bubble boxes.
# Stage 2 also cross-checks the two and flags a panel whose VLM text diverges from its OCR
# (_apply_dialog_truth_gate). Default ON; DIALOG_TRUTH=0/false/no turns that Stage 2 pass off.
DIALOG_TRUTH = os.getenv("DIALOG_TRUTH", "1").strip().lower() not in ("0", "false", "no", "")


def panel_dialog(panel: dict, page_text_blocks: list[dict] | None = None) -> list[dict]:
    """A panel's dialog lines. New schema: nested panel['dialog']. Old cached pages:
    filter the page-level text_blocks by panel_index (backward-compat)."""
    d = panel.get("dialog")
    if d is not None:
        return d
    _idx = panel.get("index", -999)
    idx = int(_idx) if _idx is not None else -999   # NB: `or` would turn index 0 into -999
    return [tb for tb in (page_text_blocks or [])
            if (int(tb.get("panel_index", -2)) if tb.get("panel_index") is not None else -2) == idx]


def page_dialog(page: dict) -> list[dict]:
    """ALL dialog on a page. New schema: flattened from panels[].dialog. Old cached
    pages: the page-level text_blocks (backward-compat)."""
    tb = page.get("text_blocks")
    if tb:   # non-empty page-level list = old cached schema; new comic output leaves it []
        return tb
    out: list[dict] = []
    for p in page.get("panels") or []:
        out.extend(p.get("dialog") or [])
    return out
