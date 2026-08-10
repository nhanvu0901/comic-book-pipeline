"""
Per-session conversation history for the Stage 1 agent.

Wraps a plain list[dict] of OpenAI-format messages with JSON save/load and phase
markers so the LLM sees approved/rejected context.

2026-08-10: the token-budget machinery (tiktoken counting, tokens_available,
is_near_limit, trim_oldest, compress_approved_phase, add_raw/extend) was removed — it
was built for a context-trimming retry loop that never got wired into the agent, so
none of it had a single caller, and it dragged a tiktoken import into Stage 1 startup
for nothing. The resume READ side (ScriptAgent.load_session) was equally unwired and
went with it; save() stays because save_session persists the transcript for inspection.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SessionHistory:
    """
    Manages OpenAI-format messages for one pipeline session.

    Approved phase results get a marker so downstream phases see context.
    """
    messages: list[dict] = field(default_factory=list)

    def add(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})

    def replace_from(self, msgs: list[dict]) -> None:
        """Replace entire message list (used after call_llm returns updated list)."""
        self.messages = msgs

    def mark_phase(self, phase_name: str, approved: bool) -> None:
        status = "APPROVED" if approved else "REJECTED"
        self.messages.append({
            "role": "user",
            "content": f"[PHASE_RESULT: {phase_name} | STATUS: {status}]",
        })

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"messages": self.messages}, indent=2, ensure_ascii=False))

    @classmethod
    def load(cls, path: Path) -> SessionHistory:
        path = Path(path)
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            return cls()
        return cls(messages=data.get("messages", []))
