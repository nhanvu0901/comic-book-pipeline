"""Load and render versioned Stage 1 research prompts and policy data."""

from __future__ import annotations

import hashlib
import json
import string
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import ScoutMode


_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROMPTS_ROOT = _REPO_ROOT / "research_prompts"
_POLICIES_ROOT = _REPO_ROOT / "research_policies"
_ALLOWED_PLACEHOLDERS = frozenset(
    {"user_intent", "angle", "digest", "candidate", "raw_evidence"}
)
_TEMPLATE_FILES = {
    "general": {
        ScoutMode.QA: "general_qa.v2.md",
        ScoutMode.MICRO: "general_micro.v1.md",
    },
    "specific": {
        ScoutMode.QA: "specific_qa.v1.md",
        ScoutMode.MICRO: "specific_micro.v1.md",
    },
    "evidence_gate": {
        ScoutMode.QA: "evidence_gate.v1.md",
        ScoutMode.MICRO: "evidence_gate.v1.md",
    },
}


@dataclass(frozen=True)
class RenderedPrompt:
    """A rendered prompt and the identity of the exact bytes sent downstream."""

    text: str
    version: str
    sha256: str


@dataclass
class PolicyBundle:
    """Versioned policy assets selected for one research scout mode."""

    mode: ScoutMode
    source_profiles: dict[str, Any]
    general_angles: dict[str, Any]
    gates: dict[str, Any]

    @classmethod
    def load(cls, mode: ScoutMode) -> "PolicyBundle":
        """Load a fresh policy bundle for ``mode`` from repository assets."""

        try:
            selected_mode = mode if isinstance(mode, ScoutMode) else ScoutMode(mode)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unsupported scout mode: {mode!r}") from exc

        return cls(
            mode=selected_mode,
            source_profiles=_load_json("source_profiles.v1.json"),
            general_angles=_load_json("general_angles.v1.json"),
            gates=_load_json("gates.v1.json"),
        )

    def render(self, template_name: str, **values: str) -> RenderedPrompt:
        """Render a named prompt, rejecting missing and unsupported values."""

        mode_files = _TEMPLATE_FILES.get(template_name)
        if mode_files is None:
            raise ValueError(f"unknown template: {template_name}")
        unsupported = sorted(set(values) - _ALLOWED_PLACEHOLDERS)
        if unsupported:
            names = ", ".join(unsupported)
            raise ValueError(f"unsupported placeholder values: {names}")
        if any(not isinstance(value, str) for value in values.values()):
            raise ValueError("placeholder values must be strings")

        path = _PROMPTS_ROOT / mode_files[self.mode]
        template = path.read_text(encoding="utf-8")
        placeholders = _placeholders(template)
        missing = sorted(placeholders - values.keys())
        if missing:
            names = ", ".join(missing)
            raise ValueError(f"missing required placeholder(s): {names}")

        try:
            rendered = template.format(**values)
        except (IndexError, KeyError, ValueError) as exc:
            raise ValueError(f"could not render template {template_name!r}: {exc}") from exc

        return RenderedPrompt(
            text=rendered,
            version=path.stem,
            sha256=hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        )


def _load_json(filename: str) -> dict[str, Any]:
    path = _POLICIES_ROOT / filename
    with path.open("r", encoding="utf-8") as handle:
        return deepcopy(json.load(handle))


def _placeholders(template: str) -> set[str]:
    names: set[str] = set()
    for _, field_name, _, _ in string.Formatter().parse(template):
        if field_name is None:
            continue
        if field_name not in _ALLOWED_PLACEHOLDERS:
            raise ValueError(f"unsupported placeholder in template: {field_name}")
        names.add(field_name)
    return names
