"""Shared OpenRouter chat-completions helper with multi-model fallback for Stage 3."""
import time
from typing import Callable

from openai import OpenAI, RateLimitError

from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, LLM_MODELS
from stages.stage_2.vlm_extract import _is_rate_limited, _detect_inline_rate_limit


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
    resp = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def call_with_chain(
    *,
    system: str,
    user: str,
    models: list[str] | None = None,
    max_tokens: int = 2000,
    progress: Callable[[str], None] | None = None,
    label: str = "llm",
) -> tuple[str, str]:
    """Call the LLM chain. Returns (content, model_used). Raises if every model fails."""
    chain = list(models) if models else list(LLM_MODELS)
    if not chain:
        raise RuntimeError(f"[{label}] no models configured")
    log = progress or (lambda _msg: None)
    client = _client()
    total = len(chain)
    errors: list[str] = []

    for idx, model in enumerate(chain, start=1):
        log(f"[{label}] try {idx}/{total} model={model}")
        content: str | None = None
        try:
            content = _call_once(client, model, system, user, max_tokens)
        except Exception as exc:
            if _is_rate_limited(exc):
                log(f"[{label}] rate-limited on {model} — falling back")
                errors.append(f"{model}: rate_limited ({type(exc).__name__})")
                continue
            log(f"[{label}] {model} transient error: {type(exc).__name__} — retrying once")
            time.sleep(2)
            try:
                content = _call_once(client, model, system, user, max_tokens)
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

        log(f"[{label}] {model} returned {len(content)} chars")
        return content, model

    raise RuntimeError(f"[{label}] all {total} models exhausted: {' | '.join(errors)}")
