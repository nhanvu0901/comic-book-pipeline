"""Pure helpers for crossover-saga (multi-issue) narration. No I/O, no LLM.

`issue_index_of_page` maps a preprocessed page to its 1-based issue number using
the `ch{NN}_page` filename prefix that scrape_issue_pages writes (chapter_index).
`allocate_beats_across_issues` splits a global beat budget across issues,
weighted by each issue's page count but guaranteeing every issue a floor of 2
beats (so no issue is dropped from the Short)."""
from __future__ import annotations

import re

_CH_RE = re.compile(r"ch0*(\d+)_page", re.IGNORECASE)

_FLOOR_PER_ISSUE = 2


def issue_index_of_page(page: dict) -> int:
    """1-based issue number for a preprocessed page, or 0 if it carries no
    chapter prefix (e.g. a cover)."""
    src = str((page or {}).get("source_image", ""))
    m = _CH_RE.search(src)
    return int(m.group(1)) if m else 0


def allocate_beats_across_issues(
    total: int, n_issues: int, page_counts: list[int]
) -> dict[int, int]:
    """Return {issue_index(1-based): beat_count}. Sums to `total`. Each issue gets
    at least min(_FLOOR_PER_ISSUE, fair share) so short issues are still narrated."""
    if n_issues <= 1:
        return {1: total}
    floor = min(_FLOOR_PER_ISSUE, max(1, total // n_issues))
    alloc = {i: floor for i in range(1, n_issues + 1)}
    remaining = total - floor * n_issues
    if remaining <= 0:
        even = {i: 1 for i in range(1, n_issues + 1)}
        for i in range(n_issues, 0, -1):
            if sum(even.values()) <= total:
                break
            even[i] = 0
        return {i: c for i, c in even.items() if c > 0} or {1: total}
    weights = page_counts if (page_counts and len(page_counts) == n_issues and sum(page_counts) > 0) \
        else [1] * n_issues
    wsum = sum(weights)
    exact = [remaining * w / wsum for w in weights]
    base = [int(x) for x in exact]
    leftover = remaining - sum(base)
    order = sorted(range(n_issues), key=lambda i: exact[i] - base[i], reverse=True)
    for j in range(leftover):
        base[order[j]] += 1
    for i in range(1, n_issues + 1):
        alloc[i] += base[i - 1]
    return alloc
