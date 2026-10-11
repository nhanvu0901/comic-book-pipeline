import hashlib
import json

from stages.research_scout import ledger_shadow
from stages.research_scout.ledger_inventory import load_production_inventory
from stages.research_scout.models import ScoutMode


def _project(root, name, *, label, approved=True, narration='story', final=False):
    path = root / name
    path.mkdir()
    (path / 'comic_context.json').write_text(json.dumps({'series_issue_year': label}))
    (path / 'scout_candidate.json').write_text(json.dumps({'series_issue_year': label}))
    (path / 'narration.json').write_text(json.dumps({'script': narration}))
    state = {'approved': {'2': approved}}
    if approved:
        state['approved_narration_sha256'] = hashlib.sha256(
            (path / 'narration.json').read_bytes()
        ).hexdigest()
    (path / 'state.json').write_text(json.dumps(state))
    if final:
        (path / 'final.mp4').write_bytes(b'video')
    return path


def _event(mode, kind, key, label='', *, keys=None, text=''):
    return {'id': f'{mode}-{kind}-{key or text}', 'ts': '2026-01-01T00:00:00Z',
            'mode': mode, 'kind': kind, 'key': key, 'keys': keys or [],
            'label': label, 'text': text, 'refs': {}, 'entities': [],
            'scope': 'item', 'reason_code': ''}


def test_bare_context_is_not_avoided(tmp_path, monkeypatch):
    from stages.research_scout import avoid_list
    monkeypatch.setattr(avoid_list.config, 'PROJECTS_ROOT', tmp_path)
    _project(tmp_path, 'bare', label='Nightwing #1 (2024)', approved=False)
    assert not any('Nightwing' in x for x in avoid_list._inventory(ScoutMode.MICRO))


def test_approved_narration_is_avoided(tmp_path, monkeypatch):
    from stages.research_scout import avoid_list
    monkeypatch.setattr(avoid_list.config, 'PROJECTS_ROOT', tmp_path)
    _project(tmp_path, 'approved', label='Nightwing #1 (2024)')
    assert any('Nightwing' in x for x in avoid_list._inventory(ScoutMode.MICRO))


def test_hash_mismatch_is_not_approved(tmp_path, monkeypatch):
    from stages.research_scout import avoid_list
    monkeypatch.setattr(avoid_list.config, 'PROJECTS_ROOT', tmp_path)
    path = _project(tmp_path, 'changed', label='Nightwing #1 (2024)')
    (path / 'narration.json').write_text('{"script":"edited"}')
    assert not any('Nightwing' in x for x in avoid_list._inventory(ScoutMode.MICRO))


def test_unapproved_partial_render_is_not_an_approval_or_hard_hold(tmp_path, monkeypatch):
    from stages.research_scout import avoid_list
    monkeypatch.setattr(avoid_list.config, 'PROJECTS_ROOT', tmp_path)
    project = _project(tmp_path, 'partial', label='Nightwing #1 (2024)', approved=False, final=True)
    (project / 'final.mp4').write_bytes(b'partial ffmpeg output')
    assert not any('Nightwing' in x for x in avoid_list._inventory(ScoutMode.MICRO))


def test_deleted_project_remains_avoided_from_snapshot(tmp_path, monkeypatch):
    from stages.research_scout import avoid_list
    monkeypatch.setattr(avoid_list.config, 'PROJECTS_ROOT', tmp_path / 'projects')
    events = [_event('micro', 'in_progress', 'nightwing|2016|1', 'Nightwing #1 (2024)')]
    monkeypatch.setattr(ledger_shadow, '_read_events', lambda **kwargs: (events, 'export', 'ready', None))
    labels, keys, _, status = load_production_inventory(ScoutMode.MICRO)
    assert 'Nightwing #1 (2024)' in labels
    assert ('nightwing', '1', '2024') in keys
    assert status == 'export:ready'


def test_qa_question_text_is_avoided(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger_shadow, '_read_events', lambda **kwargs: (
        [_event('qa', 'in_progress', '', text='Who can lift Mjolnir?')], 'export', 'ready', None))
    _, _, questions, _ = load_production_inventory(ScoutMode.QA)
    assert 'Who can lift Mjolnir?' in questions


def test_relaunches_do_not_collapse(tmp_path, monkeypatch):
    from stages.research_scout import avoid_list
    monkeypatch.setattr(avoid_list.config, 'PROJECTS_ROOT', tmp_path)
    events = [
        _event('micro', 'in_progress', 'nightwing|1996|1', 'Nightwing #1 (1996)'),
        _event('micro', 'in_progress', 'nightwing|2016|1', 'Nightwing #1 (2016)'),
    ]
    monkeypatch.setattr(ledger_shadow, '_read_events', lambda **kwargs: (events, 'export', 'ready', None))
    assert avoid_list.inventory_issue_keys(ScoutMode.MICRO) == {
        ('nightwing', '1', '1996'), ('nightwing', '1', '2016')}


def test_series_start_year_before_issue_is_not_a_publication_year(tmp_path, monkeypatch):
    from stages.research_scout import avoid_list, workflow
    monkeypatch.setattr(avoid_list.config, 'PROJECTS_ROOT', tmp_path)
    events = [_event('micro', 'in_progress', 'nightwing|2016|1', 'Nightwing (2016) #1')]
    monkeypatch.setattr(ledger_shadow, '_read_events', lambda **kwargs: (events, 'export', 'ready', None))
    labels, keys, _, _ = load_production_inventory(ScoutMode.MICRO)
    assert labels == ['Nightwing (2016) #1']
    assert keys == set()
    assert avoid_list._key('Nightwing (2016) #1') is None
    assert workflow._issue_key('Nightwing (2016) #1') is None


def test_approved_local_qa_keeps_question_and_issue_labels(tmp_path, monkeypatch):
    from stages.research_scout import avoid_list
    monkeypatch.setattr(avoid_list.config, 'PROJECTS_ROOT', tmp_path)
    project = tmp_path / 'qa'
    project.mkdir()
    (project / 'answer_context.json').write_text(json.dumps({
        'question': 'Who can lift Mjolnir?',
        'items': [{'source_comic': 'Thor #1 (2023)', 'source_year': '2023'}],
    }))
    narration = project / 'narration.json'
    narration.write_text('{"script":"approved"}')
    (project / 'state.json').write_text(json.dumps({
        'approved': {'2': True},
        'approved_narration_sha256': hashlib.sha256(narration.read_bytes()).hexdigest(),
    }))
    monkeypatch.setattr(ledger_shadow, '_read_events', lambda **kwargs: (None, None, 'unavailable', None))
    labels, _, questions, _ = load_production_inventory(ScoutMode.QA)
    assert 'Thor #1 (2023)' in labels
    assert 'Who can lift Mjolnir?' in questions


def test_qa_questions_are_returned_in_stable_sorted_order(monkeypatch, tmp_path):
    from stages.research_scout import avoid_list
    monkeypatch.setattr(avoid_list.config, 'PROJECTS_ROOT', tmp_path)
    events = [
        _event('qa', 'in_progress', '', label='a Question?', text='a Question?'),
        _event('qa', 'in_progress', '', label='A question?', text='A question?'),
    ]
    monkeypatch.setattr(ledger_shadow, '_read_events', lambda **kwargs: (events, 'export', 'ready', None))
    _, _, questions, _ = load_production_inventory(ScoutMode.QA)
    assert questions == ['A question?', 'a Question?']


def test_only_active_state_labels_contribute_hard_keys(monkeypatch, tmp_path):
    from stages.research_scout import avoid_list
    monkeypatch.setattr(avoid_list.config, 'PROJECTS_ROOT', tmp_path)
    events = [
        _event('micro', 'in_progress', 'alpha|1999|1', 'Alpha #1 (2024)'),
        _event('micro', 'proposed', 'alpha|1999|1', 'Alpha #1 (2025)'),
    ]
    monkeypatch.setattr(ledger_shadow, '_read_events', lambda **kwargs: (events, 'export', 'ready', None))
    labels, keys, _, _ = load_production_inventory(ScoutMode.MICRO)
    assert labels == ['Alpha #1 (2024)']
    assert keys == {('alpha', '1', '2024')}


def test_structured_local_context_builds_issue_identity_like_ledger(tmp_path, monkeypatch):
    from stages.research_scout import avoid_list
    monkeypatch.setattr(avoid_list.config, 'PROJECTS_ROOT', tmp_path)
    micro = tmp_path / 'micro'
    micro.mkdir()
    (micro / 'comic_context.json').write_text(json.dumps({
        'pipeline_mode': 'micro_moment', 'series': 'Nightwing (2016)',
        'issue': '1', 'source_year': '2024',
    }))
    narration = micro / 'narration.json'
    narration.write_text('{"script":"approved"}')
    (micro / 'state.json').write_text(json.dumps({
        'approved': {'2': True},
        'approved_narration_sha256': hashlib.sha256(narration.read_bytes()).hexdigest(),
    }))
    recap = tmp_path / 'recap'
    recap.mkdir()
    (recap / 'comic_context.json').write_text(json.dumps({
        'series': 'Fantastic Four', 'issue': '1', 'year': '2023',
    }))
    narration = recap / 'narration.json'
    narration.write_text('{"script":"approved"}')
    (recap / 'state.json').write_text(json.dumps({
        'approved': {'2': True},
        'approved_narration_sha256': hashlib.sha256(narration.read_bytes()).hexdigest(),
    }))
    monkeypatch.setattr(ledger_shadow, '_read_events', lambda **kwargs: (None, None, 'unavailable', None))
    micro_labels, micro_keys, _, _ = load_production_inventory(ScoutMode.MICRO)
    recap_labels, _, _, _ = load_production_inventory('recap')
    assert micro_labels == ['Nightwing (2016) #1 (2024)']
    assert micro_keys == {('nightwing', '1', '2024')}
    assert recap_labels == ['Fantastic Four #1 (2023)']
