"""Tests for the MONEY SHOT funnel Phase 1 (stages/money_shot.py).

derive_money_target() is one small OpenRouter call with a 2-model free fallback
chain — mocked at the client boundary here, no network. ocr_money_hits() is pure
lexical scoring with no LLM/network at all."""
from unittest.mock import MagicMock, patch

from stages.money_shot import derive_money_target, ocr_money_hits


ANSWER_CONTEXT = {
    "question": "Who has wielded Mjolnir?",
    "items": [
        {"entity": "Beta Ray Bill", "how_or_why": "proved worthy in ritual combat",
         "drawable_moment": "Bill lifts Mjolnir mid-fight"},
        {"entity": "Captain America", "how_or_why": "worthy heart under Ultron's Age",
         "drawable_moment": "Cap catches Mjolnir thrown at him"},
    ],
}


def _fake_response(content: str):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    return resp


def _create_mock(mock_client):
    """The chat.completions.create() mock reachable through the SAME chained call
    money_shot.py makes: client.with_options(...).chat.completions.create(...)."""
    return mock_client.return_value.with_options.return_value.chat.completions.create


# ─── (a) parse JSON from a mock response ────────────────────────────────────

def test_derive_parses_valid_json_from_first_model():
    good = ('{"money_character": "Beta Ray Bill", "money_object": "Mjolnir", '
            '"money_event": "Bill lifts Mjolnir", '
            '"query_text": "Beta Ray Bill grips Mjolnir mid-battle"}')
    with patch("stages.money_shot.OPENROUTER_API_KEY", "dummy-key"), \
         patch("stages.money_shot._client") as mock_client:
        _create_mock(mock_client).return_value = _fake_response(good)
        target = derive_money_target(ANSWER_CONTEXT, log=lambda _m: None)

    assert target == {
        "money_character": "Beta Ray Bill",
        "money_object": "Mjolnir",
        "money_event": "Bill lifts Mjolnir",
        "query_text": "Beta Ray Bill grips Mjolnir mid-battle",
    }
    assert _create_mock(mock_client).call_count == 1  # primary model only


# ─── (b) fallback to model 2 when model 1 errors ────────────────────────────

def test_derive_falls_back_to_second_model_on_first_model_error():
    good = ('{"money_character": null, "money_object": "the Ultimate Nullifier", '
            '"money_event": "Reed threatens Galactus", '
            '"query_text": "Reed Richards aims the Ultimate Nullifier at Galactus"}')
    with patch("stages.money_shot.OPENROUTER_API_KEY", "dummy-key"), \
         patch("stages.money_shot._client") as mock_client:
        _create_mock(mock_client).side_effect = [RuntimeError("boom"), _fake_response(good)]
        target = derive_money_target(ANSWER_CONTEXT, log=lambda _m: None)

    assert target["money_object"] == "the Ultimate Nullifier"
    assert target["money_character"] is None
    assert _create_mock(mock_client).call_count == 2
    calls = _create_mock(mock_client).call_args_list
    assert calls[0].kwargs["model"] == "google/gemma-4-31b-it:free"
    assert calls[1].kwargs["model"] == "openai/gpt-oss-120b:free"


# ─── (c) both models fail (error, or unparseable/incomplete JSON) -> None ───

def test_derive_returns_none_when_both_models_raise():
    with patch("stages.money_shot.OPENROUTER_API_KEY", "dummy-key"), \
         patch("stages.money_shot._client") as mock_client:
        _create_mock(mock_client).side_effect = [RuntimeError("boom1"), RuntimeError("boom2")]
        target = derive_money_target(ANSWER_CONTEXT, log=lambda _m: None)

    assert target is None
    assert _create_mock(mock_client).call_count == 2


def test_derive_returns_none_on_unparseable_json_from_both_models():
    with patch("stages.money_shot.OPENROUTER_API_KEY", "dummy-key"), \
         patch("stages.money_shot._client") as mock_client:
        _create_mock(mock_client).side_effect = [
            _fake_response("not json at all"),
            _fake_response('{"money_event": ""}'),  # missing query_text -> still invalid
        ]
        target = derive_money_target(ANSWER_CONTEXT, log=lambda _m: None)

    assert target is None


def test_derive_returns_none_without_api_key():
    with patch("stages.money_shot.OPENROUTER_API_KEY", ""):
        target = derive_money_target(ANSWER_CONTEXT, log=lambda _m: None)
    assert target is None


# ─── (d) nullable fields ─────────────────────────────────────────────────────

def test_derive_nullable_money_character_and_object():
    good = ('{"money_character": null, "money_object": null, '
            '"money_event": "The Watcher breaks his oath to intervene", '
            '"query_text": "The Watcher steps between two armies to stop the war"}')
    with patch("stages.money_shot.OPENROUTER_API_KEY", "dummy-key"), \
         patch("stages.money_shot._client") as mock_client:
        _create_mock(mock_client).return_value = _fake_response(good)
        target = derive_money_target(ANSWER_CONTEXT, log=lambda _m: None)

    assert target["money_character"] is None
    assert target["money_object"] is None
    assert target["money_event"]
    assert target["query_text"]


# ─── (e) ocr_money_hits: pure lexical scoring, fixture pages ────────────────

def _page(page_number, panels):
    return {"page_number": page_number, "panels": panels}


def _panel(index, texts):
    return {"index": index, "dialog": [{"text": t} for t in texts]}


def test_ocr_hits_scores_object_and_character_cumulative():
    target = {"money_character": "Beta Ray Bill", "money_object": "Mjolnir"}
    pages = [_page(1, [
        _panel(0, ["Beta Ray Bill lifts Mjolnir!"]),  # both hit -> 2.0 + 1.0
        _panel(1, ["Mjolnir glows"]),                  # object only -> 2.0
        _panel(2, ["Beta Ray Bill roars"]),             # character only -> 1.0
        _panel(3, ["Just some scenery"]),               # no hit -> absent from result
    ])]
    hits = ocr_money_hits(pages, target)
    assert hits == {(1, 0): 3.0, (1, 1): 2.0, (1, 2): 1.0}
    assert (1, 3) not in hits


def test_ocr_hits_word_boundary_excludes_partial_substring_match():
    # "Doom" must NOT match inside "Doomsday" — word-boundary, not raw substring.
    target = {"money_character": "Doom", "money_object": None}
    pages = [_page(1, [_panel(0, ["Doomsday rises from the crater"])])]
    assert ocr_money_hits(pages, target) == {}


def test_ocr_hits_case_insensitive_and_prefers_ocr_field_over_text():
    target = {"money_character": None, "money_object": "excalibur"}
    pages = [_page(2, [{"index": 5, "dialog": [
        {"ocr": "EXCALIBUR", "text": "garbled vlm paraphrase"},
    ]}])]
    assert ocr_money_hits(pages, target) == {(2, 5): 2.0}


def test_ocr_hits_empty_target_returns_empty_dict():
    pages = [_page(1, [_panel(0, ["anything at all"])])]
    assert ocr_money_hits(pages, {}) == {}
