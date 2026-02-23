"""
Comic Scraper — scrape comic pages from batcave.biz using nodriver.

Uses window.__DATA__ embedded JSON for efficient chapter/image discovery.
"""
from .readcomiconline import discover_issues, scrape_issue_pages, scrape_single_page

__all__ = ["discover_issues", "scrape_issue_pages", "scrape_single_page"]
