"""panel_dialog / page_dialog read a page's dialog in either schema (nested under each panel, or the
old flat page-level text_blocks). Pure logic — fake page dicts, no network."""
from stages._panel_index import panel_dialog, page_dialog


def test_panel_dialog_nested_vs_flat_fallback():
    nested = {"index": 1, "dialog": [{"text": "A"}, {"text": "B"}]}
    assert panel_dialog(nested) == [{"text": "A"}, {"text": "B"}]
    # old cached schema: no nested dialog → filter page-level text_blocks by panel_index
    old = {"index": 1}
    page_tb = [{"panel_index": 0, "text": "X"}, {"panel_index": 1, "text": "Y"}]
    assert panel_dialog(old, page_tb) == [{"panel_index": 1, "text": "Y"}]


def test_panel_dialog_index_zero_is_a_real_index():
    """`panel.get("index") or -999` would turn panel 0 into -999 and drop its dialog."""
    old = {"index": 0}
    page_tb = [{"panel_index": 0, "text": "X"}, {"panel_index": 1, "text": "Y"}]
    assert panel_dialog(old, page_tb) == [{"panel_index": 0, "text": "X"}]


def test_page_dialog_flattens_panels_or_uses_old_text_blocks():
    new_page = {"panels": [{"index": 0, "dialog": [{"text": "A"}]},
                           {"index": 1, "dialog": [{"text": "B"}]}]}
    assert [d["text"] for d in page_dialog(new_page)] == ["A", "B"]
    old_page = {"panels": [], "text_blocks": [{"text": "Z"}]}
    assert page_dialog(old_page) == [{"text": "Z"}]
