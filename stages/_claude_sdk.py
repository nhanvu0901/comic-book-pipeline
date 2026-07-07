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
import os
import threading
import time
from pathlib import Path

# Sonnet 4.6 — Opus 4.8 reasoned >330s before the first token on the big write prompt
# (measured), blowing every timeout; sonnet reasons far less (~90s on a 22K prompt) so
# the SDK writer completes well within the deadline. Same Claude subscription, good prose.
# NOTE (2026-07-01): tried claude-sonnet-5 — the SDK calls worked, but it used more agentic
# turns per completion and the glossary phase blew the max_turns cap; reverted to 4-6.
# Retry later via `CLAUDE_SDK_MODEL=claude-sonnet-5` (raise the sdk_complete max_turns too).
CLAUDE_SDK_MODEL = os.getenv("CLAUDE_SDK_MODEL", "claude-sonnet-4-6")
# Hard wall-clock deadline per completion. MEASURED: Opus 4.8 spends ~135s of
# reasoning BEFORE the first token on the complex narration WRITE task (then
# streams the output in seconds) — a simple prompt is ~16s. 180s was too tight
# and cut the writer off mid-think → 330s gives Opus room to finish (~140-220s
# typical). Small calls (panel-judge) still return in seconds, well under this.
_SDK_TIMEOUT_S = 330

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


# Transient (server-side) failures worth retrying in-process: 529 Overloaded and
# per-minute 429. Backoff seconds before retry 1/2/3 (529 wants a bigger first wait).
_TRANSIENT_RETRIES = 3
_TRANSIENT_BACKOFF_S = (5, 12, 25)


def _collect(
    system: str, user: str, model: str,
    *, allowed_tools: list[str] | None = None, max_turns: int = 2,
) -> dict:
    """Async query → STRUCTURED outcome dict. Reads the SDK's structured signals
    (RateLimitEvent.rate_limit_info, ResultMessage.api_error_status) rather than
    string-matching the assistant text — which false-positived whenever a story
    legitimately used words like "overloaded"/"capacity"/"rate limit". Runs inside
    a worker thread's own event loop (anyio.run), so it never collides with a
    caller loop. Returns: {text, rl_status, resets_at, api_error_status, is_error,
    subtype}."""
    import anyio
    from claude_agent_sdk import query, ClaudeAgentOptions
    from claude_agent_sdk.types import AssistantMessage, TextBlock, StreamEvent

    async def _run() -> dict:
        options = ClaudeAgentOptions(
            model=model,
            system_prompt=system or None,
            # thinking disabled → first token ~7s (adaptive thinking reasoned for
            # MINUTES and blew timeouts). Needs max_turns>=2; research needs more.
            # (If switching to claude-sonnet-5, raise this — it uses more agentic turns
            # per completion and blew the cap of 2 on the glossary phase.)
            thinking={"type": "disabled"},
            max_turns=max_turns,
            allowed_tools=allowed_tools if allowed_tools is not None else [],
            include_partial_messages=True,  # stream deltas (avoids buffered hang)
        )
        raw = ""
        out = {"text": "", "rl_status": None, "resets_at": None,
               "api_error_status": None, "is_error": False, "subtype": None}
        async for message in query(prompt=user, options=options):
            cls = type(message).__name__
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
            elif cls == "RateLimitEvent":
                info = getattr(message, "rate_limit_info", None)
                if info is not None:
                    out["rl_status"] = getattr(info, "status", None)
                    out["resets_at"] = getattr(info, "resets_at", None)
            elif cls == "ResultMessage":
                out["api_error_status"] = getattr(message, "api_error_status", None)
                out["is_error"] = bool(getattr(message, "is_error", False))
                out["subtype"] = getattr(message, "subtype", None)
                if not raw:
                    r = getattr(message, "result", None)
                    if isinstance(r, str):
                        raw = r
        out["text"] = raw
        return out

    return anyio.run(_run)


def _classify(res: dict) -> tuple[str, str]:
    """('ok' | 'transient' | 'cap' | 'empty', detail) from a _collect result.
    'cap' = real account usage limit (don't retry now); 'transient' = 529/per-min
    429 (retry with backoff)."""
    rl = res.get("rl_status")
    api = res.get("api_error_status")
    text = (res.get("text") or "").strip()
    if rl == "rejected":
        return "cap", f"account rate-limit REJECTED (resets_at={res.get('resets_at')})"
    if api == 529:
        return "transient", "529 overloaded (server-side)"
    if api == 429:
        # 429 without a 'rejected' RateLimitEvent → per-minute bucket → transient
        return "transient", "429 per-minute rate-limit"
    if res.get("is_error") and not text:
        return "transient", f"error subtype={res.get('subtype')}"
    if not text:
        return "empty", "no text returned"
    return "ok", ""


def _attempt(
    system: str, user: str, model: str, timeout: int,
    *, allowed_tools: list[str] | None = None, max_turns: int = 2,
) -> dict:
    """One threaded SDK attempt with a hard wall-clock deadline. Returns the
    _collect dict, or {'_timeout': True} / {'_err': exc}. The orphaned thread on
    timeout dies with the process; we never block the caller past `timeout`."""
    box: dict = {}

    def _runner():
        try:
            box["out"] = _collect(system, user, model,
                                  allowed_tools=allowed_tools, max_turns=max_turns)
        except BaseException as exc:  # never raise across threads
            box["err"] = exc

    t = threading.Thread(target=_runner, name="claude-sdk", daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return {"_timeout": True}
    if "err" in box:
        return {"_err": box["err"]}
    return box.get("out") or {}


def _complete_with_retry(
    system: str, user: str, model: str, timeout: int, _log,
    *, allowed_tools: list[str] | None = None, max_turns: int = 2,
) -> str | None:
    """Run the SDK with proper transient-retry. Returns the model text, or None on
    a real failure (timeout / account usage cap / persistent error / no SDK). Never
    raises — the caller falls back per FREE_MODEL policy. Retries transient server
    errors (529 / per-minute 429 / empty) AND flaky SDK exceptions (e.g. 'Claude
    Code returned an error result') with backoff; an account usage cap ('rejected')
    is surfaced immediately (retrying now is pointless)."""
    if not sdk_available():
        return None
    for i in range(_TRANSIENT_RETRIES + 1):
        res = _attempt(system, user, model, timeout,
                       allowed_tools=allowed_tools, max_turns=max_turns)
        if res.get("_timeout"):
            _log(f"[claude-sdk] timeout >{timeout}s — falling back")
            return None
        if "_err" in res:
            # Flaky SDK/CLI exceptions ('error result: success', decode errors,
            # transport blips) are usually transient — retry with backoff rather
            # than killing the whole stage on the first one.
            msg = str(res["_err"])
            detail = f"exception {type(res['_err']).__name__}: {msg[:120]}"
            # 'Reached maximum number of turns' is DETERMINISTIC, not flaky: the
            # agent genuinely exhausted its turn budget (e.g. looping WebSearch on
            # a comic with no single plot source, like an anthology issue). Retrying
            # re-burns the same turns for nothing — fall back immediately.
            if "maximum number of turns" in msg.lower():
                _log(f"[claude-sdk] {detail} — NOT retrying "
                     f"(ran out of turns; needs more turns or better grounding)")
                return None
            if i < _TRANSIENT_RETRIES:
                back = _TRANSIENT_BACKOFF_S[min(i, len(_TRANSIENT_BACKOFF_S) - 1)]
                _log(f"[claude-sdk] {detail} — retry {i + 1}/{_TRANSIENT_RETRIES} in {back}s")
                time.sleep(back)
                continue
            _log(f"[claude-sdk] {detail} — exhausted {_TRANSIENT_RETRIES} retries, falling back")
            return None
        kind, detail = _classify(res)
        if kind == "ok":
            return (res.get("text") or "").strip()
        if kind == "cap":
            _log(f"[claude-sdk] {detail} — NOT retrying (account usage cap)")
            return None
        # transient / empty → backoff + retry
        if i < _TRANSIENT_RETRIES:
            back = _TRANSIENT_BACKOFF_S[min(i, len(_TRANSIENT_BACKOFF_S) - 1)]
            _log(f"[claude-sdk] {detail} — retry {i + 1}/{_TRANSIENT_RETRIES} in {back}s")
            time.sleep(back)
            continue
        _log(f"[claude-sdk] {detail} — exhausted {_TRANSIENT_RETRIES} retries, falling back")
        return None
    return None


def sdk_complete(
    system: str,
    user: str,
    *,
    model: str = CLAUDE_SDK_MODEL,
    timeout: int = _SDK_TIMEOUT_S,
    log=None,
) -> str | None:
    """Single-shot text completion via the Claude Agent SDK (no tools). Returns the
    model's text, or None on any failure. Never raises — caller falls back."""
    return _complete_with_retry(system, user, model, timeout, log or (lambda _m: None))


# Web research can need several minutes (search → fetch → ... → write).
# max_turns must cover MULTIPLE source reads (the research prompt requires
# reading >= 2 sources with WebSearch/WebFetch and reconciling them) AND the
# final JSON write. At 12 the agent reliably ran out of turns mid-research and
# returned NOTHING — silently emptying the context for every comic that needs
# this fallback (all DC, whose Fandom api.php is Cloudflare-blocked, + any
# Fandom-miss Marvel). 28 leaves room for ~3-4 source reads + the write.
# Env-overridable: abstract "Why/How" research questions (Q&A archetype 2026-07-06)
# need more search→read→reconcile turns than a concrete feats question — 28 ran out
# mid-research. Raise per-run (SDK_WEB_MAX_TURNS=48 SDK_WEB_TIMEOUT_S=900) instead of
# hardcoding a new global that slows every simpler call.
_SDK_WEB_TIMEOUT_S = int(os.getenv("SDK_WEB_TIMEOUT_S", "540"))
_SDK_WEB_MAX_TURNS = int(os.getenv("SDK_WEB_MAX_TURNS", "28"))


def sdk_complete_web(
    system: str,
    user: str,
    *,
    model: str = CLAUDE_SDK_MODEL,
    max_turns: int = _SDK_WEB_MAX_TURNS,
    timeout: int = _SDK_WEB_TIMEOUT_S,
    log=None,
) -> str | None:
    """Like `sdk_complete` but the agent may use WebSearch/WebFetch to research
    the web before answering (allowed_tools enabled, higher max_turns, longer
    deadline). Returns the model's final text, or None on any failure. Never
    raises. Run standalone — the SDK throttles when another agent runs concurrently."""
    return _complete_with_retry(
        system, user, model, timeout, log or (lambda _m: None),
        allowed_tools=["WebSearch", "WebFetch"], max_turns=max_turns,
    )


# Vision judging: the agent reads local PNG files (panel crops) via the Read tool —
# the Claude model is vision-capable, so Read on an image lets it SEE the panel. Used by
# Stage 5's #6 panel rerank. max_turns must cover one Read per candidate image + the answer.
_SDK_VISION_TIMEOUT_S = 200
_SDK_VISION_MAX_TURNS = 14


def sdk_complete_vision(
    system: str,
    user: str,
    *,
    model: str = CLAUDE_SDK_MODEL,
    max_turns: int = _SDK_VISION_MAX_TURNS,
    timeout: int = _SDK_VISION_TIMEOUT_S,
    log=None,
) -> str | None:
    """Like `sdk_complete` but the agent may Read local image files (panel crops) so it
    can VISUALLY judge them — the user prompt lists absolute image paths. Returns the
    model's final text, or None on any failure. Never raises. Run standalone — the SDK
    throttles when another agent runs concurrently."""
    return _complete_with_retry(
        system, user, model, timeout, log or (lambda _m: None),
        allowed_tools=["Read"], max_turns=max_turns,
    )
