"""Suite-wide guards.

The QA discovery gate falls back to the MediaWiki API when the reader returns a bot-check
page or fails (stages/research_scout/issue_pages.py). That call is a plain urllib GET, so a
test that fakes only the reader would silently reach the network. Tests that exercise the
API route override ``issue_pages._api_get`` themselves.
"""

import pytest


@pytest.fixture(autouse=True)
def _no_real_mediawiki_api(monkeypatch):
    from stages.research_scout import issue_pages

    monkeypatch.setattr(issue_pages, "_api_get", lambda url: None)
