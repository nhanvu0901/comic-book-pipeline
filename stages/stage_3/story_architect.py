"""Story Architect — advisory pre-write structure analysis (see
docs/superpowers/specs/2026-06-24-story-architect-design.md). Never raises."""
from __future__ import annotations
import json
import re
from typing import Callable

from config import ENABLE_STORY_ARCHITECT  # noqa: E402 – module-level for monkeypatch
from ._llm import call_with_chain  # noqa: E402 – module-level for monkeypatch

_VALID_STRUCTURE = {"linear", "framed_flashback", "nonlinear"}
_VALID_ROLE = {"protagonist", "antagonist", "supporting", "cameo"}
_VALID_MENTION = {"core", "supporting", "skip"}
_STOP = {
    "the", "a", "an", "of", "and", "who", "that", "with", "vol", "comic",
    "was", "his", "her", "him", "she", "are", "for", "but", "not", "you",
    "all", "one", "out", "its",
}


def _tokens(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (s or "").lower())
            if len(w) > 2 and w not in _STOP]


def _panel_character_index(story_pages: list[dict]) -> set[str]:
    """Lowercased significant tokens of every character NAME that appears in any
    panel's `characters[]` plus every word in panel descriptions — the deterministic
    signal of who/what is actually drawn (feeds visual_available)."""
    idx: set[str] = set()
    for pg in story_pages or []:
        for p in pg.get("panels", []) or []:
            for c in p.get("characters", []) or []:
                nm = c.get("name") if isinstance(c, dict) else c
                idx.update(_tokens(str(nm)))
            idx.update(_tokens(str(p.get("description") or "")))
    return idx


def _coerce_story_map(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    structure = str(raw.get("structure", "")).strip().lower()
    spine = str(raw.get("spine", "")).strip()
    if structure not in _VALID_STRUCTURE or not spine:
        return None
    chars = []
    for c in raw.get("characters", []) or []:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name", "")).strip()
        if not name:
            continue
        role = str(c.get("role", "")).strip().lower()
        mention = str(c.get("mention", "")).strip().lower()
        chars.append({
            "name": name,
            "role": role if role in _VALID_ROLE else "supporting",
            "visual_available": bool(c.get("visual_available", True)),
            "mention": mention if mention in _VALID_MENTION else "supporting",
            "note": str(c.get("note", "")).strip(),
        })
    return {
        "structure": structure,
        "telling_order": str(raw.get("telling_order", "")).strip(),
        "spine": spine,
        "characters": chars,
        "omit": [str(x).strip() for x in (raw.get("omit") or []) if str(x).strip()],
        "framing_notes": str(raw.get("framing_notes", "")).strip(),
        "confidence": str(raw.get("confidence", "")).strip().lower() or "medium",
    }


_SYSTEM = """You are a STORY ARCHITECT analysing a comic so a downstream writer can recap \
it for viewers who know NOTHING about it. Output a STRUCTURED MAP only — you do NOT write \
narration. Ground rules: the PLOT is ground-truth for WHAT HAPPENS; the PANEL DATA is \
ground-truth for WHAT IS ON A PANEL. Never trust a panel label blindly (a VLM may label \
every Batman-like figure just "Batman"); never invent beyond the plot.

Decide, per character, visual_available: TRUE only if that SPECIFIC character clearly has \
their own panel in the panel data. A character who exists in the PLOT but has no distinct \
panel (e.g. a villain the art never shows separately) is visual_available=false and should \
usually be mention="skip" or kept as an unseen/off-screen presence — flag this in \
framing_notes so the writer never implies an on-screen figure IS them.

mention: core (drives the through-line) | supporting (mention if it helps) | skip (low-impact \
/ no payoff / not shown). omit: subplots or details too minor for a short recap.

Output STRICT JSON only:
{"structure":"linear|framed_flashback|nonlinear","telling_order":"...","spine":"one sentence",
"characters":[{"name":"...","role":"protagonist|antagonist|supporting|cameo",
"visual_available":true,"mention":"core|supporting|skip","note":"..."}],
"omit":["..."],"framing_notes":"...","confidence":"high|medium|low"}"""


def analyze_story(comic_context: dict, story_pages: list[dict], *,
                  model: str | None = None,
                  progress: Callable[[str], None] | None = None) -> dict | None:
    """Advisory story map, or None (gated off / no plot / LLM fail / unparseable).
    Never raises."""
    if not ENABLE_STORY_ARCHITECT:
        return None
    log = progress or (lambda _m: None)
    plot = str(comic_context.get("plot_summary", "")).strip() \
        or str((comic_context.get("summary") or {}).get("story_arc", "")).strip()
    if not plot:
        log("[stage4] story-architect: no plot — skipping")
        return None
    from .write_script import _extract_json, _character_names
    from config import FIDELITY_LLM_MODELS

    seen = sorted(_panel_character_index(story_pages))[:120]
    chars_hint = ", ".join(_character_names(comic_context)) or "?"
    panel_descs = []
    for pg in (story_pages or [])[:40]:
        for p in pg.get("panels", []) or []:
            d = str(p.get("description") or "").strip()
            if d:
                panel_descs.append(d)
    user = (
        f"TITLE: {comic_context.get('title', '?')}\n"
        f"KNOWN CHARACTERS: {chars_hint}\n\n"
        f"PLOT (ground-truth for events):\n{plot[:3000]}\n\n"
        f"PANEL DESCRIPTIONS (ground-truth for what is drawn):\n"
        + "\n".join(f"- {d}" for d in panel_descs[:80])
        + f"\n\nTOKENS THAT APPEAR IN PANELS (appearance signal): {', '.join(seen)}\n\n"
        "Return the STRICT JSON story map now."
    )
    log("[stage4] phase A-1 — story architect…")
    chain = [model] if model else list(FIDELITY_LLM_MODELS)
    try:
        raw, _mdl = call_with_chain(system=_SYSTEM, user=user, models=chain,
                                    max_tokens=2000, progress=progress,
                                    label="story-architect",
                                    validator=lambda c: '"structure"' in c)
    except RuntimeError as exc:
        log(f"[stage4]   story-architect chain failed — skipping: {exc}")
        return None
    data = _extract_json(raw)
    out = _coerce_story_map(data) if isinstance(data, dict) else None
    if out is None:
        log("[stage4]   story-architect: unusable output — skipping")
        return None
    n_skip = sum(1 for c in out["characters"] if c["mention"] == "skip")
    n_novis = sum(1 for c in out["characters"] if not c["visual_available"])
    log(f"[stage4]   story-architect: {out['structure']}, {len(out['characters'])} chars "
        f"({n_novis} no-panel, {n_skip} skip), confidence={out['confidence']}")
    return out


def render_story_map_block(story_map: dict | None) -> str:
    """Compact additive prompt block for the outliner/writer. "" when no map."""
    if not story_map:
        return ""
    # telling_order is intentionally NOT rendered: beat order is decided
    # deterministically by _order_beats_canonical (causal). A soft prose
    # "telling order" here only conflicts with that authority and confuses the writer.
    conf = (story_map.get("confidence") or "medium").lower()
    lines = ["STORY MAP (structural guidance — honor it; plot stays ground-truth):",
             f"- structure: {story_map['structure']}"]
    lines.append(f"- spine: {story_map['spine']}")
    if story_map.get("framing_notes"):
        lines.append(f"- framing: {story_map['framing_notes']}")
    if story_map.get("characters"):
        lines.append("- characters:")
        for c in story_map["characters"]:
            if c["visual_available"]:
                vis = "on-panel"
            elif conf == "high":
                # Only assert "NO PANEL" as fact when the architect is confident —
                # a low/medium-confidence guess false-positived before (Batman Who Laughs).
                vis = "NO PANEL (do not show on-screen)"
            else:
                vis = "panel uncertain — verify in panel data before showing on-screen"
            lines.append(f"    • {c['name']} [{c['role']}] mention={c['mention']}, {vis}"
                         + (f" — {c['note']}" if c.get("note") else ""))
    if story_map.get("omit"):
        lines.append("- omit (skip for concision): " + "; ".join(story_map["omit"]))
    return "\n".join(lines) + "\n\n"
