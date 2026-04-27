"""
Comic Scraper — scrape comic pages from batcave.biz via pure HTTP (curl_cffi).

Solves the site's SHA-256 proof-of-work challenge in Python, then reads
window.__DATA__ embedded JSON for chapters / image URLs. No browser needed.
"""
from .readcomiconline import discover_issues, scrape_issue_pages, scrape_single_page

__all__ = ["discover_issues", "scrape_issue_pages", "scrape_single_page"]
