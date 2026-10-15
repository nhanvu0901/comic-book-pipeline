"""Suite-wide guards.

The QA discovery gate reads wiki pages through the MediaWiki API (and falls back to the reader). That
call is a plain urllib GET, so a test that fakes only the reader would silently reach the network.
Tests that exercise the API route override ``issue_pages._api_get`` themselves.

The semantic answer check calls OpenRouter. The suite runs with it switched off and its one network
door (``qa_semantic_check._post``) shut: a test that wants the model fakes the checker through the
gate's ``semantic=`` argument, or patches ``_post`` itself.
"""

import pytest


@pytest.fixture(autouse=True)
def _no_real_mediawiki_api(monkeypatch):
    from stages.research_scout import issue_pages

    monkeypatch.setattr(issue_pages, "_api_get", lambda url: None)


@pytest.fixture(autouse=True)
def _no_real_semantic_check(monkeypatch):
    import config
    from stages.research_scout import qa_semantic_check

    def closed(body, *, timeout):
        raise AssertionError("network disabled in tests: mock the semantic checker")

    monkeypatch.setattr(config, "QA_SEMANTIC_CHECK", False, raising=False)
    monkeypatch.setattr(qa_semantic_check, "_post", closed)
