"""
Save comic_context and conversation log to the project folder.
"""
import json
import re
from .ui import print_success, Colors


def save_comic_context(comic_context: dict, project_name: str, get_project_dirs) -> str:
    """Save comic_context JSON to the project's folder."""
    dirs = get_project_dirs(project_name)
    path = str(dirs["root"] / "comic_context.json")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(comic_context, f, indent=2, ensure_ascii=False)

    print_success(f"Comic context saved: {path}")
    return path


def save_conversation_log(agent, project_name: str, get_project_dirs) -> str:
    """Save the full LLM conversation for debugging/reference."""
    dirs = get_project_dirs(project_name)
    log_path = str(dirs["root"] / "conversation_log.json")

    messages = getattr(agent, "messages", None) or getattr(agent.history, "messages", [])
    serializable = []
    for msg in messages:
        if isinstance(msg.get("content"), list):
            blocks = []
            for block in msg["content"]:
                if hasattr(block, "text"):
                    blocks.append({"type": "text", "text": block.text})
                elif hasattr(block, "type"):
                    blocks.append({"type": str(block.type), "data": str(block)})
                elif isinstance(block, dict):
                    blocks.append(block)
                else:
                    blocks.append({"type": "unknown", "data": str(block)})
            serializable.append({"role": msg["role"], "content": blocks})
        else:
            serializable.append(msg)

    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2, ensure_ascii=False, default=str)

    print(f"  {Colors.DIM}💾 Conversation log: {log_path}{Colors.END}")
    return log_path


def slugify(text: str) -> str:
    """Convert text to a filesystem-safe project name."""
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[\s]+", "_", slug)
    return slug[:60]
