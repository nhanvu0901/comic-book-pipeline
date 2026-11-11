# How Claude Tool Use Works
### Structured Conversation Protocol — Anthropic SDK

> All examples are taken directly from `stages/stage_1/`.

---

## The Core Idea

The LLM cannot run Python code on its own. Tool use is the bridge.

You describe a function to the LLM in JSON. When the LLM decides it needs that function, it **asks** you to run it by returning a special response. You run the function, send back the result, and the LLM continues. The model never touches your code directly — it only sends requests and reads results.

```
You → [user message] → LLM
                        LLM → "I need web_search('Batman Knightfall')"
You run web_search()
You → [search results] → LLM
                        LLM → [final text answer]
```

This is called **Tool Use** by Anthropic, and **Function Calling** by OpenAI. Same concept.

---

## Part 1: Defining a Tool

Every tool needs two things living in the same file: a **schema** and a **function**.

The schema is what the LLM reads. It tells the model what the tool does, when to use it, and what arguments to pass. The function is your actual Python code.

**From `tools/web_search.py`:**

```python
WEB_SEARCH_TOOL = {
    "name": "web_search",
    "description": "Search the web for comic book info. Use when ...",
    "input_schema": {
        "type": "object",
        "properties": {
            "query":       {"type": "string",  "description": "..."},
            "max_results": {"type": "integer", "description": "..."},
        },
        "required": ["query"],   # max_results is optional
    },
}

def web_search(query: str, max_results: int = 5) -> dict:
    # ... actual implementation
    return {"results": [...]}
```

### Writing a good description

The `description` field is the most important part of the schema. It controls **when** the LLM decides to call the tool. Be specific:

- Say *when to use it* ("Use when the comic is recent (2023+)...")
- Say *what it returns* ("Returns a list of title, url, snippet")
- Mention *what not to use it for* if there's a common mistake

The `description` inside each property also matters — the LLM fills the arguments based on those descriptions.

### Required vs optional arguments

Fields in `"required"` must always be provided by the LLM. Everything else is optional. Always give optional fields a Python default value to match.

---

## Part 2: The TOOLS List

The SDK expects a flat list of tool schema dicts. This list is sent with **every API call**.

**From `tools/__init__.py`:**

```python
from .web_search       import WEB_SEARCH_TOOL
from .sequential_thinking import SEQUENTIAL_THINKING_TOOL
from .paraphrase_query import PARAPHRASE_QUERY_TOOL

TOOLS = [
    WEB_SEARCH_TOOL,
    SEQUENTIAL_THINKING_TOOL,
    PARAPHRASE_QUERY_TOOL,
]
```

You pass it to the API like this:

```python
client.messages.create(
    model=model,
    max_tokens=4096,
    system=system,
    tools=TOOLS,      # <— the list goes here
    messages=messages,
)
```

The LLM will pick from this list as needed. It may use zero tools, one tool, or several in a single conversation.

---

## Part 3: The Conversation Protocol

This is the most important thing to understand. Every message in the conversation is stored as a dict with `role` and `content`. You accumulate **all messages** and send the entire history on every API call. That is how the LLM "remembers" the conversation.

The two normal roles:

```python
{"role": "user",      "content": "Tell me about Batman Knightfall"}
{"role": "assistant", "content": "Batman Knightfall is..."}
```

When tools are involved, two extra message types appear.

### When the LLM wants a tool

The API returns a response with `stop_reason == "tool_use"`. The `content` is a list of **content blocks**, and one of them has `type == "tool_use"`:

```python
# response.content looks like:
[
    TextBlock(type="text", text="Let me search for that..."),
    ToolUseBlock(
        type="tool_use",
        id="toolu_abc123",
        name="web_search",
        input={"query": "Batman Knightfall Bane 1993"}
    )
]
```

You must append the entire `response.content` as an assistant message before sending back the result. This preserves the model's "I asked for this tool" in the history.

### Sending back the result

You send the result back as a `user` message with `type: "tool_result"`. The `tool_use_id` links the result to the specific tool request:

```python
{
    "role": "user",
    "content": [
        {
            "type": "tool_result",
            "tool_use_id": "toolu_abc123",   # must match the id from above
            "content": json.dumps(result),    # your function's return value
        }
    ]
}
```

The result content is always a **string** — that is why `json.dumps()` is used even when the result is already a dict.

### The full message sequence

```
[user]       "Tell me about Batman Knightfall"
[assistant]  content: [TextBlock, ToolUseBlock(web_search)]   ← stop_reason=tool_use
[user]       content: [tool_result for toolu_abc123]
[assistant]  content: [TextBlock("Here is what I found...")]  ← stop_reason=end_turn
```

---

## Part 4: The Tool-Use Loop

Because the LLM may call tools multiple times (search, then search again with a different query), you need a loop. The loop runs until `stop_reason != "tool_use"`.

**From `llm.py`:**

```python
response = client.messages.create(model=..., tools=TOOLS, messages=messages)

while response.stop_reason == "tool_use" and iteration < 10:
    # 1. Find all tool requests in this response
    tool_uses = [b for b in response.content if b.type == "tool_use"]

    # 2. Run each requested tool
    tool_results = []
    for tool_use in tool_uses:
        result = dispatch_tool(tool_use.name, tool_use.input)
        tool_results.append({
            "type": "tool_result",
            "tool_use_id": tool_use.id,
            "content": json.dumps(result),
        })

    # 3. Append assistant request + your results, then call the API again
    messages = messages + [
        {"role": "assistant", "content": response.content},
        {"role": "user",      "content": tool_results},
    ]
    response = client.messages.create(model=..., tools=TOOLS, messages=messages)
```

**The loop limit (`iteration < 10`) is a safety guard.** Without it, a confused LLM could search forever. Ten iterations is generous for most tasks.

**Why `messages = messages + [...]` instead of `messages.append(...)`?**
Using `+` creates a new list. This means the original `messages` variable passed in from outside is never mutated. It is a functional style that prevents subtle bugs where multiple callers share the same list reference.

---

## Part 5: Parallel Tool Calls

The LLM can request **multiple tools at once** in a single response. For example, it might call `paraphrase_query` and `sequential_thinking` simultaneously. The `content` list will have multiple `ToolUseBlock` entries.

The loop above already handles this correctly by using:

```python
tool_uses = [b for b in response.content if b.type == "tool_use"]
```

This collects all of them. Every result goes into `tool_results`, and the API receives all results in one message. The `tool_use_id` is what matches each result to its request — that is why every result must carry the correct id.

---

## Part 6: Dispatching Tool Calls

When multiple tools exist, you need a router. A simple `if/elif` function is enough.

**From `tools/__init__.py`:**

```python
def dispatch_tool(name: str, inputs: dict) -> dict:
    if name == "web_search":
        return web_search(query=inputs["query"], ...)

    if name == "sequential_thinking":
        return think(thought=inputs["thought"], ...)

    if name == "paraphrase_query":
        return paraphrase_query(query=inputs["query"], ...)

    return {"error": f"Unknown tool: '{name}'"}
```

Always return a dict — even for errors. A `{"error": "..."}` response lets the LLM know something went wrong and it can decide how to recover, rather than crashing your program.

---

## Part 7: The System Prompt's Role

The system prompt is where you tell the LLM **how to behave**. It is sent with every API call alongside the tools.

For tools to work well, the system prompt should explain:
- When to prefer tools over its own training knowledge
- What format to use in the final response (JSON, plain text, etc.)
- Any constraints on tool usage (e.g. "search at most 3 times")

Without clear system prompt instructions, the LLM may call tools unnecessarily, or not call them when it should.

---

## Part 8: Extracting the Final Text

After the loop ends, the last response has `stop_reason == "end_turn"` and contains the actual answer. The `content` is still a list of blocks, so you need to find the text one:

```python
text_block = next((b for b in response.content if hasattr(b, "text")), None)
text = text_block.text.strip() if text_block else ""
```

Using `hasattr(b, "text")` is more robust than checking `b.type == "text"` because it handles any block type that happens to carry text without breaking on unknown types.

---

## Part 9: Tools That Need LLM Access

Some tools make their own LLM sub-call (like `paraphrase_query`, which calls the same model to generate diverse search phrasings). These tools need a reference to the client object.

The pattern used here is module-level initialization:

```python
# In paraphrase_query.py
_client = None
_model  = None

def init(client, model):
    global _client, _model
    _client = client
    _model  = model
```

The agent calls `tools.init(self.client, self.model)` once at startup. After that, any tool can make sub-calls without being passed the client every time.

The sub-call itself is just a normal API call with a very focused prompt and low `max_tokens`:

```python
response = _client.messages.create(
    model=_model,
    max_tokens=300,       # cheap, fast
    messages=[{"role": "user", "content": prompt}],
    # no tools= here — sub-call is plain text
)
```

Note that `tools=` is **not** passed to the sub-call. The paraphrase sub-call is a simple text generation task — you do not want it to recursively trigger more tool use.

---

## Summary: The Full Protocol at a Glance

```
DEFINE   tools/my_tool.py      schema dict  +  python function
REGISTER tools/__init__.py     TOOLS list   +  dispatch_tool()
WIRE     agent.py              tools.init(client, model)
CALL     llm.py                client.messages.create(..., tools=TOOLS, ...)
LOOP     llm.py                while stop_reason == "tool_use"
ROUTE    llm.py                dispatch_tool(name, input) → dict
REPLY    llm.py                {"type": "tool_result", "tool_use_id": id, "content": json}
END      llm.py                stop_reason == "end_turn" → extract text
```

The conversation history is the source of truth. Never truncate or modify it mid-conversation — the LLM needs the complete thread, including every tool request and result, to reason correctly.
