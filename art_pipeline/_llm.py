"""Art text-LLM transport selector (user decision 2026-06-12).

`ART_FREE_MODEL` picks exactly ONE backend per call — the two are kept
strictly SEPARATE, there is NO cross-fallback:

  ART_FREE_MODEL = False (default)  → Claude Agent SDK only (`sdk_complete`,
      Sonnet 4.6, no tools). On SDK error / rate-limit it RAISES — we want the
      failure visible so its root cause gets fixed, not silently masked by the
      free OpenRouter chain.
  ART_FREE_MODEL = True             → OpenRouter free chain (`call_with_chain`).

VLM region proposal is unaffected — it always runs VLM_MODELS on OpenRouter.

`art_complete` is a drop-in for `call_with_chain`: same keyword signature,
returns (content, model_used), raises on failure. `models`/`max_tokens` are
honoured only on the OpenRouter path (the SDK manages its own)."""
from typing import Callable

from stages._claude_sdk import CLAUDE_SDK_MODEL, sdk_available, sdk_complete
from stages.stage_3._llm import call_with_chain

from .config import ART_FREE_MODEL

# Same-transport retries for a transient SDK hiccup (timeout/empty). This is
# NOT a fallback — it re-tries the SAME backend; OpenRouter is never touched.
_SDK_ATTEMPTS = 2


def _safe_validate(validator: Callable[[str], bool] | None, text: str) -> bool:
    if validator is None:
        return True
    try:
        return bool(validator(text))
    except Exception:
        return False


def art_complete(*, system: str, user: str, models: list[str] | None = None,
                 max_tokens: int = 2000,
                 progress: Callable[[str], None] | None = None,
                 label: str = "llm",
                 validator: Callable[[str], bool] | None = None) -> tuple[str, str]:
    """Route an art text-generation call to the configured backend.

    ART_FREE_MODEL=True  → OpenRouter chain (unchanged behaviour).
    ART_FREE_MODEL=False → Claude SDK only; raises RuntimeError on failure with
    no OpenRouter fallback (transports are separated on purpose)."""
    log = progress or (lambda _m: None)

    if ART_FREE_MODEL:
        return call_with_chain(system=system, user=user, models=models,
                               max_tokens=max_tokens, progress=progress,
                               label=label, validator=validator)

    if not sdk_available():
        raise RuntimeError(
            f"[{label}] ART_FREE_MODEL=False requires the Claude SDK, but it is "
            f"unavailable (not installed / not authenticated). Authenticate the "
            f"SDK, or set ART_FREE_MODEL=true to use the OpenRouter free chain.")

    reason = ""
    for attempt in range(1, _SDK_ATTEMPTS + 1):
        log(f"[{label}] via claude SDK ({CLAUDE_SDK_MODEL}) "
            f"attempt {attempt}/{_SDK_ATTEMPTS}")
        out = sdk_complete(system, user, log=progress)
        if out and _safe_validate(validator, out):
            log(f"[{label}] claude SDK returned {len(out)} chars")
            return out, f"claude-sdk:{CLAUDE_SDK_MODEL}"
        reason = "validator rejected output" if out else "empty / rate-limited"
        log(f"[{label}] claude SDK attempt {attempt} unusable ({reason})")
    raise RuntimeError(
        f"[{label}] Claude SDK failed after {_SDK_ATTEMPTS} attempts ({reason}); "
        f"NO OpenRouter fallback (ART_FREE_MODEL=False). Fix the SDK issue or set "
        f"ART_FREE_MODEL=true.")
