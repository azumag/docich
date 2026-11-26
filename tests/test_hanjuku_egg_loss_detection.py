"""卵を残して敗北の検知（egg-loss detection）の回帰テスト。

実戦闘の記録形に合わせるため、mem に battle cur を組んで
``flows.battle.battle_end`` を実際に呼び、出た ``battle_result`` を
``month_audit.observe`` に渡す。レコードへ 'general' は足さない
（実形は ally のみ）。
"""
from __future__ import annotations

import json
import time

import pytest

from docich.hanjuku import month_audit as audit
from docich.hanjuku.flows import battle as B
from docich.hanjuku.flows import battle_menus as BM

IDENTITY = {'game': 'hanjuku-hero', 'runtime_id': 'g1-eggcase',
            'generation': 1, 'lease_id': 'synthetic-lease'}
CHAPTER = 9
MONTH = '1-1'


@pytest.fixture
def runtime_dir(tmp_path):
    directory = tmp_path / 'runtimes' / 'g1-eggcase'
    directory.mkdir(parents=True)
    return directory


def memory(**battle_changes):
    battle = {
        'ally': 'キッシュ', 'enemy': 'ハデス', 'castle': 'S', 'side': 'attack',
        'step': 'A:1:M1', 'ally_hp': 0, 'enemy_hp': 40, 'away': 1,
        'planned_cards': [], 'cards_used': [], 'card_evidence_version': 1,
    }
    battle.update(battle_changes)
    return {
        'chapter': CHAPTER, 'month': MONTH, 'tick': 500,
        '_records': [], 'captured': [], 'lost': [],
        'world': {'garrison': {}, 'sorties': {}, 'launched_orders': {},
                  'gold': 10},
        'egg_uses': {'キッシュ': 0},
        'battle': battle,
        'stats': {'wins': 0, 'losses': 0, 'unclassified': 0, 'cards_used': 0,
                  'generals_lost': None, 'cards_confirmed': 0,
                  'card_evidence_version': 1},
    }


def observation(mem, *, tick=500):
    mem['tick'] = tick
    mem['chapter'] = CHAPTER
    mem['month'] = MONTH
    return {'policy': mem, 'step': tick, 'decision_trace': dict(IDENTITY),
            'screen_kind': 'map', 'phase': 'field',
            'bot_version': 'synthetic-v1'}


def run_battle(runtime, mem, *, now=1000):
    B.battle_end(mem, 'map')
    after = observation(mem, tick=500)
    before = audit.capture(observation(memory(), tick=499))
    records = [row for row in mem['_records'] if row['decision'] == 'battle_result']
    audit.observe(runtime, before, after, records, now=now)
    return audit.load_incidents(runtime)


def egg_fingerprint(kind='battle_loss_with_resources'):
    return audit._fingerprint(CHAPTER, kind, [kind, f'{kind}_with_egg'])


def card_fingerprint(kind='battle_loss_with_resources'):
    return audit._fingerprint(CHAPTER, kind, [kind])


def test_t1_normal_menu_white_row_then_conserve_is_a_high_egg_loss(runtime_dir):
    mem = memory(egg_white_seen=True, egg_selected=0, egg_battle=False)
    mem['egg_uses'] = {'キッシュ': 2}
    incidents = run_battle(runtime_dir, mem)
    assert len(incidents) == 1
    incident = incidents[0]
    assert incident['confidence'] == 'high'
    assert incident['battle_event']['resources'] == ['egg']
    assert incident['fingerprint'] == egg_fingerprint()
    assert incident['battle_event']['egg_selected'] == 0
    assert incident['battle_event']['egg_uses_ledger'] == 2


def test_t2_selected_egg_then_melee_loss_is_not_reported(runtime_dir):
    mem = memory(egg_white_seen=True, egg_selected=0, egg_battle=False)
    BM._note_egg_selected(mem)
    assert mem['battle']['egg_selected'] == 1
    assert mem['battle']['egg_white_seen'] is False
    mem['battle']['egg_summon_seen'] = True
    mem['battle']['egg_battle'] = False       # panel re-read reset the latch
    mem['egg_uses'] = {'キッシュ': 1}
    incidents = run_battle(runtime_dir, mem)
    assert incidents == []


def test_t3_summon_menu_white_row_then_loss_is_a_high_egg_loss(runtime_dir):
    mem = memory(egg_white_seen=True, egg_selected=0, egg_battle=True,
                 egg_summon_seen=True)
    mem['egg_uses'] = {'キッシュ': 2}
    incidents = run_battle(runtime_dir, mem)
    assert len(incidents) == 1
    assert incidents[0]['confidence'] == 'high'
    assert incidents[0]['battle_event']['resources'] == ['egg']
    assert incidents[0]['fingerprint'] == egg_fingerprint()


def test_t4_grey_egg_row_in_summon_battle_is_not_reported(runtime_dir):
    # 灰色行は白行ではないので egg_white_seen は立たない。台帳が正でも
    # 召喚戦に入っているので残存の証拠にしない。
    mem = memory(egg_white_seen=False, egg_selected=0, egg_battle=True,
                 egg_summon_seen=True)
    mem['egg_uses'] = {'キッシュ': 3}
    incidents = run_battle(runtime_dir, mem)
    assert incidents == []


def test_t5_ledger_only_positive_is_a_high_egg_loss(runtime_dir):
    mem = memory(egg_white_seen=False, egg_selected=0, egg_battle=False)
    mem['egg_uses'] = {'キッシュ': 3}
    incidents = run_battle(runtime_dir, mem)
    assert len(incidents) == 1
    event = incidents[0]['battle_event']
    assert event['resources'] == ['egg']
    assert event['egg_ledger_only'] is True
    assert event['egg_uses_ledger'] == 3
    assert incidents[0]['confidence'] == 'high'


def test_t6_ledger_positive_after_a_selection_is_not_a_leftover(runtime_dir):
    mem = memory(egg_white_seen=False, egg_selected=1, egg_battle=False)
    mem['egg_uses'] = {'キッシュ': 3}
    incidents = run_battle(runtime_dir, mem)
    assert incidents == []


def test_t7_egg_plus_unconfirmed_card_stays_high_with_a_reason(runtime_dir):
    mem = memory(egg_white_seen=True, egg_selected=0, egg_battle=False,
                 cards_selected=['ブラッキー'], cards_used=[],
                 cards_unclassified=['ブラッキー'])
    incidents = run_battle(runtime_dir, mem)
    assert len(incidents) == 1
    incident = incidents[0]
    assert incident['confidence'] == 'high'
    assert incident['confidence_reasons'] == [
        'cards_unconfirmed_signature_card_only']
    assert incident['battle_event']['resources'] == ['egg', 'card']
    assert incident['fingerprint'] == egg_fingerprint()


def test_t8_unconfirmed_card_only_stays_medium(runtime_dir):
    mem = memory(egg_white_seen=False, egg_selected=0, egg_battle=False,
                 cards_selected=['ブラッキー'], cards_used=[],
                 cards_unclassified=['ブラッキー'])
    incidents = run_battle(runtime_dir, mem)
    assert len(incidents) == 1
    assert incidents[0]['confidence'] == 'medium'
    assert incidents[0]['confidence_reasons'] == ['cards_unconfirmed_signature']
    assert incidents[0]['battle_event']['resources'] == ['card']


def test_t9_unclassified_ally_zero_enemy_unread_is_a_loss(runtime_dir):
    mem = memory(egg_white_seen=True, egg_selected=0, egg_battle=False,
                 ally_hp=0, enemy_hp=None)
    incidents = run_battle(runtime_dir, mem)
    assert len(incidents) == 1
    event = incidents[0]['battle_event']
    assert event['outcome'] == 'loss'
    assert event['outcome_source'] == 'ally_zero_enemy_unread'

    count = len(incidents)
    unresolved_ally = memory(egg_white_seen=True, egg_selected=0, egg_battle=False,
                             ally_hp=None, enemy_hp=40)
    assert len(run_battle(runtime_dir, unresolved_ally)) == count

    both_zero = memory(egg_white_seen=True, egg_selected=0, egg_battle=False,
                       ally_hp=0, enemy_hp=0)
    assert len(run_battle(runtime_dir, both_zero)) == count


def test_t10_egg_fingerprints_match_and_card_fingerprint_stays_legacy(runtime_dir):
    def build(metric, now):
        event = audit._battle_resource_event({
            'decision': 'battle_result', 'outcome': 'loss',
            'observed_metric': metric, 'ally': 'キッシュ',
            'chart_step': 'A:1:M1'})
        assert event is not None
        after = {'policy': {'chapter': CHAPTER, 'month': MONTH, 'tick': 1}}
        return audit._build_battle_resource_incident(
            runtime_dir, {}, after, event, now)

    first = build({'egg_uses': 2, 'ally_hp': 0, 'enemy_hp': 40}, 1000)
    second = build({'egg_uses': 1, 'ally_hp': 0, 'enemy_hp': 40}, 1100)
    card = build({'cards_selected': ['クースカン'], 'cards_used': [],
                  'ally_hp': 0, 'enemy_hp': 40}, 1200)
    assert first['fingerprint'] == second['fingerprint'] == egg_fingerprint()
    assert card['fingerprint'] == card_fingerprint()


class FakeGh:
    def __init__(self, issues=None, comments=None):
        self.issues = issues if issues is not None else []
        self.comments = comments if comments is not None else []
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        if args[:2] == ['auth', 'status']:
            return 0, '', ''
        if args[:2] == ['issue', 'list']:
            return 0, json.dumps(self.issues), ''
        if args[:2] == ['issue', 'view']:
            return 0, json.dumps({'comments': self.comments}), ''
        if args[:2] == ['issue', 'comment']:
            return 0, 'https://example.invalid/comment', ''
        if args[:2] == ['issue', 'create']:
            return 0, 'https://example.invalid/issue'
        return 1, '', 'unexpected'


def test_t11_human_comment_does_not_start_cooldown_but_bot_comment_does(runtime_dir):
    mem = memory(egg_white_seen=True, egg_selected=0, egg_battle=False)
    incidents = run_battle(runtime_dir, mem)
    record = incidents[0]
    issue = {'number': 7,
             'body': f"<!-- hanjuku-month-audit fingerprint={record['fingerprint']} -->"}
    now = time.time()

    human = [{'body': '人が確認しました',
              'createdAt': time.strftime('%Y-%m-%dT%H:%M:%SZ',
                                         time.gmtime(now - 60))}]
    result = audit.file_issue(runtime_dir, gh=FakeGh(issues=[issue],
                                                     comments=human),
                              scanner=lambda path: True, now=now)
    assert result['status'] == 'commented'

    bot = [{'body': '<!-- hanjuku-month-audit occurrence=older-episode -->',
            'createdAt': time.strftime('%Y-%m-%dT%H:%M:%SZ',
                                       time.gmtime(now - 60))}]
    result = audit.file_issue(runtime_dir, gh=FakeGh(issues=[issue],
                                                     comments=bot),
                              scanner=lambda path: True, now=now)
    assert result['status'] == 'cooldown'


def test_t12_tracker_failure_is_named_and_state_is_still_written(
        runtime_dir, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError('synthetic tracker failure')

    monkeypatch.setattr(audit, '_track_boss', boom)
    mem = memory(egg_white_seen=True, egg_selected=0, egg_battle=False)
    incidents = run_battle(runtime_dir, mem)
    assert [row['kind'] for row in incidents] == ['battle_loss_with_resources']
    saved = json.loads((runtime_dir / audit.AUDIT_FILE).read_text(encoding='utf-8'))
    assert saved['write_tick'] >= 1
    err = capsys.readouterr().err
    assert 'hanjuku-month-audit: tracker_failed _track_boss RuntimeError' in err


def test_t13_melee_only_summon_loss_is_not_an_egg_loss(runtime_dir):
    mem = memory(egg_white_seen=True, egg_selected=0, egg_battle=True,
                 egg_summon_seen=True, egg_melee_only=True)
    mem['egg_uses'] = {'キッシュ': 3}
    incidents = run_battle(runtime_dir, mem)
    assert incidents == []
