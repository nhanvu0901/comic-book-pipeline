"""
LLM client setup, call_llm loop, and JSON extraction.

The TOOLS list and dispatch logic now live in tools/.
llm.py only handles the HTTP conversation with the model.
"""
import json
import re
from pathlib import Path
from anthropic import Anthropic

from .tools import TOOLS, dispatch_tool

TEMPLATE_PATH = Path(__file__).parent.parent.parent / "templates" / "system_prompt.txt"


def load_system_prompt() -> str:
    with open(TEMPLATE_PATH) as f:
        return f.read()


def create_client(api_key: str, base_url: str) -> Anthropic:
    return Anthropic(api_key=api_key, base_url=base_url)


def call_llm(client: Anthropic, messages: list, system: str, model: str) -> tuple[str, list]:
    """
    Call the LLM with tool-use support. Handles the tool-call loop internally.

    The LLM may request multiple tools in a single response (parallel tool calls).
    This function handles all of them before returning to the caller.

    Returns:
        (response_text, updated_messages)
    """
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system,
        tools=TOOLS,
        messages=messages,
    )

    # Tool-use loop — runs until the LLM stops requesting tools or hits the limit
    iteration = 0
    while response.stop_reason == "tool_use" and iteration < 10:
        iteration += 1

        # Collect ALL tool_use blocks from this response (LLM may batch them)
        tool_uses = [b for b in response.content if b.type == "tool_use"]

        # Execute each requested tool and collect results
        tool_results = []
        for tool_use in tool_uses:
            result = dispatch_tool(tool_use.name, tool_use.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": json.dumps(result),
            })

        # Append: assistant's tool request + our tool results — then call again
        messages = messages + [
            {"role": "assistant", "content": response.content},
            {"role": "user", "content": tool_results},
        ]

        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system,
            tools=TOOLS,
            messages=messages,
        )

    # Extract final text from the last response
    text_block = next((b for b in response.content if hasattr(b, "text")), None)
    text = text_block.text.strip() if text_block else ""

    final_messages = messages + [
        {"role": "assistant", "content": response.content}
    ]

    return text, final_messages


def _fix_json(text: str) -> str:
    """Fix common LLM JSON errors before parsing."""
    # Fix unquoted year ranges like 2009-2010 → "2009-2010"
    text = re.sub(r':\s*(\d{4})\s*-\s*(\d{4})\s*([,}\]])', r': "\1-\2"\3', text)
    # Fix trailing commas before } or ]
    text = re.sub(r',\s*([}\]])', r'\1', text)
    # Fix null without quotes (already valid JSON, but some models output None)
    text = re.sub(r':\s*None\s*([,}\]])', r': null\1', text)
    return text


def extract_json(raw: str) -> dict | None:
    """Extract JSON from LLM response, handling markdown code blocks and common errors."""
    patterns = [
        r"```json\s*\n(.*?)```",
        r"```\s*\n(.*?)```",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw, re.DOTALL)
        if match:
            text = match.group(1).strip()
            for attempt_text in [text, _fix_json(text)]:
                try:
                    return json.loads(attempt_text)
                except json.JSONDecodeError:
                    continue

    # Try parsing the whole thing
    for attempt_text in [raw, _fix_json(raw)]:
        try:
            return json.loads(attempt_text)
        except json.JSONDecodeError:
            continue

    # Try finding JSON-like structure in the text
    brace_start = raw.find("{")
    brace_end = raw.rfind("}")
    if brace_start != -1 and brace_end != -1:
        text = raw[brace_start : brace_end + 1]
        for attempt_text in [text, _fix_json(text)]:
            try:
                return json.loads(attempt_text)
            except json.JSONDecodeError:
                continue

    return None
