import json
from unittest.mock import MagicMock
import pytest
from pydantic import ValidationError

from stages.research_scout.router_schema import PrimaryMedium, VisualSource, QuestionRouteResponse
from stages.research_scout.media_router import route_media_source


def test_router_llm_call_with_mock_response():
    mock_client = MagicMock()
    mock_payload = {
        "question": "How did Gwen Stacy die?",
        "items": [
            {
                "event": "Neck snap from web whiplash",
                "primary_medium": "comic",
                "visual_source": "comic",
                "adaptation_title": "Amazing Spider-Man #121",
                "evidence_urls": ["https://marvel.fandom.com"],
                "confidence": 0.95,
                "reason": "Original comic death"
            }
        ]
    }
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content=json.dumps(mock_payload)))]
    mock_resp.usage = MagicMock(prompt_tokens=100, completion_tokens=50)
    mock_client.chat.completions.create.return_value = mock_resp

    res = route_media_source(
        question="How did Gwen Stacy die?",
        search_results=[{"title": "Gwen Stacy", "url": "https://marvel.fandom.com", "snippets": ["dies in #121"]}],
        client=mock_client
    )

    assert isinstance(res, QuestionRouteResponse)
    assert res.question == "How did Gwen Stacy die?"
    assert len(res.items) == 1
    assert res.items[0].primary_medium == PrimaryMedium.COMIC
    assert res.items[0].visual_source == VisualSource.COMIC
    assert mock_client.chat.completions.create.call_count == 1


def test_router_handles_malformed_json_retry():
    mock_client = MagicMock()
    # First response: malformed JSON
    resp_bad = MagicMock()
    resp_bad.choices = [MagicMock(message=MagicMock(content='{"question": "test", "items": [{"primary_medium": "invalid"}]}'))]
    resp_bad.usage = MagicMock(prompt_tokens=100, completion_tokens=50)

    # Second response: valid JSON
    valid_payload = {
        "question": "test",
        "items": [
            {
                "event": "Event 1",
                "primary_medium": "film",
                "visual_source": "youtube",
                "evidence_urls": [],
                "confidence": 0.9,
                "reason": "Film canon"
            }
        ]
    }
    resp_good = MagicMock()
    resp_good.choices = [MagicMock(message=MagicMock(content=json.dumps(valid_payload)))]
    resp_good.usage = MagicMock(prompt_tokens=120, completion_tokens=60)

    mock_client.chat.completions.create.side_effect = [resp_bad, resp_good]

    res = route_media_source(
        question="test",
        search_results=[],
        client=mock_client,
        max_retries=2
    )

    assert isinstance(res, QuestionRouteResponse)
    assert res.items[0].primary_medium == PrimaryMedium.FILM
    assert mock_client.chat.completions.create.call_count == 2


def test_router_raises_after_max_retries():
    mock_client = MagicMock()
    resp_bad = MagicMock()
    resp_bad.choices = [MagicMock(message=MagicMock(content='not even json'))]
    resp_bad.usage = MagicMock(prompt_tokens=50, completion_tokens=10)
    mock_client.chat.completions.create.return_value = resp_bad

    with pytest.raises((ValidationError, json.JSONDecodeError, RuntimeError)):
        route_media_source(
            question="test",
            search_results=[],
            client=mock_client,
            max_retries=1
        )


def test_system_prompt_asks_for_the_comic_source_and_forbids_guessing_the_issue():
    from stages.research_scout.media_router import SYSTEM_PROMPT

    # The JSON schema block repeats the field descriptions; check the instructions only.
    instructions = SYSTEM_PROMPT.split("You must output strictly valid JSON")[0].lower()
    assert "comic_series" in instructions
    # a film-only result list must still get its comic source named
    assert "even when the search results only" in instructions
    # unknown issue -> null, never a guess
    assert "never guess" in instructions


def test_router_call_caps_the_answer_length_and_the_prompt_asks_for_a_short_one():
    # On the Windows server 2 of 3 answers were cut off mid-JSON ("EOF while parsing a string"):
    # each cost a retry. Ask for a short answer and give the call an explicit output budget.
    from stages.research_scout.media_router import SYSTEM_PROMPT

    mock_client = MagicMock()
    payload = {"question": "q", "items": []}
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(payload)))])
    route_media_source("q", [], client=mock_client)

    kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert kwargs["max_tokens"] >= 2000
    instructions = SYSTEM_PROMPT.split("You must output strictly valid JSON")[0].lower()
    assert "at most 3 evidence_urls" in instructions
    assert "short" in instructions
