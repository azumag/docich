"""Synthetic month-boundary tests for the zero-sortie audit."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from docich.hanjuku import month_audit as audit

IDENTITY = {'game': 'hanjuku-hero', 'runtime_id': 'g1-abcd1234',
            'generation': 1, 'lease_id': 'synthetic-lease'}
STEP = 1.5


def state(chapter, month, tick, *, launches=0, scope=None, plan=None, world=None, flow=None):
    return {
        'policy': {
            'chapter': chapter,
            'month': month,
            'tick': tick,
            'month_start_tick': 0,
            'month_sorties': {'scope': scope if scope is not None else [chapter, month],
                              'launches': launches},
            'plan': plan if plan is not None else {'orders': {}, 'chart_plan': {}},
            'world': world if world is not None else {
                'garrison': {'S': ['G']}, 'sorties': {}, 'launched_orders': {}, 'gold': 10},
            'flow': flow if flow is not None else {},
            'stats': {},
        },
        'step': tick,
        'decision_trace': dict(IDENTITY),
        'screen_kind': 'map',
        'phase': 'field',
        'bot_version': 'synthetic-v1',
    }


def note(runtime, at, decision='situation_held', **fields):
    row = {'schema': 1, 'event': 'decision', 'at': at, 'decision': decision,
           **IDENTITY, **fields}
    with (runtime / 'hanjuku_decisions.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + '\n')


def run_month(runtime, chapter, month, start, count, *, launches=0, launch_at=None,
              launch_purpose='attack', **kwargs):
    last = None
    for index in range(count):
        at = start + index * STEP
        last = state(chapter, month, index, launches=launches, **kwargs)
        audit.observe(runtime, audit.capture(last), last, [], now=at)
        if launch_at == index:
            note(runtime, at, 'order_launched', chart_step='A:1:M1',
                 purpose=launch_purpose, general='G', target='T')
        else:
            note(runtime, at)
    return last


def boundary(runtime, old, new, at):
    audit.observe(runtime, audit.capture(old), new, [], now=at)


def prepare_zero(runtime):
    """One partial month, one full zero month and its boundary."""
    old = run_month(runtime, 1, '1-1', 1000, 100)
    new = state(1, '1-2', 200, launches=0)
    boundary(runtime, old, new, 1200)
    old = run_month(runtime, 1, '1-2', 1200, 100, launches=0)
    new = state(1, '1-3', 400, launches=0)
    boundary(runtime, old, new, 1380)
    return old, new


def sealed_zero(runtime):
    old, new = prepare_zero(runtime)
    later = state(1, '1-3', 500, launches=0)
    for index in range(5):
        audit.observe(runtime, audit.capture(new), later, [], now=1700 + index * STEP)
        note(runtime, 1700 + index * STEP)
    records = audit.load_incidents(runtime)
    assert len(records) == 1
    return records[0]


def test_full_zero_month_is_recorded_and_does_not_count_as_ok(runtime_dir):
    old, new = prepare_zero(runtime_dir)
    pending = json.loads((runtime_dir / audit.AUDIT_FILE).read_text())['pending']
    record = pending['incident']
    assert record['kind'] == 'zero_sortie'
    assert record['confidence'] == 'high'
    assert record['chapter'] == 1 and record['month'] == '1-2'
    assert record['sorties']['confirmed'] == 0
    assert record['sorties']['departures'] == 0
    assert record['month_summary']['observations'] >= audit.MIN_OBSERVATIONS
    assert record['month_summary']['duration_s'] >= audit.MIN_MONTH_SECONDS
    assert record['auto_file'] is True


def test_month_with_a_departure_is_not_recorded(runtime_dir):
    old = run_month(runtime_dir, 1, '1-1', 1000, 100)
    boundary(runtime_dir, old, state(1, '1-2', 200), 1200)
    old = run_month(runtime_dir, 1, '1-2', 1200, 100, launches=1, launch_at=40)
    boundary(runtime_dir, old, state(1, '1-3', 400), 1380)
    saved = json.loads((runtime_dir / audit.AUDIT_FILE).read_text())
    assert saved['last_close']['kind'] == 'ok'
    assert saved['pending'] is None


def test_move_only_month_is_still_zero(runtime_dir):
    old = run_month(runtime_dir, 1, '1-1', 1000, 100)
    boundary(runtime_dir, old, state(1, '1-2', 200), 1200)
    old = run_month(runtime_dir, 1, '1-2', 1200, 100, launches=0,
                    launch_at=40, launch_purpose='move')
    boundary(runtime_dir, old, state(1, '1-3', 400), 1380)
    record = json.loads((runtime_dir / audit.AUDIT_FILE).read_text())['pending']['incident']
    assert record['kind'] == 'zero_sortie'
    assert record['sorties']['departures'] == 0
    assert record['sorties']['moves'] == 1


def test_unconfirmed_departure_is_recorded_but_not_auto_filed(runtime_dir):
    old = run_month(runtime_dir, 1, '1-1', 1000, 100)
    boundary(runtime_dir, old, state(1, '1-2', 200), 1200)
    old = run_month(runtime_dir, 1, '1-2', 1200, 100, launches=0, launch_at=40)
    boundary(runtime_dir, old, state(1, '1-3', 400), 1380)
    record = json.loads((runtime_dir / audit.AUDIT_FILE).read_text())['pending']['incident']
    assert record['kind'] == 'unconfirmed_launches'
    assert record['sorties']['confirmed'] == 0
    assert record['sorties']['departures'] == 1
    assert record['auto_file'] is False


def test_first_partial_month_is_skipped(runtime_dir):
    old = run_month(runtime_dir, 1, '1-1', 1000, 100)
    boundary(runtime_dir, old, state(1, '1-2', 200), 1200)
    saved = json.loads((runtime_dir / audit.AUDIT_FILE).read_text())
    assert saved['last_close']['kind'] == 'skipped'
    assert saved['pending'] is None


def test_stale_ledger_is_recorded_as_unverified(runtime_dir):
    old = run_month(runtime_dir, 1, '1-1', 1000, 100)
    boundary(runtime_dir, old, state(1, '1-2', 200, scope=[1, '1-2']), 1200)
    old = run_month(runtime_dir, 1, '1-2', 1200, 100, launches=0, scope=[1, '1-0'])
    boundary(runtime_dir, old, state(1, '1-3', 400), 1380)
    record = json.loads((runtime_dir / audit.AUDIT_FILE).read_text())['pending']['incident']
    assert record['kind'] == 'zero_unverified'
    assert record['confidence'] == 'low'
    assert 'ledger_scope_mismatch' in record['confidence_reasons']
    assert record['sorties']['confirmed'] is None
    assert record['auto_file'] is False


def test_chapter_change_is_skipped_and_next_month_is_partial(runtime_dir):
    old = run_month(runtime_dir, 1, '1-1', 1000, 100)
    boundary(runtime_dir, old, state(1, '1-2', 200), 1200)
    old = run_month(runtime_dir, 1, '1-2', 1200, 100, launches=1, launch_at=10)
    boundary(runtime_dir, old, state(2, '1-1', 400), 1380)
    saved = json.loads((runtime_dir / audit.AUDIT_FILE).read_text())
    assert saved['last_close']['kind'] == 'skipped'
    assert saved['last_close']['confidence'] == 'low'
    assert saved['observed']['started_clean'] is False
    assert saved['pending'] is None


def test_observation_gap_downgrades_a_zero_month(runtime_dir):
    old = run_month(runtime_dir, 1, '1-1', 1000, 100)
    boundary(runtime_dir, old, state(1, '1-2', 200), 1200)
    old = run_month(runtime_dir, 1, '1-2', 1200, 100, launches=0)
    # A 100 s outage is longer than the accepted observation gap.
    boundary(runtime_dir, old, state(1, '1-3', 400), 1448.5)
    record = json.loads((runtime_dir / audit.AUDIT_FILE).read_text())['pending']['incident']
    assert record['kind'] == 'zero_unverified'
    assert 'observation_gap' in record['confidence_reasons']
    assert record['auto_file'] is False


def test_short_month_is_not_auto_filed(runtime_dir):
    old = run_month(runtime_dir, 1, '1-1', 1000, 100)
    boundary(runtime_dir, old, state(1, '1-2', 200), 1200)
    old = run_month(runtime_dir, 1, '1-2', 1200, 60, launches=0)
    boundary(runtime_dir, old, state(1, '1-3', 400), 1300)
    record = json.loads((runtime_dir / audit.AUDIT_FILE).read_text())['pending']['incident']
    assert record['kind'] == 'zero_unverified'
    assert 'short_month' in record['confidence_reasons']


def test_pending_is_sealed_once_and_has_after_window(runtime_dir):
    record = sealed_zero(runtime_dir)
    assert record['after']['complete'] is True
    assert record['after']['early'] is False
    before = len(audit.load_incidents(runtime_dir))
    later = state(1, '1-3', 600, launches=0)
    for index in range(3):
        audit.observe(runtime_dir, audit.capture(later), later, [], now=1800 + index * STEP)
    assert len(audit.load_incidents(runtime_dir)) == before


def test_new_boundary_seals_the_previous_pending_early(runtime_dir):
    prepare_zero(runtime_dir)
    # The 1-3 month closes before the 1-2 after window ends.
    last = state(1, '1-3', 500, launches=0)
    boundary(runtime_dir, last, state(1, '1-4', 600, launches=0), 1500)
    records = audit.load_incidents(runtime_dir)
    assert len(records) == 1
    assert records[0]['after']['early'] is True
    assert records[0]['month'] == '1-2'


def test_malformed_state_does_not_raise(runtime_dir):
    (runtime_dir / audit.AUDIT_FILE).write_text('{not json', encoding='utf-8')
    value = audit.observe(runtime_dir, None, {'policy': {}}, [], now=1.0)
    assert value is None
    value = audit.observe(runtime_dir, None, state(1, '1-1', 0), [], now=1.0)
    assert value is not None


def test_capture_is_bounded(runtime_dir):
    plan = {'orders': {f'S{i}': 'pending' for i in range(100)},
            'chart_plan': {'orders': [
                {'step': f'S{i}', 'note': 'x' * 400, 'general': 'g' * 100}
                for i in range(100)]}}
    world = {'garrison': {f'C{i}': [f'G{j}' for j in range(100)] for i in range(100)},
             'sorties': {f'S{i}': {'status': 'en_route', 'tick': 0} for i in range(100)},
             'launched_orders': {f'S{i}': {'target': 'T'} for i in range(100)},
             'captured': [f'C{i}' for i in range(100)]}
    value = audit.capture(state(1, '1-1', 10, plan=plan, world=world))
    assert len(value['plan']['orders']) <= 60
    assert len(value['plan']['chart_plan']['orders']) <= audit.MAX_LIST
    assert len(value['world']['garrison']) <= 12
    assert all(len(names) <= 12 for names in value['world']['garrison'].values())
    assert len(value['world']['sorties']) <= audit.MAX_LIST


def test_render_contains_markers_and_evidence(runtime_dir):
    record = sealed_zero(runtime_dir)
    text = audit.render(record)
    assert 'hanjuku-month-audit fingerprint=' in text
    assert f'occurrence={record["incident_id"]}' in text
    assert audit.TITLE_PREFIX in text
    assert '月内の決定' in text
    assert record['incident_id'] in text


def test_issue_title_uses_the_primary_signal():
    assert audit._issue_title({'chapter': 1, 'signature': 'unread'}) == (
        '[半熟英雄] 月内出撃ゼロ: ch1 未読画面で保留')
    assert audit._issue_title({'chapter': 2, 'signature': 'departure_failure'}) == (
        '[半熟英雄] 月内出撃ゼロ: ch2 出撃開始後に発射されない')


def test_render_comment_is_compact(runtime_dir):
    record = sealed_zero(runtime_dir)
    text = audit.render_comment(record)
    assert '### 再発' in text
    assert f'occurrence={record["incident_id"]}' in text
    assert len(text) < 4000


def test_record_size_is_bounded(runtime_dir):
    record = sealed_zero(runtime_dir)
    assert len(json.dumps(record, ensure_ascii=False).encode('utf-8')) <= audit.MAX_RECORD_BYTES


class FakeGh:
    def __init__(self, issues=None, comments=None):
        self.issues = issues if issues is not None else []
        self.comments = comments if comments is not None else []
        self.calls = []
        self.comment_body = None

    def __call__(self, args):
        self.calls.append(list(args))
        if args[:2] == ['auth', 'status']:
            return 0, '', ''
        if args[:2] == ['issue', 'list']:
            return 0, json.dumps(self.issues), ''
        if args[:2] == ['issue', 'view']:
            return 0, json.dumps({'comments': self.comments}), ''
        if args[:2] == ['issue', 'create']:
            return 0, 'https://github.com/azumag/docich/issues/1', ''
        if args[:2] == ['issue', 'comment']:
            path = args[args.index('--body-file') + 1]
            self.comment_body = Path(path).read_text(encoding='utf-8')
            return 0, 'https://github.com/azumag/docich/issues/1#c', ''
        return 1, '', 'unexpected'


def test_file_creates_one_issue_with_labels(runtime_dir):
    record = sealed_zero(runtime_dir)
    gh = FakeGh()
    result = audit.file_issue(runtime_dir, gh=gh, scanner=lambda path: True, now=time.time())
    assert result['status'] == 'created'
    create = [call for call in gh.calls if call[:2] == ['issue', 'create']]
    assert len(create) == 1
    assert '--label' in create[0] and 'auto-generated' in create[0]
    assert record['fingerprint'] in result['fingerprint']


def test_file_comments_on_the_matching_open_issue(runtime_dir):
    record = sealed_zero(runtime_dir)
    issue = {'number': 7, 'body': f"<!-- hanjuku-month-audit fingerprint={record['fingerprint']} -->"}
    gh = FakeGh(issues=[issue], comments=[])
    result = audit.file_issue(runtime_dir, gh=gh, scanner=lambda path: True, now=time.time())
    assert result['status'] == 'commented'
    assert result['issue'] == 7
    assert '### 再発' in gh.comment_body


def test_file_does_not_repeat_the_same_occurrence(runtime_dir):
    record = sealed_zero(runtime_dir)
    issue = {'number': 7, 'body': f"<!-- hanjuku-month-audit fingerprint={record['fingerprint']} -->"}
    comments = [{'body': f"<!-- hanjuku-month-audit occurrence={record['incident_id']} -->",
                 'createdAt': '2026-11-20T00:00:00Z'}]
    gh = FakeGh(issues=[issue], comments=comments)
    result = audit.file_issue(runtime_dir, gh=gh, scanner=lambda path: True, now=time.time())
    assert result['status'] == 'already_commented'


def test_file_respects_comment_cooldown(runtime_dir):
    record = sealed_zero(runtime_dir)
    issue = {'number': 7, 'body': f"<!-- hanjuku-month-audit fingerprint={record['fingerprint']} -->"}
    now = time.time()
    comments = [{'body': 'earlier', 'createdAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now - 60))}]
    gh = FakeGh(issues=[issue], comments=comments)
    result = audit.file_issue(runtime_dir, gh=gh, scanner=lambda path: True, now=now)
    assert result['status'] == 'cooldown'


def test_file_refuses_when_secret_scan_detects(runtime_dir):
    sealed_zero(runtime_dir)
    gh = FakeGh()
    with pytest.raises(audit.AuditError):
        audit.file_issue(runtime_dir, gh=gh, scanner=lambda path: False, now=time.time())
    assert not [call for call in gh.calls if call[:2] == ['issue', 'create']]


def test_file_dry_run_never_calls_gh(runtime_dir):
    sealed_zero(runtime_dir)

    def explode(args):
        raise AssertionError('gh must not run in dry-run')

    result = audit.file_issue(runtime_dir, dry_run=True, gh=explode, scanner=lambda path: True)
    assert result['status'] == 'dry_run'
    assert result['body']


def test_file_min_confidence_filters(runtime_dir):
    sealed_zero(runtime_dir)
    log = runtime_dir / f'{audit.LOG_NAME}.jsonl'
    rows = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines() if line]
    rows[-1]['confidence'] = 'medium'
    rows[-1]['auto_file'] = True
    log.write_text('\n'.join(json.dumps(row, ensure_ascii=False) for row in rows) + '\n',
                   encoding='utf-8')
    assert audit.file_issue(runtime_dir, gh=FakeGh(), scanner=lambda path: True)['status'] == 'no_incident'
    assert audit.file_issue(runtime_dir, gh=FakeGh(), scanner=lambda path: True,
                            min_confidence='medium')['status'] == 'created'


def test_watch_once_files_new_and_skips_unchanged(runtime_dir):
    sealed_zero(runtime_dir)
    gh = FakeGh()
    seen = {}
    state_dir = runtime_dir.parent.parent
    results = audit.watch_once(state_dir, gh=gh, scanner=lambda path: True,
                               now=time.time(), seen=seen)
    assert [row['status'] for row in results] == ['created']
    assert audit.watch_once(state_dir, gh=gh, scanner=lambda path: True,
                            now=time.time(), seen=seen) == []


def test_load_incidents_skips_malformed_lines(runtime_dir):
    path = runtime_dir / f'{audit.LOG_NAME}.jsonl'
    path.write_text('{bad\n' + json.dumps({'schema': 1, 'incident_id': 'x'}) + '\n',
                    encoding='utf-8')
    rows = audit.load_incidents(runtime_dir)
    assert [row['incident_id'] for row in rows] == ['x']


@pytest.fixture
def runtime_dir(tmp_path):
    directory = tmp_path / 'runtimes' / 'g1-abcd1234'
    directory.mkdir(parents=True)
    return directory


def test_shrink_singleton_and_after_rows():
    record = {'decisions': {'before': ['x' * audit.MAX_RECORD_BYTES]},
              'after': {'rows': ['y' * audit.MAX_RECORD_BYTES]}}
    result = audit._shrink(record)
    assert result['decisions']['before'] == []
    assert result['after']['rows'] == []
    assert result['truncated'] is True
    assert len(json.dumps(result, ensure_ascii=False).encode('utf-8')) <= audit.MAX_RECORD_BYTES


def test_shrink_oversize_required_payload_fails_closed():
    with pytest.raises(audit.AuditError):
        audit._shrink({'required': 'x' * audit.MAX_RECORD_BYTES})


def test_file_deduplicates_occurrence_in_issue_body(runtime_dir):
    record = sealed_zero(runtime_dir)
    gh = FakeGh(issues=[{'number': 7, 'body': audit.render(record)}])
    result = audit.file_issue(runtime_dir, gh=gh, scanner=lambda path: True)
    assert result['status'] == 'already_commented'
    assert not any(call[:2] == ['issue', 'comment'] for call in gh.calls)


def test_watch_retries_unchanged_log_after_cooldown(runtime_dir):
    record = sealed_zero(runtime_dir)
    now = time.time()
    issue = {'number': 7, 'body': f"<!-- hanjuku-month-audit fingerprint={record['fingerprint']} -->"}
    comments = [{'body': 'earlier', 'createdAt': time.strftime(
        '%Y-%m-%dT%H:%M:%SZ', time.gmtime(now - 60))}]
    gh = FakeGh(issues=[issue], comments=comments)
    seen = {}
    state_dir = runtime_dir.parent.parent
    assert audit.watch_once(state_dir, gh=gh, scanner=lambda path: True,
                            now=now, seen=seen)[0]['status'] == 'cooldown'
    assert not seen
    assert audit.watch_once(state_dir, gh=gh, scanner=lambda path: True,
                            now=now + audit.COMMENT_COOLDOWN_SECONDS + 1,
                            seen=seen)[0]['status'] == 'commented'
