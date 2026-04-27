"""
Web search tool — Tavily search with schema definition.
"""
import os
from tavily import TavilyClient
from ..ui import Colors

# ─── Schema (sent to LLM so it knows when/how to call this tool) ────────────

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web for information about comic books, manga, graphic novels, "
            "characters, story arcs, events, issue numbers, creators, and publication dates. "
            "Use when: the comic is recent (2023+), you need to verify specific issue numbers "
            "or credits, multiple events share the same name, or the user mentions an "
            "indie/lesser-known title. Tip: combine with paraphrase_query for better coverage."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Specific search query about the comic book topic",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Number of results to return (default 5, max 10)",
                },
            },
            "required": ["query"],
        },
    },
}


# ─── Implementation ──────────────────────────────────────────────────────────

def web_search(query: str, max_results: int = 5) -> dict:
    """Search the web using Tavily. Returns a list of {title, url, snippet}."""
    max_results = min(max_results, 10)
    print(f"  {Colors.DIM}🔍 Searching: {query}{Colors.END}")
    try:
        client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
        response = client.search(query, max_results=max_results)
        results = response.get("results", [])
        print(f"  {Colors.DIM}   Found {len(results)} results{Colors.END}")
        return {
            "results": [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("content", ""),
                }
                for r in results
            ]
        }
    except Exception as e:
        print(f"  {Colors.DIM}   Search error: {e}{Colors.END}")
        return {"error": str(e), "results": []}
