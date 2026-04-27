"""
Map a comic_context.json `issues` string (e.g. "#121-122") and a batcave_url
to a list of (issue_label, reader_url) pairs ready for scrape_issue_pages().
"""
import re
from utils.comic_scraper import discover_issues


def parse_issue_range(issues: str) -> list[float]:
    """
    Parse strings like '#121-122', '#1', '#1, #3', 'chapter 5'
    into a sorted list of numeric issue identifiers.
    """
    if not issues:
        return []
    nums: set[float] = set()
    # handle hyphenated ranges first: "#121-122" or "121-122"
    for m in re.finditer(r"#?\s*(\d+(?:\.\d+)?)\s*-\s*#?\s*(\d+(?:\.\d+)?)", issues):
        a, b = float(m.group(1)), float(m.group(2))
        lo, hi = min(a, b), max(a, b)
        n = lo
        while n <= hi:
            nums.add(n)
            n += 1
    # handle singletons: "#1", "#3"
    # (avoid double-counting by stripping already-matched range substrings)
    stripped = re.sub(r"#?\s*\d+(?:\.\d+)?\s*-\s*#?\s*\d+(?:\.\d+)?", "", issues)
    for m in re.finditer(r"#?\s*(\d+(?:\.\d+)?)", stripped):
        nums.add(float(m.group(1)))
    return sorted(nums)


def resolve_chapters(batcave_url: str, issues: str) -> list[dict]:
    """
    Discover all chapters for the series, then filter to the requested issues.
    Returns list of {"label": "#N", "number": float, "reader_url": str, "chapter_id": int}.
    """
    wanted = parse_issue_range(issues)
    all_chapters = discover_issues(batcave_url)
    if not all_chapters:
        return []

    if not wanted:
        # No specific issues requested — return all chapters
        return [
            {
                "label": f"#{int(c['number']) if c['number'].is_integer() else c['number']}",
                "number": c["number"],
                "reader_url": c["url"],
                "chapter_id": c["chapter_id"],
            }
            for c in all_chapters
        ]

    by_num = {c["number"]: c for c in all_chapters}
    resolved: list[dict] = []
    for n in wanted:
        c = by_num.get(n) or by_num.get(float(int(n)))
        if not c:
            continue
        label = f"#{int(n) if float(n).is_integer() else n}"
        resolved.append({
            "label": label,
            "number": c["number"],
            "reader_url": c["url"],
            "chapter_id": c["chapter_id"],
        })
    return resolved
