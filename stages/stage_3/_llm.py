"""Shared OpenRouter chat-completions helper with multi-model fallback for Stage 3."""
import threading
import time
from typing import Callable

from openai import OpenAI, RateLimitError, APITimeoutError

from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, LLM_MODELS
from stages.stage_2.vlm_extract import _is_rate_limited, _detect_inline_rate_limit
from stages._claude_sdk import sdk_available, sdk_complete, CLAUDE_SDK_MODEL


# Per-request timeout (seconds). Without it, a free-tier model that accepts the TCP
# connection but never streams the response body blocks forever (the SDK applies no
# read deadline here). 90s matches Stage 2's _BATCH_TIMEOUT_S — long enough for slow
# reasoning models, short enough to fail fast so call_with_chain falls through to the
# next model. See stages/stage_2/vlm_extract._BATCH_TIMEOUT_S.
_REQUEST_TIMEOUT_S = 90


_client_singleton: OpenAI | None = None


def _client() -> OpenAI:
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = OpenAI(
            api_key=OPENROUTER_API_KEY,
            base_url=OPENROUTER_BASE_URL,
            default_headers={
                "HTTP-Referer": "https://github.com/comic-video-pipeline",
                "X-Title": "Comic Video Pipeline",
            },
        )
    return _client_singleton


def _call_once(client: OpenAI, model: str, system: str, user: str, max_tokens: int) -> str:
    # max_retries=0: let call_with_chain own the retry/fallback policy. Without this
    # the SDK silently retries a stalled request up to 2x more (≈ 3×timeout) before
    # raising, which defeats the timeout.
    resp = client.with_options(timeout=_REQUEST_TIMEOUT_S, max_retries=0).chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def _call_with_deadline(client: OpenAI, model: str, system: str, user: str, max_tokens: int) -> str:
    """Run _call_once under a HARD wall-clock deadline.

    httpx's per-read timeout (set in _call_once) is not enough on its own: OpenRouter
    sends keep-alive bytes while a slow free-tier request is queued/generating, and each
    byte resets httpx's read timer — so a stalled request can run effectively forever
    even with timeout set. We run the call in a daemon thread and abandon it after
    _REQUEST_TIMEOUT_S. The orphaned thread eventually unwinds (its own httpx timeout
    fires once the keep-alive stops) or dies with the process; meanwhile call_with_chain
    falls through to the next model. A fresh daemon thread per call (not a bounded pool)
    avoids worker-exhaustion deadlock when several models stall in one run.
    """
    box: dict = {}

    def _run():
        try:
            box["value"] = _call_once(client, model, system, user, max_tokens)
        except BaseException as exc:  # propagate to caller thread verbatim
            box["error"] = exc

    t = threading.Thread(target=_run, name=f"llm-{model[:24]}", daemon=True)
    t.start()
    t.join(_REQUEST_TIMEOUT_S)
    if t.is_alive():
        raise TimeoutError(f"hard deadline {_REQUEST_TIMEOUT_S}s exceeded for {model}")
    if "error" in box:
        raise box["error"]
    return box.get("value", "")


def call_with_chain(
    *,
    system: str,
    user: str,
    models: list[str] | None = None,
    max_tokens: int = 2000,
    progress: Callable[[str], None] | None = None,
    label: str = "llm",
    validator: Callable[[str], bool] | None = None,
) -> tuple[str, str]:
    """Call the LLM chain. Returns (content, model_used). Raises if every model fails.

    If `validator(content)` is provided and returns False, the response is
    rejected and the chain advances to the next model. Use this to guard against
    models that leak chain-of-thought reasoning as text instead of returning the
    requested JSON shape.
    """
    chain = list(models) if models else list(LLM_MODELS)
    if not chain:
        raise RuntimeError(f"[{label}] no models configured")
    log = progress or (lambda _msg: None)

    # Fix A: route ONLY the writer (phase C `write` + the wiki-retry rewrite)
    # through the Claude Agent SDK; outline/glossary/fidelity/wiki-check stay on
    # the OpenRouter chain. This cuts claude calls from ~13 to ~1/pass so the
    # subscription rate window isn't exhausted mid-run (which made later SDK
    # calls queue → time out → fall back). The OpenRouter `chain` is still the
    # fallback for the writer when the SDK is unavailable. VLM is never routed here.
    # Writer phases + the Stage-5 panel-judge route through the Claude SDK (Opus);
    # outline/glossary/fidelity/wiki-check stay on the OpenRouter chain. (Judge
    # prompts are tiny, so the SDK answers fast even under concurrent load.)
    _use_sdk = (label == "write" or label.startswith("retry") or label == "panel-judge")
    if _use_sdk and sdk_available():
        log(f"[{label}] via claude SDK ({CLAUDE_SDK_MODEL})")
        sdk_out = sdk_complete(system, user, log=log)
        ok = bool(sdk_out) and not _detect_inline_rate_limit(sdk_out or "")
        if ok and validator is not None:
            try:
                ok = bool(validator(sdk_out))
            except Exception:
                ok = False
        if ok:
            log(f"[{label}] claude SDK returned {len(sdk_out)} chars")
            return sdk_out, f"claude-sdk:{CLAUDE_SDK_MODEL}"
        log(f"[{label}] claude SDK unusable — falling back to OpenRouter chain")

    client = _client()
    total = len(chain)
    errors: list[str] = []

    for idx, model in enumerate(chain, start=1):
        log(f"[{label}] try {idx}/{total} model={model}")
        content: str | None = None
        try:
            content = _call_with_deadline(client, model, system, user, max_tokens)
        except Exception as exc:
            if _is_rate_limited(exc):
                log(f"[{label}] rate-limited on {model} — falling back")
                errors.append(f"{model}: rate_limited ({type(exc).__name__})")
                continue
            if isinstance(exc, (APITimeoutError, TimeoutError)):
                # Stalled provider (accepted the connection, never sent the body) — caught
                # either by httpx (APITimeoutError) or our hard wall-clock deadline
                # (TimeoutError). Retrying the same model just stalls again — fall through.
                log(f"[{label}] {model} timed out after {_REQUEST_TIMEOUT_S}s — "
                    f"falling back (stalled provider, no same-model retry)")
                errors.append(f"{model}: timeout_{_REQUEST_TIMEOUT_S}s")
                continue
            log(f"[{label}] {model} transient error: {type(exc).__name__} — retrying once")
            time.sleep(2)
            try:
                content = _call_with_deadline(client, model, system, user, max_tokens)
            except Exception as exc2:
                if _is_rate_limited(exc2):
                    log(f"[{label}] rate-limited on {model} (retry) — falling back")
                    errors.append(f"{model}: rate_limited_retry ({type(exc2).__name__})")
                else:
                    log(f"[{label}] {model} failed twice: {type(exc2).__name__}")
                    errors.append(f"{model}: {type(exc2).__name__}: {str(exc2)[:160]}")
                continue

        if _detect_inline_rate_limit(content):
            log(f"[{label}] rate-limited on {model} (inline error body) — falling back")
            errors.append(f"{model}: rate_limited_inline")
            continue

        if not content or not content.strip():
            log(f"[{label}] {model} returned empty — falling back")
            errors.append(f"{model}: empty_content")
            continue

        if validator is not None:
            try:
                ok = bool(validator(content))
            except Exception as vexc:
                log(f"[{label}] validator raised {type(vexc).__name__} on {model} — treating as fail")
                ok = False
            if not ok:
                preview = content.strip().splitlines()[0][:120] if content.strip() else "(empty)"
                log(f"[{label}] {model} response failed validator (no usable JSON) — falling back. "
                    f"First line: {preview!r}")
                errors.append(f"{model}: validator_failed")
                continue

        log(f"[{label}] {model} returned {len(content)} chars")
        return content, model

    raise RuntimeError(f"[{label}] all {total} models exhausted: {' | '.join(errors)}")
