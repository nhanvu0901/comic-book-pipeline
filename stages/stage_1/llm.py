"""
LLM client setup, call_llm loop, and JSON extraction.

Uses OpenRouter's OpenAI-compatible /v1/chat/completions endpoint. Tool use is
native OpenAI function-calling format. The TOOLS list and dispatch logic live
in tools/. llm.py only handles the HTTP conversation with the model.
"""
import json
import re
from pathlib import Path
from openai import OpenAI

from .tools import TOOLS, dispatch_tool

TEMPLATE_PATH = Path(__file__).parent.parent.parent / "templates" / "system_prompt.txt"


def load_system_prompt() -> str:
    with open(TEMPLATE_PATH) as f:
        return f.read()


def create_client(api_key: str, base_url: str) -> OpenAI:
    return OpenAI(
        api_key=api_key,
        base_url=base_url,
        default_headers={
            "HTTP-Referer": "https://github.com/comic-video-pipeline",
            "X-Title": "Comic Video Pipeline",
        },
    )


def _assistant_tool_call_message(choice_message) -> dict:
    """Serialize an assistant reply with tool_calls into a dict suitable for re-sending."""
    return {
        "role": "assistant",
        "content": choice_message.content,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in (choice_message.tool_calls or [])
        ],
    }


def call_llm(client: OpenAI, messages: list, system: str, model: str, tools: list | None = None) -> tuple[str, list]:
    """
    Call the LLM with tool-use support. Handles the tool-call loop internally.

    Args:
        messages:  conversation history (OpenAI format, without the system message)
        system:    system prompt, prepended as role=system on each request
        tools:     optional tool schemas (OpenAI function format). None = all TOOLS. [] = disabled.

    Returns:
        (response_text, updated_messages)
    """
    active_tools = tools if tools is not None else TOOLS

    def _request(msgs: list, with_tools: bool):
        kwargs = {
            "model": model,
            "max_tokens": 4096,
            "messages": [{"role": "system", "content": system}] + msgs,
        }
        if with_tools and active_tools:
            kwargs["tools"] = active_tools
        return client.chat.completions.create(**kwargs)

    response = _request(messages, with_tools=True)
    choice = response.choices[0]

    iteration = 0
    while choice.finish_reason == "tool_calls" and iteration < 15:
        iteration += 1
        tool_calls = choice.message.tool_calls or []

        messages = messages + [_assistant_tool_call_message(choice.message)]

        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                args = {}
            result = dispatch_tool(tc.function.name, args)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result),
            })

        response = _request(messages, with_tools=True)
        choice = response.choices[0]

    # Budget exhausted and the model still wants tools — force a text-only reply.
    if choice.finish_reason == "tool_calls" and iteration >= 15:
        tool_calls = choice.message.tool_calls or []
        messages = messages + [_assistant_tool_call_message(choice.message)]
        for tc in tool_calls:
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps({"note": "Tool call budget exhausted. Please respond with your final answer now."}),
            })
        response = _request(messages, with_tools=False)
        choice = response.choices[0]

    text = (choice.message.content or "").strip()
    messages = messages + [{"role": "assistant", "content": choice.message.content}]
    return text, messages


def _fix_json(text: str) -> str:
    """Fix common LLM JSON errors before parsing."""
    text = re.sub(r':\s*(\d{4})\s*-\s*(\d{4})\s*([,}\]])', r': "\1-\2"\3', text)
    text = re.sub(r',\s*([}\]])', r'\1', text)
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

    for attempt_text in [raw, _fix_json(raw)]:
        try:
            return json.loads(attempt_text)
        except json.JSONDecodeError:
            continue

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
