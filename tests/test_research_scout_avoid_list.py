from stages.research_scout import avoid_list
from stages.research_scout.models import ScoutMode


def test_micro_guard_ignores_prompt_cap(monkeypatch):
    labels = [f'Alpha #{n} (2020)' for n in range(1, 56)]
    monkeypatch.setattr(avoid_list, '_inventory', lambda mode: labels)
    assert len(avoid_list.relevant_avoid_lines(ScoutMode.MICRO, 'unrelated')) == 50
    assert ('alpha', '55', '2020') in avoid_list.inventory_issue_keys(ScoutMode.MICRO)


def test_corrupt_snapshot_uses_local_approvals(monkeypatch, tmp_path):
    from stages.research_scout import ledger_shadow
    monkeypatch.setattr(avoid_list.config, 'PROJECTS_ROOT', tmp_path)
    project = tmp_path / 'approved'
    project.mkdir()
    import hashlib, json
    context = project / 'comic_context.json'
    context.write_text(json.dumps({'series_issue_year': 'Alpha #1 (2020)'}))
    (project / 'scout_candidate.json').write_text(json.dumps({'series_issue_year': 'Alpha #1 (2020)'}))
    narration = project / 'narration.json'
    narration.write_text('{"script":"approved"}')
    (project / 'state.json').write_text(json.dumps({
        'approved': {'2': True},
        'approved_narration_sha256': hashlib.sha256(narration.read_bytes()).hexdigest(),
    }))
    monkeypatch.setattr(ledger_shadow, '_read_events', lambda **kwargs: (None, 'export', 'corrupted', 'bad json'))
    assert ('alpha', '1', '2020') in avoid_list.inventory_issue_keys(ScoutMode.MICRO)


def test_micro_inventory_does_not_read_recap_candidates_csv(monkeypatch, tmp_path):
    import csv
    import json
    from stages.research_scout import ledger_shadow
    monkeypatch.setattr(avoid_list.config, 'PROJECTS_ROOT', tmp_path / 'projects')
    csv_path = tmp_path / 'comic_candidates.csv'
    with csv_path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['title', 'year'])
        writer.writeheader()
        writer.writerow({'title': 'Recap Series #1 (2024)', 'year': '2024'})
    monkeypatch.setattr(ledger_shadow, '_read_events', lambda **kwargs: (None, None, 'unavailable', None))
    assert 'Recap Series #1 (2024)' not in avoid_list._inventory(ScoutMode.MICRO)


def test_recap_mode_includes_approved_local_recap(monkeypatch, tmp_path):
    import hashlib
    import json
    from stages.research_scout import ledger_shadow
    monkeypatch.setattr(avoid_list.config, 'PROJECTS_ROOT', tmp_path)
    project = tmp_path / 'recap'
    project.mkdir()
    (project / 'comic_context.json').write_text(json.dumps({
        'series_issue_year': 'Fantastic Four #1 (2024)', 'pipeline_mode': 'narrate_1_comic',
    }))
    narration = project / 'narration.json'
    narration.write_text('{"script":"approved"}')
    (project / 'state.json').write_text(json.dumps({
        'approved': {'2': True},
        'approved_narration_sha256': hashlib.sha256(narration.read_bytes()).hexdigest(),
    }))
    monkeypatch.setattr(ledger_shadow, '_read_events', lambda **kwargs: (None, None, 'unavailable', None))
    labels, _, _, _ = avoid_list.ledger_inventory.load_production_inventory('recap')
    assert labels == ['Fantastic Four #1 (2024)']


def test_question_avoidance_is_scoped_to_qa_mode(monkeypatch, tmp_path):
    from stages.research_scout import ledger_shadow
    monkeypatch.setattr(avoid_list.config, 'PROJECTS_ROOT', tmp_path)
    monkeypatch.setattr(ledger_shadow, '_read_events', lambda **kwargs: (
        [{'id': 'q', 'ts': '2026-01-01T00:00:00Z', 'mode': 'qa', 'kind': 'in_progress',
          'key': '', 'label': 'Who can lift Mjolnir?', 'text': 'Who can lift Mjolnir?', 'keys': []}],
        'export', 'ready', None))
    qa_lines = avoid_list.relevant_question_avoid_lines(ScoutMode.QA)
    micro_lines = avoid_list.relevant_question_avoid_lines(ScoutMode.MICRO)
    assert any('Can Thor really beat the Hulk?' in line for line in qa_lines)
    assert 'Who can lift Mjolnir?' in qa_lines
    assert not any('Can Thor really beat the Hulk?' in line for line in micro_lines)
    assert 'Who can lift Mjolnir?' not in micro_lines


def test_relevant_avoid_lines_keeps_qa_banlist_for_string_mode(monkeypatch, tmp_path):
    from stages.research_scout import ledger_shadow
    monkeypatch.setattr(avoid_list.config, 'PROJECTS_ROOT', tmp_path)
    monkeypatch.setattr(ledger_shadow, '_read_events', lambda **kwargs: (None, None, 'unavailable', None))
    lines = avoid_list.relevant_avoid_lines('qa', 'Marvel DC', limit=50)
    assert 'Marvel/DC #1 (2025)' in lines
