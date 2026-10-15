"""TEMPORARY RED-phase shim: behaves like the round-1 fetch so the round-2 tests run and
fail on behaviour. Replaced by the real implementation next."""

from __future__ import annotations

from . import cited_sources


def _api_get(url):
    return None


def page_problem(text):
    return None


def wiki_html_to_text(html, title=""):
    return ""


def slug_variants(url, issue):
    return []


def fetch_issue_page(url):
    return cited_sources.fetch_source(url, timeout=15, max_chars=250_000, retries=1)
