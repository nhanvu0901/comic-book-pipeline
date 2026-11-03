"""Contracts of the rendered Q&A prompt and the script importer."""
import json
import re

import pytest

import stages.stage_3.gemini_prompt as gp


def render(tmp_path, monkeypatch, question, count, fmt=None):
    monkeypatch.setattr(gp, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(__import__("config"), "RESEARCH_SESSIONS_ROOT", tmp_path / "no-sessions")
    root = tmp_path / "case"
    root.mkdir()
    context = {"question": question, "items": [
        {"entity": f"Subject {n}", "source_comic": f"Series #{n}",
         "how_or_why": f"Verified event {n}", "evidence_urls": [f"https://example.test/{n}"]}
        for n in range(1, count + 1)
    ]}
    if fmt is not None:
        context["format"] = fmt
    (root / "answer_context.json").write_text(json.dumps(context))
    prompt, path = gp.generate_gemini_writer_prompt("case")
    assert path.read_text() == prompt
    assert "{{QUESTION}}" not in prompt and "{{SCOUT_JSON}}" not in prompt
    assert "{{STORY_CONTEXT}}" not in prompt
    return prompt


@pytest.mark.parametrize("question,fmt,expected", [
    ("Why did the hero leave?", None, "direct"),
    ("How many heroes escaped?", None, "list"),
    ("What are some examples of escape?", None, "list"),
    ("How did the hero escape?", "list", "list"),
    ("How many heroes escaped?", "direct", "direct"),
    ("What is the power?", "explain", "direct"),
])
def test_rendered_format_respects_explicit_choice_and_question(tmp_path, monkeypatch, question, fmt, expected):
    prompt = render(tmp_path, monkeypatch, question, 4, fmt)
    assert f"- Format: {expected} (" in prompt
    assert "For DIRECT format" in prompt
    assert "For LIST format" in prompt
    assert "MULTIPLE items, each hook must tell the listener this is a list" not in prompt
    assert "final answer" in prompt.lower()


@pytest.mark.parametrize("count", [1, 4, 8])
def test_budget_and_source_order_are_compatible_with_import(tmp_path, monkeypatch, count):
    prompt = render(tmp_path, monkeypatch, "Why did it happen?", count)
    assert "110–160 spoken words including the hook" in prompt
    assert not re.search(r"40\s*(?:to|–|-)\s*60 words per item|40 to 60 words of screen time", prompt)
    assert "may exceed" in prompt
    assert "over 20 words" in prompt
    assert "never fill gaps from memory or invent details" in prompt.lower()
    assert "comic and issue" in prompt
    assert prompt.index('"entity": "Subject 1"') < prompt.index(f'"entity": "Subject {count}"') if count > 1 else '"entity": "Subject 1"' in prompt
    body = [f"In Series #{n}, " + " ".join([f"event{n}"] * 22) + "." for n in range(1, count + 1)]
    hook, parsed, outro = gp._split_qa_script("A question needs an answer.\n\n" + "\n\n".join(body), count)
    assert hook == "A question needs an answer."
    assert parsed == body
    assert not outro


def test_general_answer_card_does_not_assume_an_object_is_used(tmp_path, monkeypatch):
    prompt = render(tmp_path, monkeypatch, "Why did the hero leave?", 2)
    assert "the character use it" not in prompt
    assert "exact construct or thing" not in prompt
    assert "person, action, mechanism, decision, limitation, or result" in prompt
