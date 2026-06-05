"""Run text-LLM completions through the Claude Agent SDK (`claude_agent_sdk.query`)
instead of the OpenRouter API.

Learned from the auto_generated_lyric project (lyric_studio/core/engine.py): the
SDK keeps a *streaming* connection to the local Claude Code engine (same auth as
`~/.claude.json`), so it does NOT pay the cold-start / context-reload cost of
spawning `claude -p` per call — which is what throttled and timed-out the earlier
subprocess approach. Streaming also means a slow generation never "hangs silently"
behind a buffered read.

Every call falls back to the caller's OpenRouter path (returns None) when the SDK
is missing, not logged in, errors, times out, or the output signals a usage limit
— so a machine without the SDK still runs the pipeline. Never raises.
"""
import json
import threading
from pathlib import Path

# Sonnet 4.6 — Opus 4.8 reasoned >330s before the first token on the big write
# prompt (measured), blowing every timeout; sonnet reasons far less (~90s on a
# 22K prompt) so the SDK writer completes well within the deadline. Same Claude
# subscription, good prose quality.
CLAUDE_SDK_MODEL = "claude-sonnet-4-6"
# Hard wall-clock deadline per completion. MEASURED: Opus 4.8 spends ~135s of
# reasoning BEFORE the first token on the complex narration WRITE task (then
# streams the output in seconds) — a simple prompt is ~16s. 180s was too tight
# and cut the writer off mid-think → 330s gives Opus room to finish (~140-220s
# typical). Small calls (panel-judge) still return in seconds, well under this.
_SDK_TIMEOUT_S = 330

# Usage/rate-limit phrases (from auto_generated_lyric). If the model echoes one of
# these instead of real output, treat it as a limit hit and fall back.
_LIMIT_PHRASES = (
    "usage limit", "rate limit", "quota", "too many requests",
    "limit reached", "please slow down", "overloaded", "capacity",
    "try again later", "upgrade your plan",
)

try:
    import claude_agent_sdk as _sdk  # noqa: F401
    _SDK_IMPORTABLE = True
except Exception:
    _SDK_IMPORTABLE = False


def _logged_in() -> bool:
    """Login check by reading ~/.claude.json — instant, no subprocess."""
    try:
        data = json.loads((Path.home() / ".claude.json").read_text(encoding="utf-8"))
        return bool(data.get("oauthAccount"))
    except Exception:
        return False


def sdk_available() -> bool:
    return _SDK_IMPORTABLE and _logged_in()


def _collect(system: str, user: str, model: str) -> str:
    """Async query → concatenated assistant text. Runs inside a worker thread's
    own event loop (via anyio.run), so it never collides with a caller loop."""
    import anyio
    from claude_agent_sdk import query, ClaudeAgentOptions
    from claude_agent_sdk.types import AssistantMessage, TextBlock, StreamEvent

    async def _run() -> str:
        options = ClaudeAgentOptions(
            model=model,
            system_prompt=system or None,
            # ROOT-CAUSE FIX: the engine defaults to ADAPTIVE extended thinking —
            # on complex creative prompts the model silently reasons for MINUTES
            # before the first text token (measured: 85-330s+), which looked like
            # a hang and blew every timeout. Disabling thinking → first token ~7s,
            # full narration ~31s. thinking-disabled needs max_turns=2 (the engine
            # finalizes in a second message round; =1 errors "max turns reached").
            thinking={"type": "disabled"},
            max_turns=2,
            allowed_tools=[],       # pure text generation — no tools
            include_partial_messages=True,  # stream deltas (avoids buffered hang)
        )
        raw = ""
        async for message in query(prompt=user, options=options):
            if isinstance(message, StreamEvent):
                event = message.event
                if event.get("type") == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        raw += delta.get("text", "")
            elif isinstance(message, AssistantMessage):
                if not raw:  # fallback when no streamed deltas arrived
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            raw += block.text
        return raw

    return anyio.run(_run)


def sdk_complete(
    system: str,
    user: str,
    *,
    model: str = CLAUDE_SDK_MODEL,
    timeout: int = _SDK_TIMEOUT_S,
    log=None,
) -> str | None:
    """Single-shot completion via the Claude Agent SDK. Returns the model's text,
    or None on any failure (missing SDK, not logged in, error, timeout, usage
    limit). Never raises — the caller falls back to its OpenRouter path on None.

    The async streaming query runs in a daemon thread with its own event loop;
    `join(timeout)` is the hard wall-clock deadline. On timeout the thread is
    abandoned (dies with the process) and we fall back immediately.
    """
    _log = log or (lambda _m: None)
    if not sdk_available():
        return None

    box: dict = {}

    def _runner():
        try:
            box["out"] = _collect(system, user, model)
        except BaseException as exc:  # never raise across threads
            box["err"] = exc

    t = threading.Thread(target=_runner, name="claude-sdk", daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        _log(f"[claude-sdk] timeout >{timeout}s — falling back")
        return None
    if "err" in box:
        _log(f"[claude-sdk] error: {type(box['err']).__name__}: {str(box['err'])[:160]}")
        return None

    out = (box.get("out") or "").strip()
    if not out:
        _log("[claude-sdk] empty output — falling back")
        return None
    low = out.lower()
    if any(p in low for p in _LIMIT_PHRASES):
        _log("[claude-sdk] usage/rate limit signalled — falling back")
        return None
    return out
