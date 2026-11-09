"""Comic vs. Show / Movie comparison mode — high-retention comparison shorts.

Targets Blue Ocean franchises (The Boys, Invincible, MCU, DCU) comparing what
mainstream viewers saw on screen with the unhinged/darker events in the comics.
"""
from __future__ import annotations

COMIC_VS_SHOW_MODE = "comic_vs_show"
BLUE_OCEAN_FRANCHISES = ("The Boys", "Invincible", "MCU", "DCU")


def is_comic_vs_show(comic_ctx: dict | None, state_data: dict | None, mode_override: str | None = None) -> bool:
    """Return True if comic_vs_show mode is active for this project."""
    if mode_override == COMIC_VS_SHOW_MODE:
        return True
    ctx = comic_ctx or {}
    st = state_data or {}
    if ctx.get("pipeline_mode") == COMIC_VS_SHOW_MODE or st.get("pipeline_mode") == COMIC_VS_SHOW_MODE:
        return True
    return bool(ctx.get("comic_vs_show") or st.get("comic_vs_show"))
