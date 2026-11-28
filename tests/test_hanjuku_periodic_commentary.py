"""Periodic Hanjuku commentary is corner-owned, grounded and non-repetitive."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from docich import hanjuku_narration, hanjuku_periodic as periodic
from docich import hanjuku_periodic_worker as worker
from docich.game_switch import atomic_write_json



LONG_A = ('第8章の14年2月、この15分はエピィ将軍がミュスカデに勝ちました。'
          'その一方でおかざき城を失い、攻めの勢いと守りの脆さが同時に見えた15分でした。'
          'それでも兵士を99人補充できたのは大きい。'
          '次はおかざき城の奪還を狙いますが、まずは守りを立て直したいところです。'
          '同志たちの粘りは見事で、流れはまだ十分に巻き返せます。'
          'この経験を次の戦いに活かせれば、きっと面白い展開になります。'
          'その判断の背景には、城を守りながら次の攻めを探る難しさがありました。'
          '将軍たちは限られた兵と札の中で、何を優先するかを毎回合わせています。'
          'だからこそ、小さな勝ちを次につなげる采配が問われるのでしょう。'
          'この15分の流れを、次の戦いの準備として受け止めたいと思います。')

LONG_B = ('第8章の14年2月、エピィ将軍がミュスカデに勝った一方で、おかざき城は失いました。'
          'それでも兵士を99人補充し、次はおかざき城の奪還を狙います。'
          '苦しい時間帯でも判断を止めなかった同志たちには、思わず拍手を送りたくなります。'
          '残るのは城を守り切ることで、攻めと守りのバランスを見直したいところです。'
          '城の出入りは激しく、目が離せない展開でしたが、最後まで前を向いていました。'
          'この15分の経験が次の戦いできっと生きてくると信じています。'
          'その判断の背景には、攻めと守りのどちらを優先するかの迷いがありました。'
          'それでも将軍たちは、限られた兵と札で次の一手を選び続けています。'
          'だからこそ、小さな前進を次につなげる采配が問われるのでしょう。'
          'この流れを、次の戦いの準備として一緒に見守りたいと思います。')

LONG_NO_PERSONA = LONG_A.replace('同志たち', '将軍たち')


IDENTITY = {'game': 'hanjuku-hero', 'runtime_id': 'g7-abc123',
            'generation': 7, 'lease_id': 'lease-periodic'}
RUN = {**IDENTITY, 'playing': True, 'phase': 'field', 'terminal_reason': None,
       'terminal_candidate': False}


def _runtime(tmp_path):
    runtime = tmp_path / 'runtimes' / IDENTITY['runtime_id']
    runtime.mkdir(parents=True)
    return runtime


def _write(path, value):
    atomic_write_json(path, value)


def _game(**extra):
    settings = {'enabled': True, 'interval_s': 900, 'retry_s': 300,
                'generation_timeout_s': 15, 'max_attempts': 2, **extra}
    return SimpleNamespace(raw={'hanjuku': {'periodic_commentary': settings}})


def _bot(identity=IDENTITY, **policy):
    return {'decision_trace': dict(identity),
            'policy': {'chapter': 8, 'month': '14-2', 'chart_edition': 'steady',
                       'gold': 732, 'captured': ['ながはま'], 'lost': ['おかざき'],
                       'stats': {'wins': 670, 'losses': 145},
                       'tally': {'battles_started': 825},
                       'battle': {'ally': 'トレビス', 'enemy': 'ブラッキー',
                                  'ally_hp': 66, 'enemy_hp': 55,
                                  'castle': 'いちじょうだに'},
                       'goals': [{'kind': 'retake', 'target': 'おかざき'}],
                       **policy}}


def _decision(path, *records):
    with path.open('w', encoding='utf-8') as stream:
        for record in records:
            stream.write(json.dumps({'event': 'decision', **record},
                                    ensure_ascii=False) + '\n')


@pytest.fixture
def live(tmp_path):
    runtime = _runtime(tmp_path)
    _write(runtime / 'hanjuku_run.json', RUN)
    _write(runtime / 'hanjuku_progress.json',
           {**IDENTITY, 'schema': 1, 'cleared': 7, 'ambiguous': False,
            'frame_sha256': 'a' * 64})
    _write(runtime / 'hanjuku_bot.json', _bot())
    now = time.time()
    _decision(runtime / 'hanjuku_decisions.jsonl',
              {'decision': 'battle_result', 'outcome': 'win', 'ally': 'エピィ',
               'enemy': 'ミュスカデ', 'ally_hp': 57, 'enemy_hp': 0,
               'at': now - 500, 'step': 1},
              {'decision': 'castle_lost_observed', 'castle': 'おかざき',
               'at': now - 300, 'step': 2},
              {'decision': 'soldier_refill_receipt', 'resulting_event': 'paid',
               'observed_metric': {'qty': 99, 'soldiers_after': 99},
               'at': now - 200, 'step': 3},
              {'decision': 'action_plan', 'at': now - 50})
    return SimpleNamespace(runtime=runtime, root=tmp_path, now=now, game=_game())


def _state(live):
    state = periodic._state(live.runtime, IDENTITY)
    state['run_started_at'] = live.now - 900
    return state


def test_config_is_opt_in_and_bounded():
    assert periodic.settings({})['enabled'] is False
    assert periodic.settings({'enabled': True})['interval_s'] == 900.0
    with pytest.raises(ValueError):
        periodic.settings({'enabled': 'yes'})
    with pytest.raises(ValueError):
        periodic.settings({'interval_s': 60})
    with pytest.raises(ValueError):
        periodic.settings({'max_attempts': True})
    assert periodic.settings({'agents': 'opencode-go:deepseek-v4.1-flash'})['agents'] == \
        'opencode-go:deepseek-v4.1-flash'
    with pytest.raises(ValueError):
        periodic.settings({'agents': 'model; rm -rf /'})
    assert periodic.settings({})['persona'] == periodic.DEFAULT_PERSONA
    with pytest.raises(ValueError):
        periodic.settings({'persona': 'bad\x00persona'})


def test_scene_generate_helper_honors_the_periodic_agent_override():
    helper = (ROOT / 'scripts/hanjuku_scene_generate.sh').read_text(encoding='utf-8')
    assert 'HANJUKU_PERIODIC_AGENTS' in helper
    assert 'scene_agents="$HANJUKU_PERIODIC_AGENTS"' in helper


def test_live_game_config_is_read_from_the_game_file(tmp_path):
    path = tmp_path / 'hanjuku-hero.toml'
    path.write_text('[hanjuku.periodic_commentary]\n'
                    'enabled = true\ninterval_s = 600\n', encoding='utf-8')
    game = SimpleNamespace(raw={'hanjuku': {'periodic_commentary': {'enabled': False}}})
    assert periodic.config(game)['enabled'] is False
    assert periodic.config(game, path) == {
        'enabled': True, 'interval_s': 600.0, 'retry_s': 300.0,
        'generation_timeout_s': 90.0, 'max_attempts': 2, 'agents': '',
        'persona': periodic.DEFAULT_PERSONA}


def test_current_falls_back_to_observed_country_counts_without_shop():
    policy = {'chapter': 8, 'month': '14-2',
              'country_soldier_count': {'count': 12, 'scope': [8, '14-2']},
              'country_general_count': {'count': 3, 'scope': [8, '14-2']}}
    current = periodic._current(policy)
    assert current['economy']['soldiers'] == 12
    assert current['economy']['generals'] == 3


def test_event_fact_reads_battle_hp_from_the_observed_metric():
    fact = periodic._event_fact({
        'decision': 'battle_result', 'outcome': 'win', 'ally': 'エピィ',
        'enemy': 'ミュスカデ',
        'observed_metric': {'ally_hp': 57, 'enemy_hp': 0}})
    assert fact == {'kind': 'battle_result', 'outcome': 'win', 'ally': 'エピィ',
                    'enemy': 'ミュスカデ', 'ally_hp': 57, 'enemy_hp': 0}
    assert periodic._event_fact({
        'decision': 'order_start', 'general': 'コリアンダー', 'target': 'ながはま',
        'roster_probe': True}) is None


def test_focus_fact_recognizes_strategic_decisions():
    card = periodic._focus_fact({
        'decision': 'battle_card', 'card': 'クースカン', 'enemy': 'ミュスカデ',
        'observed_metric': {'card_damage_gate': {
            'allowed': True, 'role': 'egg_drop_candidate_not_a_kill',
            'target_kind': 'general', 'lethal': False}}})
    assert card['category'] == 'card' and card['feature'] == '切り札の使用判断'
    assert card['allowed'] is True and card['role'] == 'egg_drop_candidate_not_a_kill'
    assert '撃破の実績ではない' in card['meaning']
    order = periodic._focus_fact({
        'decision': 'order_start', 'general': 'コリアンダー', 'source': 'あるまむーん',
        'target': 'ながはま', 'cards': ['フットバース']})
    assert order['category'] == 'sortie' and order['target'] == 'ながはま'
    assert periodic._focus_fact({
        'decision': 'order_start', 'general': 'コリアンダー', 'target': 'ながはま',
        'roster_probe': True}) is None
    defense = periodic._focus_fact({
        'decision': 'castle_lost_observed', 'castle': 'おかざき',
        'observed_metric': {'enemy': 'ブレッツェル', 'cause': 'defense_loss',
                            'garrison': ['ブラマンジェ']}})
    assert defense['category'] == 'defense' and defense['enemy'] == 'ブレッツェル'
    chart = periodic._focus_fact({
        'decision': 'chart_adjust_applied',
        'order_digest': [{'general': 'ポワソン', 'target': 'しまばら'}]})
    assert chart['category'] == 'chart' and chart['orders'] == [['ポワソン', 'しまばら']]
    economy = periodic._focus_fact({
        'decision': 'month_plan', 'month': '14-2', 'gold': 732,
        'plan': {'cards': [['クースカン', 2]], 'soldiers': 99}})
    assert economy['category'] == 'economy' and economy['gold'] == 732
    assert economy['cards'] == ['クースカン']


def test_focus_rotation_prefers_the_least_recent_category():
    candidates = [(1.0, {'category': 'defense', 'feature': '城の防衛'}),
                  (2.0, {'category': 'economy', 'feature': '兵士補充'}),
                  (3.0, {'category': 'card', 'feature': '切り札の使用判断'})]
    assert periodic._choose_focus(candidates, [])['category'] == 'card'
    assert periodic._choose_focus(candidates, ['card', 'economy'])['category'] == 'defense'
    assert periodic._choose_focus(candidates, ['card'])['category'] == 'economy'
    assert periodic._choose_focus([], ['card']) is None


def test_situation_is_whitelisted_and_counts_the_window(live):
    value = periodic.situation(live.runtime, RUN, _state(live), live.now)
    assert value['progress'] == {'cleared': 7, 'chapter': 8, 'year': 14,
                                 'month': 2, 'edition': 'steady'}
    assert value['window']['wins'] == 1 and value['window']['losses'] == 0
    assert value['window']['castle_losses'] == 1
    assert {event['kind'] for event in value['events']} == {
        'battle_result', 'castle_lost_observed', 'soldier_refill_receipt'}
    assert value['current']['economy']['gold'] == 732
    assert 'battle' not in value['current'] and 'order' not in value['current']
    assert 'totals' not in value['current']
    assert value['focus']['category'] in {'defense', 'economy'}
    text = json.dumps(value, ensure_ascii=False)
    for forbidden in ('decision_id', 'runtime_id', 'lease_id', 'reason',
                      'battle_card', 'action_plan', 'snapshot'):
        assert forbidden not in text


def test_prompt_contains_situation_and_avoid_list(live):
    value = periodic.situation(live.runtime, RUN, _state(live), live.now)
    prompt = periodic.build_prompt(value, ['前回の実況です。'], ['直前の候補です。'])
    assert '直近の実況' in prompt
    assert '前回の実況です。' in prompt and '直前の候補です。' in prompt
    assert '同じ書き出し' in prompt
    assert '3〜5文' in prompt and '320〜450' in prompt
    assert '課題' in prompt and 'ユーモア' in prompt
    assert '列挙' in prompt and 'focus' in prompt
    assert '焦点' in prompt
    assert '短い報告' in prompt and '乾いた' in prompt
    assert '評価したいのは' in prompt
    assert 'ソ連' in prompt and '同志' in prompt and 'focus.humor' in prompt
    assert '史実' in prompt and '暗い' in prompt and '正確' in prompt
    assert value['focus']['feature'] in prompt
    assert periodic.DEFAULT_PERSONA in prompt
    assert '"texts"' in prompt


def test_purchase_noun_does_not_require_a_completed_receipt(live):
    facts = _facts(live)
    assert periodic.valid_text('月次の購入と補充の判断でした。', facts) is True
    assert periodic.valid_text('クースカンを購入しました。', facts) is False


def test_style_rejects_report_formula():
    report = ('評価したいのは粘りです。課題は守備です。見どころは次です。'
              '楽しみにしています。')
    assert periodic.style_ok(report) is False
    hedging = ('切り分けられません。急いで結びつけない慎重さが必要です。'
               '宿題です。次の観測で答え合わせをしましょう。')
    assert periodic.style_ok(hedging) is False
    purple = ('長い戦いの中で現場は静かに区切りをつけ、深い余韻が穏やかに長く残った。'
              '帳面は落ち着き、土台と足元を整えて時を待つ。')
    assert periodic.style_ok(purple) is False
    assert periodic.style_ok('あ' * 81 + '。') is False
    assert periodic.style_ok(LONG_A) is True


def test_focus_prefers_a_confirmed_outcome_over_a_plan():
    found = [(3.0, {'category': 'card', 'not_result': True,
                    'outcome': {'kind': 'battle_result', 'outcome': 'win'}}),
             (2.0, {'category': 'battle', 'not_result': False,
                    'outcome': {'kind': 'battle_result', 'outcome': 'win'}})]
    assert periodic._choose_focus(found, [])['category'] == 'battle'


def test_unattributed_card_does_not_take_another_battles_result(tmp_path):
    runtime = tmp_path / 'runtimes' / IDENTITY['runtime_id']
    runtime.mkdir(parents=True)
    _decision(runtime / 'hanjuku_decisions.jsonl',
              {'decision': 'battle_card', 'card': 'イッテツーン',
               'enemy': 'プラリネ', 'at': 100, 'step': 1},
              {'decision': 'battle_result', 'outcome': 'win', 'ally': 'グリッシーニ',
               'enemy': 'プラリネ', 'at': 200, 'step': 2})
    found = periodic._focus_candidates(runtime, 0, 300)
    card = next(fact for _at, fact in found if fact['category'] == 'card')
    assert 'outcome' not in card


def test_focus_candidates_attach_a_later_battle_outcome(tmp_path):
    runtime = tmp_path / 'runtimes' / IDENTITY['runtime_id']
    runtime.mkdir(parents=True)
    _decision(runtime / 'hanjuku_decisions.jsonl',
              {'decision': 'order_start', 'general': 'コリアンダー',
               'source': 'あるまむーん', 'target': 'ながはま',
               'cards': ['フットバース'], 'at': 100, 'step': 1},
              {'decision': 'battle_result', 'outcome': 'win', 'ally': 'コリアンダー',
               'enemy': 'ヘーゼル', 'at': 200, 'step': 2})
    found = periodic._focus_candidates(runtime, 0, 300)
    order = next(fact for _at, fact in found if fact['category'] == 'sortie')
    assert order['outcome']['kind'] == 'battle_result'
    assert order['outcome']['outcome'] == 'win'
    assert order['not_result'] is False


def test_narrative_rejects_a_flat_fact_list():
    fact_list = ('エピィ将軍がミュスカデに勝ちました。おかざき城を失いました。'
                 '兵士を99人補充しました。次はおかざき城の奪還を狙います。')
    assert periodic.narrative_ok(fact_list) is False
    assert periodic.narrative_ok(LONG_A) is True
    assert periodic.narrative_ok(LONG_B) is True


def test_history_allows_framed_facts_but_rejects_game_attribution_and_mockery():
    assert periodic.history_ok('史実では、ソ連の大粛清で多くの人が犠牲になった。') is True
    assert periodic.history_ok('史実では、ソ連で飢餓が起きた。') is True
    assert periodic.history_ok('1942年の命令227号は退却を禁じた。') is True
    assert periodic.history_ok('将軍が粛清されました。') is False
    assert periodic.history_ok('史実ではソ連の大粛清を笑え。') is False
    assert periodic.history_ok('ソ連の大粛清はなかったという主張は誤りだ。') is False


def test_persona_requires_one_to_four_soviet_riffs():
    assert periodic.persona_ok('今回の焦点は同志の出撃判断です。') is True
    assert periodic.persona_ok('今回の焦点は出撃判断です。') is False
    assert periodic.persona_ok('同志、人民、赤旗、五カ年計画、政治局、集団農場、ペレストロイカ。') is False
    assert periodic.persona_ok('同志、人民、五カ年計画、赤旗、冬将軍。') is False


def test_focus_clarity_rejects_buried_focus_and_too_many_names():
    facts = [{'kind': 'battle_result', 'ally': 'マスカット', 'enemy': 'ヘーゼル'},
             {'kind': 'current_battle', 'ally': 'マスカット', 'enemy': 'ヘーゼル'},
             {'kind': 'focus', 'category': 'battle', 'ally': 'マスカット',
              'enemy': 'ヘーゼル'},
             {'kind': 'current_order', 'general': 'ゼウス', 'target': 'ひめじ'},
             {'kind': 'order', 'general': 'キッシュ', 'target': 'おだわら'},
             {'kind': 'order', 'general': 'ショコラ', 'target': 'しまばら'},
             {'kind': 'order', 'general': 'コンポート', 'target': 'えど'},
             {'kind': 'order', 'general': 'ヴィーナス', 'target': 'いちじょうだに'},
             {'kind': 'order', 'general': 'ポワソン', 'target': 'まつやま'}]
    focus = {'category': 'battle', 'ally': 'マスカット', 'enemy': 'ヘーゼル'}
    good = '今回の焦点はマスカットとヘーゼルの戦いです。開戦時体力は27対33でした。'
    assert periodic.focus_clear(good, focus, facts) is True
    buried = 'まず遠征の話です。札の話も少し。今回の焦点はマスカットとヘーゼルの戦いです。'
    assert periodic.focus_clear(buried, focus, facts) is False
    many = ('今回の焦点はマスカットとヘーゼルの戦いです。'
            'ゼウス、キッシュ、ショコラ、コンポート、ヴィーナス、ポワソンも動きました。')
    assert periodic.focus_clear(many, focus, facts) is False


def test_parse_candidates_keeps_valid_long_lines_and_skips_short_ones():
    output = json.dumps({'texts': [LONG_A, LONG_A]}, ensure_ascii=False)
    assert periodic.parse_candidates(output) == [LONG_A]
    with pytest.raises(periodic.PeriodicError):
        periodic.parse_candidates(json.dumps({'texts': ['短い。']}, ensure_ascii=False))


def _facts(live):
    return periodic.flatten(
        periodic.situation(live.runtime, RUN, _state(live), live.now))


@pytest.mark.parametrize('text,expected', [
    ('第8章の14年2月、おかざき城の奪還を狙います。', True),
    ('エピィ将軍がミュスカデに勝ちました。', True),
    ('この15分で1勝0敗、兵士を99人補充しました。', True),
    ('7章をクリアしました。', True),
    ('8章をクリアしました。', False),
    ('トレビス将軍のHPは66、ブラッキー将軍のHPは55です。', True),
    ('トレビス将軍とブラッキー将軍、体力は66対55です。', True),
    ('第9章に進みました。', False),
    ('兵士を500人補充しました。', False),
    ('クーモンを倒しました。', False),
    ('おかざき城を占領しました。', False),
    ('ゲームオーバーになりました。', False),
    ('コリアンダー将軍が出撃しました。', False),
    ('次はえど城へ向かいます。', False),
])
def test_valid_text_grounds_names_numbers_and_claims(live, text, expected):
    facts = _facts(live)
    if 'HP' in text or '体力' in text:
        facts.append({'kind': 'current_battle', 'ally': 'トレビス', 'enemy': 'ブラッキー',
                      'ally_hp': 66, 'enemy_hp': 55, 'castle': 'いちじょうだに'})
    assert periodic.valid_text(text, facts) is expected


def test_novelty_rejects_same_opening_and_template_lines():
    recent = ['第8章の14年2月、おかざき城の奪還を狙います。']
    assert periodic.similarity(recent[0], recent[0]) >= 1.0
    assert periodic.similarity('第8章の14年2月、所持金は732ゴールドです。',
                               '第8章の14年2月、兵士を99人補充しました。') >= 1.0
    assert periodic.similarity('この15分、エピィ将軍がミュスカデに勝ちました。',
                               'おかざき城を失いましたが、兵士を99人補充しました。') < 1.0
    assert periodic.choose_novel(
        ['第8章の14年2月、おかざき城の奪還を狙います。',
         'この15分、エピィ将軍がミュスカデに勝ちました。'], recent) == \
        'この15分、エピィ将軍がミュスカデに勝ちました。'
    assert periodic.choose_novel(
        ['第8章の14年2月、おかざき城の奪還を狙います。'], recent) is None


def test_scheduler_waits_interval_then_starts_one_worker(live, monkeypatch):
    spawned = []
    monkeypatch.setattr(periodic, '_spawn', lambda repo, runtime, fd, config: spawned.append(runtime))
    g = SimpleNamespace(repo_root=live.root, config_path=live.root / 'config.toml')
    assert periodic.tick(g, live.game, live.runtime, RUN, IDENTITY, now=live.now) is None
    assert spawned == []
    request = periodic.tick(g, live.game, live.runtime, RUN, IDENTITY,
                            now=live.now + 900)
    assert request is not None and spawned == [live.runtime]
    assert json.loads((live.runtime / periodic.REQUEST).read_text())['seq'] == 1
    state = json.loads((live.runtime / periodic.STATE).read_text())
    assert state['pending_seq'] == 1
    assert periodic.tick(g, live.game, live.runtime, RUN, IDENTITY,
                         now=live.now + 930) is None
    assert len(spawned) == 1
    atomic_write_json(live.runtime / periodic.RESULT, {
        'schema': 1, **IDENTITY, 'seq': 1, 'at': live.now + 920,
        'status': 'enqueued', 'text': LONG_A,
        'attempts': 1})
    periodic.tick(g, live.game, live.runtime, RUN, IDENTITY, now=live.now + 940)
    state = json.loads((live.runtime / periodic.STATE).read_text())
    assert state['pending_seq'] is None
    assert state['last_result_seq'] == 1
    assert state['recent_texts'] == [LONG_A]
    assert state['next_at'] >= live.now + 900


def test_scheduler_honors_a_live_enable_without_in_memory_config(live, monkeypatch):
    config_dir = live.root / 'config' / 'games'
    config_dir.mkdir(parents=True)
    (config_dir / 'hanjuku-hero.toml').write_text(
        '[hanjuku.periodic_commentary]\nenabled = true\ninterval_s = 600\n',
        encoding='utf-8')
    spawned = []
    monkeypatch.setattr(periodic, '_spawn', lambda repo, runtime, fd, config: spawned.append(runtime))
    g = SimpleNamespace(repo_root=live.root, config_path=live.root / 'config.toml',
                        games_dir=config_dir)
    stale_game = _game(enabled=False)
    assert periodic.tick(g, stale_game, live.runtime, RUN, IDENTITY, now=live.now) is None
    state = json.loads((live.runtime / periodic.STATE).read_text())
    assert state['next_at'] == live.now + 600
    assert periodic.tick(g, stale_game, live.runtime, RUN, IDENTITY,
                         now=live.now + 600) is not None
    assert spawned == [live.runtime]


def test_scheduler_defers_during_battle_but_not_forever(live, monkeypatch):
    spawned = []
    monkeypatch.setattr(periodic, '_spawn', lambda repo, runtime, fd, config: spawned.append(runtime))
    g = SimpleNamespace(repo_root=live.root, config_path=live.root / 'config.toml')
    battle = {**RUN, 'phase': 'battle'}
    assert periodic.tick(g, live.game, live.runtime, battle, IDENTITY,
                         now=live.now) is None
    assert periodic.tick(g, live.game, live.runtime, battle, IDENTITY,
                         now=live.now + 900) is None
    state = json.loads((live.runtime / periodic.STATE).read_text())
    assert state['defer_since'] == live.now + 900
    assert spawned == []
    request = periodic.tick(g, live.game, live.runtime, battle, IDENTITY,
                            now=live.now + 900 + periodic.DEFER_MAX_S + 1)
    assert request is not None and spawned == [live.runtime]


def test_scheduler_waits_for_a_minimum_window_after_a_recent_delivery(live, monkeypatch):
    spawned = []
    monkeypatch.setattr(periodic, '_spawn', lambda repo, runtime, fd, config: spawned.append(runtime))
    g = SimpleNamespace(repo_root=live.root, config_path=live.root / 'config.toml')
    state = _state(live)
    state['last_delivered_at'] = live.now - 300
    state['next_at'] = live.now - 1
    periodic._save_state(live.runtime, state)
    assert periodic.tick(g, live.game, live.runtime, RUN, IDENTITY, now=live.now) is None
    assert spawned == []
    state = json.loads((live.runtime / periodic.STATE).read_text())
    assert state['next_at'] == live.now + 300


def test_scheduler_does_nothing_when_disabled_or_terminal(live, monkeypatch):
    monkeypatch.setattr(periodic, '_spawn', lambda *args: pytest.fail('must not spawn'))
    g = SimpleNamespace(repo_root=live.root, config_path=live.root / 'config.toml')
    disabled = _game(enabled=False)
    assert periodic.tick(g, disabled, live.runtime, RUN, IDENTITY, now=live.now + 900) is None
    terminal = {**RUN, 'terminal_reason': 'game_over'}
    assert periodic.tick(g, live.game, live.runtime, terminal, IDENTITY,
                         now=live.now + 900) is None
    assert not (live.runtime / periodic.REQUEST).exists()


def _request(live, recent=()):
    value = periodic.situation(live.runtime, RUN, _state(live), live.now)
    return {'schema': 1, 'version': periodic.VERSION, **IDENTITY, 'seq': 1,
            'at': live.now, 'expires_at': live.now + periodic.REQUEST_TTL_S,
            'recent_texts': list(recent), 'situation': value}


def _worker(live, monkeypatch, output, delivered, recent=()):
    _write(live.runtime / periodic.REQUEST, _request(live, recent))
    monkeypatch.setattr(worker, 'load_game', lambda *args: live.game)
    monkeypatch.setattr(worker, 'current', lambda *args, **kwargs: RUN)
    return worker.consume(
        SimpleNamespace(repo_root=live.root, state_dir=live.root), live.runtime, now=live.now,
        generate_fn=lambda *args: (output, 'RADIO_AGENTS'),
        deliver_fn=lambda *args, **kwargs: delivered.append((args, kwargs)) or 'enqueued')


def test_worker_sets_the_periodic_agent_override(live, monkeypatch):
    seen = []
    live.game.raw['hanjuku']['periodic_commentary']['agents'] = (
        'opencode-go:deepseek-v4.1-flash,opencode-go:deepseek-v4-flash')
    _write(live.runtime / periodic.REQUEST, _request(live))
    monkeypatch.setattr(worker, 'load_game', lambda *args: live.game)
    monkeypatch.setattr(worker, 'current', lambda *args, **kwargs: RUN)
    worker.consume(
        SimpleNamespace(repo_root=live.root, state_dir=live.root), live.runtime,
        now=live.now,
        generate_fn=lambda *args: seen.append(os.environ.get('HANJUKU_PERIODIC_AGENTS'))
        or (json.dumps({'texts': [LONG_A]},
                       ensure_ascii=False), 'RADIO_AGENTS'),
        deliver_fn=lambda *args, **kwargs: 'enqueued')
    assert seen == ['opencode-go:deepseek-v4.1-flash,opencode-go:deepseek-v4-flash']


def test_worker_falls_back_to_a_grounded_line_without_persona(live, monkeypatch):
    delivered = []
    _write(live.runtime / periodic.REQUEST, _request(live))
    monkeypatch.setattr(worker, 'load_game', lambda *args: live.game)
    monkeypatch.setattr(worker, 'current', lambda *args, **kwargs: RUN)
    status, text, attempts, role = worker.consume(
        SimpleNamespace(repo_root=live.root, state_dir=live.root), live.runtime,
        now=live.now,
        generate_fn=lambda *args: (json.dumps({'texts': [LONG_NO_PERSONA]},
                                              ensure_ascii=False), 'RADIO_AGENTS'),
        deliver_fn=lambda *args, **kwargs: delivered.append(args) or 'enqueued')
    assert status == 'enqueued' and text == LONG_NO_PERSONA
    log = (live.runtime / (periodic.LOG + '.jsonl')).read_text(encoding='utf-8')
    assert 'validation_fallback' in log


def test_worker_delivers_the_least_repetitive_grounded_candidate(live, monkeypatch):
    delivered = []
    output = json.dumps({'texts': [LONG_A, LONG_B]}, ensure_ascii=False)
    status, text, attempts, role = _worker(
        live, monkeypatch, output, delivered, recent=[LONG_A])
    assert (status, text, attempts, role) == ('enqueued', LONG_B, 1, 'RADIO_AGENTS')
    assert len(delivered) == 1
    assert delivered[0][0][4] == text
    result = json.loads((live.runtime / periodic.RESULT).read_text())
    assert result['status'] == 'enqueued' and result['text'] == text


def test_worker_result_records_the_focus_and_state_rotates_it(live, monkeypatch):
    delivered = []
    request = _request(live)
    focus = request['situation']['focus']['category']
    _write(live.runtime / periodic.REQUEST, request)
    monkeypatch.setattr(worker, 'load_game', lambda *args: live.game)
    monkeypatch.setattr(worker, 'current', lambda *args, **kwargs: RUN)
    status, text, attempts, role = worker.consume(
        SimpleNamespace(repo_root=live.root, state_dir=live.root), live.runtime, now=live.now,
        generate_fn=lambda *args: (json.dumps({'texts': [LONG_A]}, ensure_ascii=False),
                                   'RADIO_AGENTS'),
        deliver_fn=lambda *args, **kwargs: delivered.append(args) or 'enqueued')
    assert status == 'enqueued'
    result = json.loads((live.runtime / periodic.RESULT).read_text())
    assert result['focus'] == focus
    state = periodic._state(live.runtime, IDENTITY)
    periodic._apply(live.runtime, state, result, periodic.config(live.game), live.now)
    assert state['focus_history'] == [focus]


def test_worker_is_silent_when_every_candidate_repeats(live, monkeypatch):
    delivered = []
    repeated = LONG_A
    _write(live.runtime / periodic.REQUEST, _request(live, [repeated]))
    monkeypatch.setattr(worker, 'load_game', lambda *args: live.game)
    monkeypatch.setattr(worker, 'current', lambda *args, **kwargs: RUN)
    status, text, attempts, role = worker.consume(
        SimpleNamespace(repo_root=live.root, state_dir=live.root), live.runtime, now=live.now,
        generate_fn=lambda *args: (json.dumps({'texts': [repeated]}, ensure_ascii=False),
                                   'RADIO_AGENTS'),
        deliver_fn=lambda *args, **kwargs: delivered.append(args) or 'enqueued')
    assert status == 'duplicate' and text is None and delivered == []
    result = json.loads((live.runtime / periodic.RESULT).read_text())
    assert result['status'] == 'duplicate'


def test_worker_retries_an_invalid_answer_then_delivers(live, monkeypatch):
    delivered = []
    _write(live.runtime / periodic.REQUEST, _request(live))
    monkeypatch.setattr(worker, 'load_game', lambda *args: live.game)
    monkeypatch.setattr(worker, 'current', lambda *args, **kwargs: RUN)
    answers = iter([
        json.dumps({'texts': ['第9章に進みました。']}, ensure_ascii=False),
        json.dumps({'texts': [LONG_A]}, ensure_ascii=False)])
    status, text, attempts, role = worker.consume(
        SimpleNamespace(repo_root=live.root, state_dir=live.root), live.runtime, now=live.now,
        generate_fn=lambda *args: (next(answers), 'RADIO_AGENTS'),
        deliver_fn=lambda *args, **kwargs: delivered.append(args) or 'enqueued')
    assert status == 'enqueued' and attempts == 2
    assert text == LONG_A


def test_periodic_delivery_keeps_the_soren_fence_within_120_seconds(live, monkeypatch):
    queued = []
    monkeypatch.setattr('docich.agent.fence.read_canonical',
                        lambda _: {'phase': 'ready', 'active': IDENTITY})
    monkeypatch.setattr('docich.agent.fence.shared_section', lambda _path, fn, **kw: fn())
    monkeypatch.setattr('docich.trading.soren_output.enqueue_audio_text',
                        lambda *args, **kwargs: queued.append((args, kwargs)))
    _write(live.runtime / 'hanjuku_bot.json', {'decision_trace': dict(IDENTITY)})
    status = hanjuku_narration.deliver_periodic(
        SimpleNamespace(state_dir=live.root, repo_root=live.root), live.game,
        live.runtime, IDENTITY, '第8章の14年2月、所持金は732ゴールドです。',
        at=live.now - 200, max_age=240, seq=4)
    assert status == 'enqueued' and len(queued) == 1
    fence = queued[0][1]['runtime_fence']
    now = time.time()
    assert now <= fence['expires_at'] <= now + 120.0


def test_delivery_uses_the_fenced_audio_queue_and_posts_no_chat(live, monkeypatch):
    queued = []
    monkeypatch.setattr('docich.agent.fence.read_canonical',
                        lambda _: {'phase': 'ready', 'active': IDENTITY})
    monkeypatch.setattr('docich.agent.fence.shared_section', lambda _path, fn, **kw: fn())
    monkeypatch.setattr('docich.trading.soren_output.enqueue_audio_text',
                        lambda *args, **kwargs: queued.append((args, kwargs)))
    _write(live.runtime / 'hanjuku_bot.json', {'decision_trace': dict(IDENTITY)})
    status = hanjuku_narration.deliver_periodic(
        SimpleNamespace(state_dir=live.root, repo_root=live.root), live.game,
        live.runtime, IDENTITY, '第8章の14年2月、所持金は732ゴールドです。',
        at=live.now, max_age=60, seq=3)
    assert status == 'enqueued' and len(queued) == 1
    args, kwargs = queued[0]
    assert args[1] == '第8章の14年2月、所持金は732ゴールドです。'
    assert kwargs['context'] == 'hanjuku_commentary'
    assert kwargs['runtime_fence'] == {**IDENTITY, 'expires_at': live.now + 60}
    assert json.loads((live.runtime / 'hanjuku_narration.jsonl').read_text())['status'] == 'enqueued'

def test_prompt_requires_desumasu_tone(live):
    # owner 2026-11-27: 15分実況は「です・ます」調にする（だ・である調は不可）。
    value = periodic.situation(live.runtime, RUN, _state(live), live.now)
    prompt = periodic.build_prompt(value)
    assert 'です・ます' in prompt
    assert '常体は使わない' in prompt
    # 以前の「〜でしょう」「〜ですね」禁止は、丁寧な語尾として許可に変わった。
    assert '「〜でしょう」「〜ですね」「〜てやりましょう」' not in prompt


def test_prompt_bans_detail_numbers_and_excuses_and_asks_for_strategy(live):
    # owner 2026-11-28: HP等の細かい数値と「未確認です」系の留保を入れず、
    # bot の戦略アルゴリズムと絡めた行動説明を入れる。
    value = periodic.situation(live.runtime, RUN, _state(live), live.now)
    prompt = periodic.build_prompt(value)
    assert 'HP・兵数・ダメージ・残金などの細かい数値は書かない' in prompt
    assert 'エクスキューズや留保は書かない' in prompt
    assert '戦略の言葉で添えてください' in prompt

