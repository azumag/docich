"""Chart bot contracts on synthetic frames (no ROM images or game data).

Frames are drawn from the measured glyph table, so the tests exercise the
same exact-tile reader the live bot uses without storing screenshots.
"""
from pathlib import Path
import json
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich import hanjuku_chart as chart
from docich import hanjuku_commentary, hanjuku_narration, hanjuku_policy as policy
from docich import pulse_volume
from docich.hanjuku_bot import decide
from docich.hanjuku_font import UNKNOWN, read_lines
from docich.hanjuku_glyphs import GLYPHS, MARKS
from docich.hanjuku_pixels import Frame
from docich.hanjuku_screen import parse

CODE = {ch: code for code, ch in GLYPHS.items()}
MARK = {v: k for k, v in MARKS.items()}
DAKU = dict(zip('がぎぐげござじずぜぞだぢづでどばびぶべぼガギグゲゴザジズゼゾダヂヅデドバビブベボ',
                'かきくけこさしすせそたちつてとはひふへほカキクケコサシスセソタチツテトハヒフヘホ'))
HANDAKU = dict(zip('ぱぴぷぺぽパピプペポ', 'はひふへほハヒフヘホ'))
GREEN = (16, 72, 57)
HAND = ((255, 174, 82), (205, 105, 24), (205, 149, 32))


class Canvas:
    def __init__(self, color=GREEN):
        self.rgb = bytearray(bytes(color) * (256 * 224))

    def put(self, x, y, color):
        i = (y * 256 + x) * 3
        self.rgb[i:i + 3] = bytes(color)

    def tile(self, x, y, code, color=(255, 255, 255)):
        for row in range(8):
            for col in range(8):
                if code >> (63 - row * 8 - col) & 1:
                    self.put(x + col, y + row, color)

    def text(self, x, y, text, color=(255, 255, 255)):
        for i, ch in enumerate(text):
            if ch == ' ':
                continue
            base, mark = ch, None
            if ch in DAKU:
                base, mark = DAKU[ch], '゛'
            elif ch in HANDAKU:
                base, mark = HANDAKU[ch], '゜'
            self.tile(x + 8 * i, y, CODE[base], color)
            if mark:
                self.tile(x + 8 * i, y - 8, MARK[mark], color)

    def hand(self, x0, y0):
        for y in range(y0, y0 + 13):
            for x in range(x0, x0 + 18):
                self.put(x, y, HAND[(x + y) % 3])

    def frame(self):
        return Frame(256, 224, bytes(self.rgb))


def test_tile_reader_round_trips_kana_marks_and_digits_and_never_guesses():
    c = Canvas()
    c.text(16, 39, 'どうし パピプ 104G')
    c.tile(160, 55, 0x0123456789ABCDEF)
    lines = {line.y: line for line in read_lines(c.frame())}
    assert lines[39].known == 'どうし パピプ 104G'
    assert UNKNOWN in lines[55].text and lines[55].known == ''


def test_header_menu_hand_and_battle_panel_are_structured():
    c = Canvas()
    c.text(48, 15, '1ねん 5のつき 104G')
    c.text(48, 47, 'しょうにん')
    c.text(48, 63, 'へいしほじゅう')
    c.text(128, 95, 'も〜おしまい!')
    c.hand(26, 41)
    s = parse(c.frame())
    assert s.header == {'chapter': None, 'year': 1, 'month': 5, 'gold': 104}
    assert s.kind == 'month_menu' and s.selected == 'しょうにん'
    assert policy.menu_to(s, 'へいしほじゅう')['buttons'] == ['down']
    b = Canvas((238, 238, 238))
    b.text(16, 176, 'ミント', (32, 32, 32))
    b.text(96, 176, '32', (32, 32, 32))
    b.text(144, 176, 'どうし', (32, 32, 32))
    b.text(224, 176, '90', (32, 32, 32))
    battle = parse(b.frame()).battle
    assert (battle.enemy, battle.enemy_hp, battle.ally, battle.ally_hp) == ('ミント', 32, 'どうし', 90)


def name_screen(typed='', cell=None, menu=False):
    c = Canvas((0, 0, 0))
    c.text(24, 23, 'ごじぶんの なまえの かきとりですぞ!')
    c.text(64, 55, typed)
    c.text(32, 87, 'ひらがな')
    for r, row in enumerate(policy.KANA_GRID):
        for col, ch in enumerate(row):
            if ch != ' ':
                x, y = policy.kana_cell(ch)
                c.text(x, y, ch)
    if menu:
        c.hand(17, 81)
    else:
        x, y = policy.kana_cell(cell)
        c.hand(x - 22, y - 6)
    return c.frame()


def test_name_entry_types_どうし_through_the_grid_and_confirms_only_on_screen_text():
    mem = {}
    s = parse(name_screen(menu=True))
    assert s.kind == 'name_entry'
    assert policy.name_step(s, mem)[0]['buttons'] == ['a']        # enter the grid
    # From あ the cursor walks to ど (row 5, col 9): down first, then right.
    assert policy.name_step(parse(name_screen(cell='あ')), mem)[0]['buttons'] == ['down']
    assert policy.name_step(parse(name_screen(cell='だ')), mem)[0]['buttons'] == ['right']
    assert policy.name_step(parse(name_screen(cell='ど')), mem)[0]['buttons'] == ['a']
    assert policy.name_step(parse(name_screen('ど', cell='え')), mem)[0]['buttons'] == ['left']
    assert policy.name_step(parse(name_screen('どう', cell='し')), mem)[0]['buttons'] == ['a']
    assert policy.name_step(parse(name_screen('どうし', cell='し')), mem)[0]['buttons'] == ['start']
    assert mem['name']['done'] is True
    records = mem.pop('_records')
    assert [r['decision'] for r in records] == ['name_type', 'name_type', 'name_confirm']
    assert records[-1]['typed'] == 'どうし'
    # A wrong character is deleted instead of confirmed.
    assert policy.name_step(parse(name_screen('どあ', cell='あ')), {})[0]['buttons'] == ['b']


def test_hand_hiding_the_target_glyph_still_navigates_by_measured_layout():
    c = Canvas((0, 0, 0))
    c.text(24, 23, 'ごじぶんの なまえの かきとりですぞ!')
    c.text(64, 55, 'ど')
    c.text(32, 87, 'ひらがな')
    c.text(128, 87, 'え')           # う is hidden under the hand
    c.hand(106, 81)
    assert policy.name_step(parse(c.frame()), {})[0]['buttons'] == ['left']


def test_budget_plan_prioritises_boss_kit_and_records_deviation():
    mem = {'chapter': 1}
    shop = policy._plan(mem, {'year': 1, 'month': 5, 'gold': 137})
    assert shop['items'][:3] == [['クースカン', 1], ['ノリウツール', 1], ['イッテツーン', 6]]
    rec = mem['_records'][-1]
    assert rec['strategy_variant'] == 'budget_boss_kit_first'
    assert '214' in rec['deviation_reason'] and rec['expected_metric']['chart_gold'] == 214
    full = policy._plan({'chapter': 1}, {'year': 1, 'month': 5, 'gold': 214})
    assert full['items'] == [list(i) for i in chart.CHAPTER_1_PURCHASES['cards']]
    assert full['soldiers'] == 41
    assert policy._plan({'chapter': 1}, {'year': 1, 'month': 6, 'gold': 999}) is None


def test_battle_result_uses_only_visible_hp_and_captures_on_attack_win():
    mem = {'chapter': 1, 'battle': {'enemy': 'ミント', 'ally': 'どうし', 'enemy_hp': 0, 'ally_hp': 90,
                                    'castle': 'キカンドン', 'side': 'attack', 'step': '1-A1',
                                    'cards_used': []}}
    policy.battle_end(mem, 'map')
    assert 'battle' in mem                      # one missing panel may be a blink
    policy.battle_end(mem, 'map')
    assert mem['captured'] == ['キカンドン']
    rec = mem['_records'][-1]
    assert rec['outcome'] == 'win' and rec['resulting_event'] == 'captured:キカンドン'
    mem = {'chapter': 1, 'battle': {'enemy': 'X', 'ally': 'Y', 'enemy_hp': 5, 'ally_hp': 9,
                                    'castle': 'ゴーメン', 'side': 'attack', 'cards_used': []}}
    policy.battle_end(mem, 'map'); policy.battle_end(mem, 'map')
    assert mem['_records'][-1]['outcome'] == 'unclassified'
    assert mem.get('captured', []) == []


def test_attack_loss_requeues_the_chart_order_with_bounded_retries():
    for attempt in (1, 2, 3):
        mem = {'chapter': 1, 'retries': {'1-C1': attempt - 1}, 'orders': {'1-C1': 'launched'},
               'battle': {'enemy': 'ラズベリー', 'ally': 'ココット', 'enemy_hp': 45, 'ally_hp': 0,
                          'castle': 'ジョンリギ', 'side': 'attack', 'step': '1-C1', 'cards_used': []}}
        policy.battle_end(mem, 'map'); policy.battle_end(mem, 'map')
        assert (mem['orders']['1-C1'] == 'pending') is (attempt <= 2)


def test_chart_orders_follow_the_chart_and_unlock_on_captures():
    mem = {'chapter': 1, 'orders': {}}
    assert [policy.next_order(mem)['step']] == ['1-A1']
    mem['orders'] = {'1-A1': 'launched', '1-V1': 'launched', '1-C1': 'launched'}
    assert policy.next_order(mem) is None
    mem['captured'] = ['キカンドン']
    order = policy.next_order(mem)
    assert order['step'] == '1-A2' and order['cards'] == ('フットバース',) and order['target'] == 'ゴーメン'
    mem['captured'] = ['キカンドン', 'ナキューメラ', 'ジョンリギ', 'ゴーメン', 'スペンソニア', 'カストーラ']
    mem['orders'].update({s: 'launched' for s in ('1-A2', '1-V2', '1-C2', '1-A3')})
    assert policy.next_order(mem)['target'] == 'けっかい'
    assert policy.next_order({'chapter': 2, 'orders': {}}) is None


def test_boss_tactic_waits_for_the_first_clash_then_chains_cards():
    mem = {'chapter': 1, 'attack': {'general': 'どうし', 'castle': 'けっかい', 'side': 'attack', 'step': '1-B1'}}
    from docich.hanjuku_screen import Battle, Screen
    screen = lambda hp: Screen(lines=[], hand=None, text='', battle=Battle('クイーン', hp, 'どうし', 90), kind='battle')
    assert policy.battle_step(screen(70), mem) == []          # first reading: wait for a stable one
    assert policy.battle_step(screen(70), mem) == []          # stable, no clash yet: melee
    assert policy.battle_step(screen(60), mem)[0]['buttons'] == ['b']
    assert mem['battle']['card_flow']['card'] == 'クースカン'


def test_quantity_editor_uses_the_digit_cursor_and_the_price_message():
    mem = {'shop': {'want': ['イッテツーン', 9, 1]}}
    from docich.hanjuku_screen import Screen
    s = Screen(lines=[], hand=(202, 197, 222, 210), text='1こで1Gになりまんな!よろしいでっか?', kind='shop_quantity')
    assert policy.quantity_step(s, mem)[0]['buttons'] == ['down']      # 1 -> 9 wraps downwards
    s = Screen(lines=[], hand=(202, 197, 222, 210), text='9こで9Gになりまんな!', kind='shop_quantity')
    assert policy.quantity_step(s, mem)[0]['buttons'] == ['a']
    mem = {'shop': {'want': ['イッテツーン', 12, 1]}}
    s = Screen(lines=[], hand=(202, 197, 222, 210), text='2こで2Gになりまんな!', kind='shop_quantity')
    assert policy.quantity_step(s, mem)[0]['buttons'] == ['left']


def test_decline_duels_and_do_not_confirm_unrequested_month_exit():
    c = Canvas()
    c.text(24, 183, 'いって ごあいて ねがえぬか?')
    c.text(184, 183, 'うむッ!')
    c.text(184, 199, 'いかんッ!')
    c.hand(162, 177)
    mem = {}
    assert policy.yes_no_step(parse(c.frame()), mem)[0]['buttons'] == ['down']
    c2 = Canvas()
    c2.text(48, 15, '1ねん 5のつき 20G')
    c2.text(48, 47, 'しょうにん')
    c2.text(24, 183, 'よろしいですかな?')
    c2.text(184, 183, 'うむッ!')
    c2.text(184, 199, 'いかんッ!')
    c2.hand(162, 177)
    assert policy.month_step(parse(c2.frame()), {'chapter': 2})[0]['buttons'] == ['down']
    assert policy.month_step(parse(c2.frame()), {'chapter': 2, 'month_exit': True})[0]['buttons'] == ['a']


def test_commentary_is_grounded_and_holds_when_unknown():
    key, text = hanjuku_commentary.compose({'decision': 'battle_card', 'card': 'クースカン',
                                            'enemy': 'クイーン', 'enemy_hp': 60})
    assert 'クースカン' in text and '60' in text
    assert hanjuku_commentary.compose({'decision': 'battle_result', 'outcome': 'unclassified'})[1] is None
    assert hanjuku_commentary.compose({'decision': 'situation_held'})[1] is None


class Game:
    def __init__(self, **narration):
        self.raw = {'hanjuku': {'narration': {'enabled': True, 'cooldown_s': 25, **narration}}}


def write_candidates(runtime, items):
    with (runtime / 'hanjuku_commentary.jsonl').open('a') as stream:
        for item in items:
            stream.write(json.dumps(item, ensure_ascii=False) + '\n')


def test_narration_enqueues_one_line_off_thread_with_dedupe_and_cooldown(tmp_path):
    sent, release = [], threading.Event()

    def slow_enqueue(g, text, *, context, speaker):
        release.wait(5)
        sent.append((text, context))
    now = time.time()
    write_candidates(tmp_path, [{'seq': 1, 'at': now, 'key': 'a', 'text': '古い候補'},
                                {'seq': 2, 'at': now, 'key': 'b', 'text': '新しい候補'}])
    started = time.monotonic()
    chosen = hanjuku_narration.consider(None, Game(), tmp_path, now=now, enqueue=slow_enqueue)
    assert time.monotonic() - started < 1          # never waits for the audio queue
    assert chosen['seq'] == 2
    release.set()
    for _ in range(50):
        if sent:
            break
        time.sleep(.05)
    assert sent == [('新しい候補', 'hanjuku:commentary')]
    time.sleep(.1)
    log = [json.loads(l) for l in (tmp_path / 'hanjuku_narration.jsonl').read_text().splitlines()]
    assert {'seq': 1, 'status': 'skipped:superseded'}.items() <= log[0].items()
    assert log[-1]['status'] == 'enqueued'
    # Same key, same text or inside the cooldown: skipped, not queued.
    write_candidates(tmp_path, [{'seq': 3, 'at': now + 5, 'key': 'c', 'text': '別の候補'}])
    assert hanjuku_narration.consider(None, Game(), tmp_path, now=now + 5, enqueue=slow_enqueue) is None
    write_candidates(tmp_path, [{'seq': 4, 'at': now + 40, 'key': 'd', 'text': '新しい候補'}])
    assert hanjuku_narration.consider(None, Game(), tmp_path, now=now + 40, enqueue=slow_enqueue) is None
    write_candidates(tmp_path, [{'seq': 5, 'at': now + 80, 'key': 'e', 'text': None, 'status': 'held'}])
    assert hanjuku_narration.consider(None, Game(), tmp_path, now=now + 80, enqueue=slow_enqueue) is None
    statuses = [json.loads(l)['status'] for l in (tmp_path / 'hanjuku_narration.jsonl').read_text().splitlines()]
    assert statuses[-3:] == ['skipped:cooldown', 'skipped:repeat', 'skipped:held']


def test_narration_is_off_outside_play_and_rejects_invalid_settings(tmp_path):
    write_candidates(tmp_path, [{'seq': 1, 'at': time.time(), 'key': 'a', 'text': 'x'}])
    fail = lambda *a, **k: pytest.fail('must not enqueue')
    assert hanjuku_narration.consider(None, Game(), tmp_path, terminal=True, enqueue=fail) is None
    assert hanjuku_narration.consider(None, Game(enabled=False), tmp_path, enqueue=fail) is None
    with pytest.raises(ValueError):
        hanjuku_narration.settings(Game(cooldown_s=1))
    with pytest.raises(ValueError):
        hanjuku_narration.settings(Game(enabled='yes'))


PACTL = '''Sink Input #41
\tSink: 3
\tMute: no
\tVolume: front-left: 65536 / 100% / 0.00 dB,   front-right: 65536 / 100% / 0.00 dB
\tProperties:
\t\tapplication.process.id = "500"
Sink Input #42
\tSink: 3
\tMute: no
\tVolume: front-left: 65536 / 100% / 0.00 dB,   front-right: 65536 / 100% / 0.00 dB
\tProperties:
\t\tapplication.process.id = "900"
'''


def test_game_volume_touches_only_the_games_own_stream():
    calls, volumes = [], {41: 100, 42: 100}

    class R:
        def __init__(self, out=''):
            self.returncode, self.stdout = 0, out

    def run(*args, env=None):
        calls.append(args)
        if args[:2] == ('list', 'sink-inputs'):
            return R(PACTL.replace('100%', '{v41}%', 2).replace('100%', '{v42}%', 2)
                     .format(v41=volumes[41], v42=volumes[42]))
        if args[:2] == ('list', 'short'):
            return R('3\tsoren_null\tmodule-null-sink.c\ts16le 2ch 44100Hz\tRUNNING\n')
        if args[0] == 'set-sink-input-volume':
            volumes[int(args[1])] = int(args[2].rstrip('%'))
        return R()
    result = pulse_volume.apply_once(400, 80, run=run, tree=lambda pid: {400, 500})
    assert ('set-sink-input-volume', '41', '80%') in calls
    assert all(c[1] != '42' for c in calls if c[0] == 'set-sink-input-volume')
    assert result == {'status': 'applied', 'target_percent': 80,
                      'streams': [{'sink_input': 41, 'sink': 'soren_null',
                                   'volume_percent': [80, 80], 'mute': False}]}


def test_presentation_rejects_out_of_range_volume():
    from docich.presentation import _parser
    with pytest.raises(SystemExit):
        _parser().parse_args(['--display', ':1', '--title', 't', '--x', '0', '--y', '0',
                              '--width', '1', '--height', '1', '--audio-volume-percent', '0', '--', 'x'])


def test_decide_emits_records_and_never_calls_models(monkeypatch):
    import socket
    import subprocess
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **k: pytest.fail('network'))
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('subprocess'))
    actions, state = decide(name_screen(cell='ど'), {})
    assert actions[0]['buttons'] == ['a']
    assert state['_records'][0]['decision'] == 'name_type'
    assert state['bot_version'] == 'hanjuku-chart-v2'
    assert '_records' not in state['policy']


def test_battle_without_matching_message_or_order_is_not_attributed_to_a_castle():
    from docich.hanjuku_screen import Battle, Screen
    mem = {'chapter': 1, 'attack': {'general': 'ココット', 'castle': 'ジョンリギ', 'side': 'attack', 'step': '1-C1'},
           'launched': {'ゴーメン': {'general': 'どうし', 'step': '1-A2'}}}
    screen = Screen(lines=[], hand=None, text='', battle=Battle('クミン', 27, 'どうし', 90), kind='battle')
    policy.battle_step(screen, mem); policy.battle_step(screen, mem)
    assert (mem['battle']['castle'], mem['battle']['step']) == ('ゴーメン', '1-A2')
    mem = {'chapter': 1, 'attack': {'general': 'ココット', 'castle': 'ジョンリギ', 'side': 'attack'}}
    policy.battle_step(screen, mem); policy.battle_step(screen, mem)
    assert mem['battle']['castle'] is None and mem['battle']['context'] == 'unclassified'


def test_egg_or_card_announcement_does_not_end_a_battle():
    from docich.hanjuku_bot import decide as bot_decide
    c = Canvas((0, 0, 0))
    c.text(40, 183, 'たまごをつかう')
    state = {'policy': {'chapter': 1, 'battle': {'enemy': 'クミン', 'ally': 'どうし', 'enemy_hp': 17,
                                                 'ally_hp': 90, 'cards_used': []}}}
    for _ in range(3):
        _, state = bot_decide(c.frame(), state)
    assert state['policy']['battle']['enemy_hp'] == 17
    assert not [r for r in state['_records'] if r['decision'] == 'battle_result']


def test_chart_summary_is_bounded_counters():
    s = policy.summary({'chapter': 1, 'active': '1-A2', 'captured': ['キカンドン'], 'gold': 137,
                        'stats': {'wins': 2, 'losses': True}, 'orders': {'1-A1': 'launched'},
                        'name': {'done': True}, 'month': '1-5'})
    assert s['chapter'] == 1 and s['captured'] == 1 and s['wins'] == 2 and s['losses'] is None
    assert s['orders_launched'] == 1 and s['name_entered'] is True
    assert policy.summary(None)['chapter'] is None


def test_summoned_monster_turn_menu_is_answered_instead_of_stalling():
    c = Canvas()
    c.text(176, 175, 'こうげき')
    c.text(176, 191, 'もうこうげき')
    c.text(176, 207, 'たまごをつかう')
    actions, state = decide(c.frame(), {'policy': {'chapter': 1}})
    assert actions[0]['buttons'] == ['a'] and state['screen_kind'] == 'egg_battle_menu'
    assert state['_records'][0]['strategy_variant'] == 'egg_battle_attack'
    m = Canvas((20, 120, 20))
    m.text(40, 183, '59ポイントのダメージ!!')
    actions, state = decide(m.frame(), state)
    assert actions[0]['buttons'] == ['a']


def test_gift_request_buys_the_cheapest_and_declines_extra_money():
    c = Canvas()
    c.text(48, 15, '1ねん 4のつき 100G')
    c.text(24, 39, 'なにを かいあたえますか?')
    c.text(168, 167, 'ゆびわ 10G')
    c.text(168, 183, 'ペンダント 5G')
    c.text(168, 199, 'コート 20G')
    c.hand(146, 161)
    mem = {}
    assert policy.gift_step(parse(c.frame()), mem)[0]['buttons'] == ['down']
    d = Canvas()
    d.text(24, 183, '20Gで いい。')
    d.text(184, 183, 'うむッ!')
    d.text(184, 199, 'いかんッ!')
    d.hand(162, 177)
    assert policy.yes_no_step(parse(d.frame()), mem)[0]['buttons'] == ['down']
