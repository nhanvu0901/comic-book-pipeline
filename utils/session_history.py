"""
Per-session conversation history for the Stage 1 agent.

Wraps a plain list[dict] of OpenAI-format messages with:
  - Token counting (tiktoken cl100k_base, ±10% for non-OpenAI models)
  - JSON save/load for crash-resume
  - Phase markers so the LLM sees approved/rejected context
  - Trim safety valve when context grows too large after many retries
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import tiktoken

_enc = tiktoken.get_encoding("cl100k_base")


def _count_str(s: str) -> int:
    return len(_enc.encode(s))


def _count_message(msg: dict) -> int:
    content = msg.get("content") or ""
    if isinstance(content, list):
        content = " ".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return _count_str(str(content)) + _count_str(msg.get("role", "")) + 4


@dataclass
class SessionHistory:
    """
    Manages OpenAI-format messages for one pipeline session.

    Failed retry attempts stay in history so the LLM learns from mistakes.
    Approved phase results get a marker so downstream phases see context.
    """
    messages: list[dict] = field(default_factory=list)
    context_limit: int = 128_000
    reserve_tokens: int = 4_096

    def add(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})

    def add_raw(self, msg: dict) -> None:
        self.messages.append(msg)

    def extend(self, msgs: list[dict]) -> None:
        self.messages.extend(msgs)

    def replace_from(self, msgs: list[dict]) -> None:
        """Replace entire message list (used after call_llm returns updated list)."""
        self.messages = msgs

    @property
    def token_count(self) -> int:
        return sum(_count_message(m) for m in self.messages)

    @property
    def tokens_available(self) -> int:
        return self.context_limit - self.reserve_tokens - self.token_count

    def is_near_limit(self, threshold: float = 0.85) -> bool:
        used_budget = self.context_limit - self.reserve_tokens
        return self.token_count >= used_budget * threshold

    def mark_phase(self, phase_name: str, approved: bool) -> None:
        status = "APPROVED" if approved else "REJECTED"
        self.messages.append({
            "role": "user",
            "content": f"[PHASE_RESULT: {phase_name} | STATUS: {status}]",
        })

    def compress_approved_phase(self, phase_name: str, summary: str) -> None:
        """After approval, optionally replace verbose phase messages with a compact summary."""
        cleaned = [
            m for m in self.messages
            if not (m.get("_phase") == phase_name and m.get("role") != "user")
        ]
        cleaned.append({
            "role": "user",
            "content": f"[PHASE {phase_name} COMPLETED] Summary: {summary}",
        })
        self.messages = cleaned

    def trim_oldest(self, keep_last_n: int = 6) -> int:
        """Remove oldest user+assistant exchange pairs, keeping the last N."""
        pairs: list[tuple[int, int]] = []
        i = 0
        while i < len(self.messages):
            if self.messages[i]["role"] == "user":
                end = i + 1
                while end < len(self.messages) and self.messages[end]["role"] != "user":
                    end += 1
                pairs.append((i, end))
            i += 1
        to_remove = max(0, len(pairs) - keep_last_n)
        if to_remove == 0:
            return 0
        indices_to_drop: set[int] = set()
        for start, end in pairs[:to_remove]:
            indices_to_drop.update(range(start, end))
        original_len = len(self.messages)
        self.messages = [m for i, m in enumerate(self.messages) if i not in indices_to_drop]
        return original_len - len(self.messages)

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "context_limit": self.context_limit,
            "reserve_tokens": self.reserve_tokens,
            "messages": self.messages,
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    @classmethod
    def load(cls, path: Path) -> SessionHistory:
        path = Path(path)
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        return cls(
            messages=data.get("messages", []),
            context_limit=data.get("context_limit", 128_000),
            reserve_tokens=data.get("reserve_tokens", 4_096),
        )

    def clear(self) -> None:
        self.messages.clear()

    def __len__(self) -> int:
        return len(self.messages)
