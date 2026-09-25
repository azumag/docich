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
    # Before the charted month the gold is reserved for it.
    assert policy._plan({'chapter': 1}, {'year': 1, 'month': 4, 'gold': 999}) is None
    # Afterwards every month refills soldiers with the gold left (up to 99).
    refill = policy._plan({'chapter': 1}, {'year': 1, 'month': 6, 'gold': 999})
    assert refill['items'] == [] and refill['soldiers'] == 99
    assert refill['variant'] == 'soldier_refill_only'


def test_uncovered_month_opens_refill_instead_of_leaving_the_menu():
    mem = {'chapter': 1}
    shop = policy._plan(mem, {'year': 1, 'month': 7, 'gold': 130})
    assert shop['items'] == [] and shop['soldiers'] == 99 and shop['soldiers_done'] is False
    rec = mem['_records'][-1]
    assert rec['strategy_variant'] == 'soldier_refill_only'
    assert rec['deviation_reason'] == 'chart_month_uncovered'
    assert rec['plan'] == {'cards': [], 'soldiers': 99}
    # No gold left: the month closes without opening the refill.
    assert policy._plan({'chapter': 1}, {'year': 1, 'month': 8, 'gold': 0})['soldiers_done'] is True
    c = Canvas()
    c.text(48, 15, '1ねん 7のつき 130G')
    c.text(48, 47, 'しょうにん')
    c.text(48, 63, 'へいしほじゅう')
    c.text(128, 95, 'も〜おしまい!')
    c.hand(26, 41)
    assert policy.month_step(parse(c.frame()), mem)[0]['buttons'] == ['down']


def test_a_charted_purchase_still_ahead_keeps_its_own_soldier_budget():
    # 1ねん7つきのチャート(兵士0)は、後続の807G/1738G予定を温存するための配分。
    # 後に購入月がある間は残金を99人へ回さず、チャート人数が上限になる。
    mem = {'chapter': 3}
    shop = policy._plan(mem, {'year': 1, 'month': 7, 'gold': 475})
    assert shop['items'] == [list(i) for i in chart.CHAPTER_3_PURCHASES[0]['cards']]
    assert shop['soldiers'] == chart.CHAPTER_3_PURCHASES[0]['soldiers'] == 0
    assert shop['soldiers_done'] is True
    # 9のつきは11のつきの購入予定が残るので、残金でも補充しない。
    assert policy._plan({'chapter': 3}, {'year': 1, 'month': 9, 'gold': 900}) is None


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


def test_accept_duels_and_do_not_confirm_unrequested_month_exit():
    c = Canvas()
    c.text(24, 183, 'いって ごあいて ねがえぬか?')
    c.text(184, 183, 'うむッ!')
    c.text(184, 199, 'いかんッ!')
    c.hand(162, 177)
    mem = {}
    # うむッ! is already under the hand, so the duel is taken with one press.
    assert policy.yes_no_step(parse(c.frame()), mem)[0]['buttons'] == ['a']
    assert mem['_records'][-1]['strategy_variant'] == 'accept_duel'
    c2 = Canvas()
    c2.text(48, 15, '1ねん 5のつき 20G')
    c2.text(48, 47, 'しょうにん')
    c2.text(24, 183, 'よろしいですかな?')
    c2.text(184, 183, 'うむッ!')
    c2.text(184, 199, 'いかんッ!')
    c2.hand(162, 177)
    assert policy.month_step(parse(c2.frame()), {'chapter': 2})[0]['buttons'] == ['down']
    assert policy.month_step(parse(c2.frame()), {'chapter': 2, 'month_exit': True})[0]['buttons'] == ['a']


def test_a_lost_source_castle_releases_the_running_order_instead_of_steer_back():
    from docich.hanjuku_screen import Screen as S
    order = {'step': 'A:test:J3', 'general': 'ゼウス', 'source': 'ジョンリギ',
             'target': 'スペンソニア', 'cards': [], 'after': ['captured', 'ジョンリギ'],
             'note': 'ジョンリギ制圧後ゼウスでスペンソニアへ'}
    mem = {'chapter': 1, 'captured': [], 'active': 'A:test:J3', 'orders': {},
           'chart_plan': {'request_id': 'test', 'orders': [order]}}
    for base in chart.orders(1):
        mem['orders'][base['step']] = 'launched'
    screen = S(lines=[], hand=None, text='', kind='map')
    # The source castle was taken back: the cursor must not be steered there.
    assert policy.map_step(screen, mem, None) == []
    names = [r['decision'] for r in mem['_records']]
    assert 'order_precondition_lost' in names
    lost = next(r for r in mem['_records'] if r['decision'] == 'order_precondition_lost')
    assert lost['chart_step'] == 'A:test:J3'
    assert lost['observed_metric'] == {'captured': [], 'after': ['captured', 'ジョンリギ']}
    assert mem['active'] is None
    assert 'order_start' not in names          # never re-picks the unready order
    assert 'chart_adjust_request' in names     # holds for a chart instead


def test_commentary_is_grounded_and_holds_when_unknown():
    key, text = hanjuku_commentary.compose({'decision': 'battle_card', 'card': 'クースカン',
                                            'enemy': 'クイーン', 'enemy_hp': 60})
    assert 'クースカン' in text and '60' in text
    assert hanjuku_commentary.compose({'decision': 'battle_result', 'outcome': 'unclassified'})[1] is None
    assert hanjuku_commentary.compose({'decision': 'situation_held'})[1] is None


@pytest.mark.parametrize('ally_hp,enemy_hp,expected', [
    (90, 90, '体力は互角'),
    (91, 90, '体力で上回っている'),
    (89, 90, '体力では負けている'),
])
def test_battle_start_commentary_compares_only_observed_hp(ally_hp, enemy_hp, expected):
    key, text = hanjuku_commentary.compose({'decision': 'battle_start', 'ally': 'どうし',
        'enemy': 'ミント', 'ally_hp': ally_hp, 'enemy_hp': enemy_hp})
    assert key == 'battle:ミント:どうし'
    assert f'体力は{ally_hp}対{enemy_hp}' in text
    assert expected in text
    if ally_hp == enemy_hp:
        assert '上回っている' not in text and '負けている' not in text


def test_battle_start_commentary_equal_hp_keeps_verified_card_plan():
    _, text = hanjuku_commentary.compose({'decision': 'battle_start', 'ally': 'どうし',
        'enemy': 'にせヒーロー', 'ally_hp': 90, 'enemy_hp': 90,
        'planned_cards': ['クースカン']})
    assert '体力は互角' in text and 'クースカン' in text
    assert '上回っている' not in text


@pytest.mark.parametrize('ally_hp,enemy_hp', [
    (None, 90), (90, None), (None, None), (True, 90), (90, False),
    (-1, 90), (90, -1), ('90', 90), (90, float('nan')),
])
@pytest.mark.parametrize('plan', [[], ['クースカン']])
def test_battle_start_commentary_holds_when_either_hp_is_unknown(ally_hp, enemy_hp, plan):
    key, text = hanjuku_commentary.compose({'decision': 'battle_start', 'ally': 'どうし',
        'enemy': 'ミント', 'ally_hp': ally_hp, 'enemy_hp': enemy_hp, 'planned_cards': plan})
    assert key == 'battle:ミント:どうし'
    assert text is None


def test_battle_start_unknown_hp_is_logged_as_held_commentary(tmp_path):
    import importlib.util
    path = Path(__file__).resolve().parents[1] / 'brains/hanjuku/bot.py'
    spec = importlib.util.spec_from_file_location('hanjuku_commentary_hp_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.persist(tmp_path, {'step': 1, 'screen_kind': 'battle'}, [
        {'decision': 'battle_start', 'ally': 'どうし', 'enemy': 'ミント',
         'ally_hp': 90, 'enemy_hp': None, 'planned_cards': ['クースカン']},
    ], {'hanjuku': {'game': 'hanjuku-hero', 'runtime_id': 'g1-test',
                   'generation': 1, 'lease_id': 'lease-1'}},
        actions=[], frame_sha256='a' * 64)
    candidate = json.loads((tmp_path / 'hanjuku_commentary.jsonl').read_text())
    assert candidate['text'] is None
    assert candidate['status'] == 'held'
    assert candidate['held_reason'] == '状況判定保留'


class Game:
    def __init__(self, **narration):
        self.raw = {'hanjuku': {'narration': {'enabled': True, 'cooldown_s': 25, **narration}}}


def write_candidates(runtime, items):
    with (runtime / 'hanjuku_commentary.jsonl').open('a') as stream:
        for item in items:
            stream.write(json.dumps(item, ensure_ascii=False) + '\n')


def test_narration_enqueues_one_line_off_thread_with_dedupe_and_cooldown(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from docich.game_switch import atomic_write_json
    identity = {'game': 'hanjuku-hero', 'runtime_id': 'g1-test', 'generation': 1, 'lease_id': 'lease-1'}
    g = SimpleNamespace(state_dir=tmp_path)
    atomic_write_json(tmp_path / 'hanjuku_run.json', identity)
    monkeypatch.setattr('docich.agent.fence.read_canonical', lambda _: {'active': identity})
    sent, release = [], threading.Event()
    entered = threading.Event()

    def slow_enqueue(g, text, *, context, speaker, runtime_fence):
        assert runtime_fence == {**identity, "expires_at": now + 20}
        entered.set()
        release.wait(5)
        sent.append((text, context))
    now = time.time()
    write_candidates(tmp_path, [{'seq': 1, 'at': now, 'key': 'a', 'text': '古い候補'},
                                {'seq': 2, 'at': now, 'key': 'b', 'text': '新しい候補', **identity}])
    started = time.monotonic()
    chosen = hanjuku_narration.consider(g, Game(), tmp_path, now=now, enqueue=slow_enqueue)
    assert time.monotonic() - started < 1          # never waits for the audio queue
    assert chosen['seq'] == 2
    try:
        assert entered.wait(1)
        # Slow queue I/O must not retain the game-switch shared lock.
        from docich.game_switch import GameSwitchStore
        with GameSwitchStore(tmp_path).lock(exclusive=True):
            pass
    finally:
        release.set()
    for _ in range(50):
        if sent:
            break
        time.sleep(.05)
    assert sent == [('新しい候補', 'hanjuku_commentary')]
    time.sleep(.1)
    log = [json.loads(l) for l in (tmp_path / 'hanjuku_narration.jsonl').read_text().splitlines()]
    assert {'seq': 1, 'status': 'skipped:superseded'}.items() <= log[0].items()
    assert log[-1]['status'] == 'enqueued'
    # Same key, same text or inside the cooldown: skipped, not queued.
    write_candidates(tmp_path, [{'seq': 3, 'at': now + 5, 'key': 'c', 'text': '別の候補'}])
    assert hanjuku_narration.consider(g, Game(), tmp_path, now=now + 5, enqueue=slow_enqueue) is None
    write_candidates(tmp_path, [{'seq': 4, 'at': now + 40, 'key': 'd', 'text': '新しい候補'}])
    assert hanjuku_narration.consider(g, Game(), tmp_path, now=now + 40, enqueue=slow_enqueue) is None
    write_candidates(tmp_path, [{'seq': 5, 'at': now + 80, 'key': 'e', 'text': None, 'status': 'held'}])
    assert hanjuku_narration.consider(g, Game(), tmp_path, now=now + 80, enqueue=slow_enqueue) is None
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
    assert (mem['battle']['castle'], mem['battle']['step']) == (None, '1-A2')
    assert mem['battle']['side'] is None
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
    # Independent judgment defaults to たまご (two downs from こうげき), not attack.
    assert actions[0]['buttons'] == ['down'] and state['screen_kind'] == 'egg_battle_menu'
    assert state['_records'][0]['strategy_variant'] == 'egg_battle_use_egg'
    # Enemy summon answer is original pattern ⑥ (own egg when melee is not enough).
    assert state['_records'][0]['source_pattern'] == '⑥'
    actions, state = decide(c.frame(), state)
    assert actions[0]['buttons'] == ['down']
    actions, state = decide(c.frame(), state)
    assert actions[0]['buttons'] == ['a']
    m = Canvas((20, 120, 20))
    m.text(40, 183, '59ポイントのダメージ!!')
    actions, state = decide(m.frame(), state)
    assert actions[0]['buttons'] == ['a']


def test_experience_prefers_a_better_action_and_stays_with_an_untried_default():
    from docich import hanjuku_experience as exp_mod
    assert exp_mod.preferred(None, 'k', default='use_egg', kind='egg_summon') == 'use_egg'
    mem = {'_experience': exp_mod.empty()}
    key = exp_mod.situation_key('egg_summon', {'chapter': 1, 'battle': {'enemy': 'ミント', 'ally': 'どうし'}})
    assert exp_mod.record(mem, key, 'use_egg', 'loss')
    assert exp_mod.record(mem, key, 'attack', 'win')
    assert exp_mod.preferred(mem['_experience'], key, default='use_egg', kind='egg_summon') == 'attack'
    assert exp_mod.preferred(mem['_experience'], key, default='attack', kind='egg_summon') == 'attack'
    # Losing default with an untried alternative explores the alternative.
    only_loss = {'schema': 1, 'situations': {'k': {'actions': {'use_egg': {'wins': 0, 'losses': 2}}}}}
    assert exp_mod.preferred(only_loss, 'k', default='use_egg', kind='egg_summon') == 'attack'
    # Untried default stays default.
    assert exp_mod.preferred(exp_mod.empty(), 'k', default='use_egg', kind='egg_summon') == 'use_egg'


def test_experience_file_round_trips_and_rejects_symlinks(tmp_path):
    from docich import hanjuku_experience as exp_mod
    path = tmp_path / 'hanjuku_experience.json'
    mem = {'_experience': exp_mod.empty()}
    exp_mod.record(mem, 'egg_summon|1|ミント|どうし|1-A1', 'use_egg', 'win')
    exp_mod.save(path, mem['_experience'])
    loaded = exp_mod.load(path)
    assert loaded['situations']['egg_summon|1|ミント|どうし|1-A1']['actions']['use_egg']['wins'] == 1
    assert exp_mod.load(tmp_path / 'missing.json') == exp_mod.empty()
    path.write_text('not-json')
    assert exp_mod.load(path) == exp_mod.empty()


def test_battle_menu_without_chart_tactic_records_independent_judgment():
    from docich.hanjuku_screen import Screen
    from docich.hanjuku_font import TextLine
    screen = Screen(lines=[TextLine(175, ((160, 'たまごをつかう'),)),
                           TextLine(191, ((160, 'きりふだ'),)),
                           TextLine(207, ((160, 'たいきゃく'),))],
                    hand=None, text='たまごをつかうきりふだたいきゃく', kind='battle_menu')
    mem = {'chapter': 1, 'battle': {'enemy': 'ミント', 'ally': 'どうし', 'enemy_hp': 50,
                                    'ally_hp': 20, 'cards_used': []}}
    actions = policy.battle_menu_step(screen, mem)
    assert actions[0]['buttons'] == ['a']  # top item = たまご (behind on HP)
    rec = mem['_records'][-1]
    assert rec['decision'] == 'independent_menu'
    assert rec['strategy_variant'] == 'independent_use_egg'
    # Behind on HP with no chart card due maps to original pattern ⑥ (own egg).
    assert rec['source_pattern'] == '⑥'
    assert mem['battle']['independent']['kind'] == 'battle_menu'
    assert mem['battle']['independent']['pattern'] == '⑥'
    # Same menu: choice is held, not re-decided.
    assert policy.battle_menu_step(screen, mem)[0]['buttons'] == ['a']
    assert len([r for r in mem['_records'] if r['decision'] == 'independent_menu']) == 1
    # Ahead on HP defaults to pass (B) — original pattern ① (melee push).
    mem2 = {'chapter': 1, 'battle': {'enemy': 'ミント', 'ally': 'どうし', 'enemy_hp': 20,
                                     'ally_hp': 90, 'cards_used': []}}
    assert policy.battle_menu_step(screen, mem2)[0]['buttons'] == ['b']
    assert mem2['_records'][-1]['strategy_variant'] == 'independent_pass'
    assert mem2['_records'][-1]['source_pattern'] == '①'
    # Even HP after a clash maps to pattern ③ (adjusted melee), still pass.
    mem3 = {'chapter': 1, 'battle': {'enemy': 'ミント', 'ally': 'どうし', 'enemy_hp': 50,
                                     'ally_hp': 50, 'cards_used': [], 'clashed': True}}
    assert policy.battle_menu_step(screen, mem3)[0]['buttons'] == ['b']
    assert mem3['_records'][-1]['source_pattern'] == '③'


def test_battle_end_records_independent_choice_outcome_into_experience():
    from docich import hanjuku_experience as exp_mod
    mem = {'chapter': 1, '_experience': exp_mod.empty(),
           'battle': {'enemy': 'ミント', 'ally': 'どうし', 'enemy_hp': 0, 'ally_hp': 30,
                      'castle': None, 'side': None, 'cards_used': [],
                      'independent': {'kind': 'battle_menu', 'key': 'battle_menu|1|ミント|どうし|1-A1|behind',
                                      'action': 'use_egg'}}}
    policy.battle_end(mem, 'map')
    policy.battle_end(mem, 'map')
    row = mem['_experience']['situations']['battle_menu|1|ミント|どうし|1-A1|behind']
    assert row['actions']['use_egg']['wins'] == 1
    result = next(r for r in mem['_records'] if r['decision'] == 'experience_result')
    assert result['experience_action'] == 'use_egg' and result['outcome'] == 'win'


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


def test_unaffordable_gift_request_is_recognised_and_answered():
    c = Canvas()
    c.text(48, 15, '1ねん 6のつき 95G')
    c.text(24, 39, 'どれか ほしいなー')
    c.text(168, 167, 'こうすい100G')
    c.text(168, 183, 'くるま 200G')
    c.hand(146, 161)
    mem = {}
    s = parse(c.frame())
    assert s.kind == 'gift_request'
    assert policy.gift_step(s, mem)[0]['buttons'] == ['a']
    assert mem['_records'][-1]['observed_metric'] == {'price': 100, 'affordable': False}


def test_missing_chart_general_is_replaced_by_a_non_hero_present_at_the_source():
    c = Canvas()
    c.text(64, 31, 'しゅつげき')
    c.text(64, 47, 'ステータス')
    c.text(144, 39, 'どうし')
    c.text(144, 55, 'ゼウス')
    c.hand(122, 33)
    mem = {'chapter': 1, 'active': '1-C1', 'orders': {'1-C1': 'pending'}}
    s = parse(c.frame())
    assert s.kind == 'general_list'
    assert policy.deploy_step(s, mem)[0]['buttons'] == ['down']
    rec = mem['_records'][-1]
    assert rec['strategy_variant'] == 'substitute_general' and mem['general_override']['1-C1'] == 'ゼウス'


def test_empty_general_list_canvas_parses_as_general_list_without_hand():
    c = Canvas()
    c.text(64, 31, 'しゅつげき')
    c.text(136, 39, 'しょうぐんは')
    c.text(64, 47, 'ステータス')
    c.text(152, 79, 'おりません……')
    s = parse(c.frame())
    assert s.hand is None
    assert s.kind == 'general_list'
    mem = {'chapter': 1, 'active': '1-C1', 'orders': {'1-C1': 'pending'}}
    actions = policy.deploy_step(s, mem)
    assert actions == [policy.pad('b'), policy.pad('b')]
    assert mem['source_override']['1-C1'] == 'ほんじょう'
    assert mem['_records'][-1]['decision'] == 'order_source_changed'


def test_narration_delivery_summary_counts_without_text(tmp_path):
    (tmp_path / 'hanjuku_narration.jsonl').write_text('\n'.join(json.dumps(x) for x in [
        {'status': 'enqueued', 'text': 'a'}, {'status': 'skipped:cooldown'}, {'status': 'skipped:held'},
        {'status': 'delivery_failed'}]) + '\n')
    assert hanjuku_narration.delivery_summary(tmp_path) == {'enqueued': 1, 'delivery_failed': 1, 'skipped': 2}


def test_name_confirmation_preserves_unknown_tiles_instead_of_erasing_them():
    from docich.hanjuku_screen import Screen
    from docich.hanjuku_font import TextLine
    screen = Screen(lines=[TextLine(55, ((64, 'ど'), (72, UNKNOWN), (80, 'う'), (88, 'し')))],
                    hand=None, text='', kind='name_entry')
    mem = {}
    assert policy.name_step(screen, mem) == []
    assert not mem['name']['done']
    assert mem['_records'][-1]['decision'] == 'name_wait'
    assert UNKNOWN in mem['name']['typed']


def test_unreadable_name_screen_never_uses_legacy_confirmation(monkeypatch):
    from docich import hanjuku_bot, hanjuku_screen
    monkeypatch.setattr(hanjuku_bot, 'classify', lambda frame: 'name')
    monkeypatch.setattr(hanjuku_screen, 'parse', lambda *a, **k:
                        hanjuku_screen.Screen(lines=[], hand=None, text='', kind='unknown'))
    state = {}
    for _ in range(3):
        actions, state = hanjuku_bot.decide(Canvas().frame(), state)
        assert actions == []
        assert state['_records'][-1]['decision'] == 'name_wait'
    assert not state['policy'].get('name', {}).get('done')


@pytest.mark.parametrize('step,ally,hp,expected', [
    ('1-V2', 'ヴィーナス', 60, 'フットバース'),
    ('1-C2', 'ココット', 14, None),
    ('1-C2', 'ココット', 13, 'ダイチスイム'),
    ('1-A2', 'どうし', 25, None),
    ('1-A2', 'どうし', 24, 'フットバース'),
])
def test_garbanzo_tactics_follow_each_generals_chart_branch(step, ally, hp, expected):
    from docich.hanjuku_screen import Battle, Screen
    mem = {'chapter': 1, 'attack': {'general': ally, 'castle': None, 'side': 'attack', 'step': step}}
    screen = Screen(lines=[], hand=None, text='', battle=Battle('ガルバンゾー', hp, ally, 60), kind='battle')
    assert policy.battle_step(screen, mem) == []
    actions = policy.battle_step(screen, mem)
    if expected is None:
        assert actions == [] and not mem['battle'].get('card_flow')
    else:
        assert actions[0]['buttons'] == ['b']
        assert mem['battle']['card_flow']['card'] == expected
        assert mem['_records'][-1]['chart_step'] == step




def _card_evidence_battle(*, retry=False):
    from docich.hanjuku_screen import Battle, Screen
    mem = {'chapter': 1, 'attack': {'general': 'どうし', 'castle': 'けっかい',
                                   'side': 'attack', 'step': '1-B1'}}
    if retry:
        mem['card_override'] = {'1-B1': ['イッテツーン', 'イッテツーン']}
    screen = Screen(lines=[], hand=None, text='', battle=Battle('クイーン', 60, 'どうし', 90), kind='battle')
    policy.battle_step(screen, mem); policy.battle_step(screen, mem)
    if not retry:
        screen.battle.enemy_hp = 59
        policy.battle_step(screen, mem)  # the observed clash schedules クースカン
    return mem, screen


def _card_screen(cards, *, announcement=None, hand=True):
    from docich.hanjuku_font import TextLine
    from docich.hanjuku_screen import Screen
    lines = [TextLine(176 + index * 16, tuple((176 + i * 8, ch) for i, ch in enumerate(card)))
             for index, card in enumerate(cards)]
    return Screen(lines=lines, hand=(150, 170, 172, 186) if hand else None,
                  text=announcement or ''.join(cards), kind='text')


def test_missing_and_selected_cards_do_not_confirm_use_or_unlock_after_card():
    mem, screen = _card_evidence_battle()
    cur = mem['battle']
    cur['card_flow'] = {'card': 'クースカン', 'stage': 'list'}
    assert policy.card_list_step(_card_screen(['ノリウツール']), mem)
    assert cur['cards_used'] == [] and cur['cards_missing'] == ['クースカン']
    missing = mem['_records'][-1]
    assert missing['strategy_variant'] == 'chart_card_unavailable' and missing['deviation_reason']
    assert policy.battle_step(screen, mem) == []  # no ノリウツール without confirmed クースカン
    cur['card_flow'] = {'card': 'クースカン', 'stage': 'list'}
    policy.card_list_step(_card_screen(['クースカン']), mem)
    assert cur['cards_selected'] == ['クースカン'] and cur['cards_used'] == []
    # A one-card list, missing hand, wrong card, or incomplete text is no receipt.
    for candidate in (_card_screen(['クースカン']), _card_screen(['クースカン'], hand=False),
                      _card_screen(['ノリウツール'], announcement='ノリウツールをつかった', hand=False)):
        assert policy.card_list_step(candidate, mem) == []
        assert cur['cards_used'] == []
    policy.battle_step(screen, mem); policy.battle_step(screen, mem)
    assert cur['card_flow'] is None and cur['cards_unclassified'] == ['クースカン']
    assert policy.battle_step(screen, mem) == []


@pytest.mark.parametrize('statement', ['クースカンをつかった', 'クースカンをしようした'])
def test_uncalibrated_card_text_never_confirms_use_or_unlocks_after_card(statement):
    mem, screen = _card_evidence_battle()
    cur = mem['battle']
    cur['card_flow'] = {'card': 'クースカン', 'stage': 'list'}
    mem['stats'] = {'cards_used': 0, 'cards_confirmed': 0, 'card_evidence_version': 1,
                    'wins': 0, 'losses': 0, 'unclassified': 0}
    assert policy.summary(mem)['cards_used'] is None  # active flow, before selection
    policy.card_list_step(_card_screen(['クースカン']), mem)
    assert cur['card_consumption_complete'] is False
    assert policy.summary(mem)['cards_used'] is None  # selection planned, before next screen
    candidate = _card_screen(['クースカン'], announcement=statement, hand=False)
    policy.card_list_step(candidate, mem)
    policy.card_list_step(candidate, mem)
    assert cur['cards_used'] == []
    assert policy.summary(mem)['cards_used'] is None
    pending = [r for r in mem['_records'] if r['decision'] == 'battle_card_candidate']
    assert len(pending) == 1 and pending[0]['observed_metric']['confirmation'] == 'unclassified'
    assert not any(r['decision'] == 'battle_card_used' for r in mem['_records'])
    # On return, bounded unconfirmed handling clears the flow without unlocking
    # the dependent ノリウツール tactic, even for apparently explicit use text.
    policy.battle_step(screen, mem); policy.battle_step(screen, mem)
    assert cur['card_flow'] is None
    assert policy.summary(mem)['cards_used'] is None  # unclassified despite cleared flow
    assert policy.battle_step(screen, mem) == []
    cur['enemy_hp'] = 0
    policy.battle_end(mem, 'map'); policy.battle_end(mem, 'map')
    assert mem['stats']['cards_used'] is None and mem['stats']['cards_confirmed'] == 0


def test_retry_variant_survives_battle_card_and_result_records():
    mem, screen = _card_evidence_battle(retry=True)
    cur = mem['battle']
    cur['card_flow']['stage'] = 'list'
    policy.card_list_step(_card_screen(['イッテツーン']), mem)
    policy.card_list_step(_card_screen(['イッテツーン'], announcement='イッテツーンをつかった', hand=False), mem)
    cur['enemy_hp'] = 0
    policy.battle_end(mem, 'map'); policy.battle_end(mem, 'map')
    records = [r for r in mem['_records'] if r['decision'] in
               {'battle_start', 'battle_card', 'battle_card_selected', 'battle_card_candidate', 'battle_result'}]
    assert {r['decision'] for r in records} == {
        'battle_start', 'battle_card', 'battle_card_selected', 'battle_card_candidate', 'battle_result'}
    assert all(r['chart_step'] == '1-B1' and r['strategy_variant'] == 'retry_with_opening_cards'
               and r['deviation_reason'] and r['expected_metric'] is not None
               and r['observed_metric'] is not None for r in records)
    assert records[-1]['resulting_event'] == 'chapter_1_boss_defeated'


def test_legacy_selected_cards_and_stats_are_not_confirmed_by_upgrade():
    from docich.hanjuku_screen import Screen
    mem = {'chapter': 1, 'stats': {'wins': 1, 'losses': 1, 'unclassified': 0, 'cards_used': 4},
           'card_override': {'1-C1': ['イッテツーン', 'イッテツーン']},
           'battle': {'step': '1-C1', 'enemy': 'ラズベリー', 'ally': 'ココット',
                      'cards_used': ['イッテツーン'], 'card_flow': {'card': 'イッテツーン', 'stage': 'announce'}}}
    assert policy.summary(mem)['cards_used'] is None
    policy.observe_events(Screen(lines=[], hand=None, text='', kind='unknown'), mem)
    assert mem['stats']['cards_used'] is None and mem['stats']['cards_confirmed'] == 0
    assert mem['battle']['cards_used'] == [] and mem['battle']['card_flow'] is None
    assert mem['battle']['cards_unclassified'] == ['イッテツーン']
    migrated = next(r for r in mem['_records'] if r['decision'] == 'battle_card_evidence_migrated')
    assert migrated['observed_metric']['legacy_cards_unclassified'] == ['イッテツーン']
    assert policy.summary(mem)['cards_used'] is None
    assert mem['battle']['strategy_variant'] == 'retry_with_opening_cards'
    old_log_count = len(mem['_records'])
    policy.observe_events(Screen(lines=[], hand=None, text='', kind='unknown'), mem)
    assert len(mem['_records']) == old_log_count


@pytest.mark.parametrize('stage', ['menu', 'down', 'list', 'announce'])
def test_unconfirmed_card_flow_is_bounded_after_return_to_battle(stage):
    mem, screen = _card_evidence_battle()
    cur = mem['battle']
    cur['card_flow'] = {'card': 'クースカン', 'stage': stage}
    policy.battle_step(screen, mem); policy.battle_step(screen, mem)
    assert cur['card_flow'] is None
    assert cur['cards_used'] == []
    assert cur['cards_unclassified'] == ['クースカン']

def test_hp_defeat_followed_by_living_hero_does_not_count_a_general_loss():
    from docich.hanjuku_screen import Screen
    mem = {'chapter': 1, 'battle': {'enemy': 'だいじん', 'ally': 'どうし', 'enemy_hp': 15,
                                   'ally_hp': 0, 'castle': None, 'side': None, 'cards_used': []}}
    policy.battle_end(mem, 'map'); policy.battle_end(mem, 'map')
    assert mem['stats']['losses'] == 1
    assert mem['stats']['generals_lost'] is None
    result = next(r for r in mem['_records'] if r['decision'] == 'battle_result')
    assert result['outcome'] == 'loss' and result['observed_metric']['general_loss'] == 'unclassified'
    mem['launched'] = {'キカンドン': {'general': 'どうし', 'step': '1-A1'}}
    policy.message_step(Screen(lines=[], hand=None, text='どうししょうぐんがキカンドンじょうにのりこんだ',
                               kind='attack_started'), mem)
    assert mem['attack']['general'] == 'どうし'
    assert policy.summary(mem)['losses'] == 1
    assert policy.summary(mem)['generals_lost'] is None


@pytest.mark.parametrize('legacy_count', [0, 1, 7])
def test_next_observation_invalidates_legacy_general_loss_without_erasing_battle_counts(legacy_count):
    from docich.hanjuku_screen import Screen
    mem = {'chapter': 1, 'stats': {'wins': 2, 'losses': 1, 'unclassified': 3, 'cards_used': 4,
                                  'generals_lost': legacy_count},
           'previous_stats': {'wins': 1, 'losses': 2, 'generals_lost': 2}}
    # Diagnostics must not expose the stale scalar even before another frame.
    assert policy.summary(mem)['generals_lost'] is None
    screen = Screen(lines=[], hand=None, text='', kind='unknown')
    policy.observe_events(screen, mem)
    assert {k: mem['stats'][k] for k in ('wins', 'losses', 'unclassified', 'generals_lost')} == {
        'wins': 2, 'losses': 1, 'unclassified': 3, 'generals_lost': None}
    assert mem['stats']['cards_used'] is None
    assert mem['previous_stats']['wins'] == 1 and mem['previous_stats']['losses'] == 2
    assert mem['previous_stats']['generals_lost'] is None
    invalidations = [r for r in mem['_records'] if r['decision'] == 'metric_invalidated' and r['metric'] == 'generals_lost']
    assert len(invalidations) == 2
    assert invalidations[0]['observed_metric'] == {'previous_inferred_count': legacy_count,
                                                 'generals_lost': None, 'status': 'unclassified'}
    policy.observe_events(screen, mem)
    assert len([r for r in mem['_records'] if r['decision'] == 'metric_invalidated' and r['metric'] == 'generals_lost']) == 2


def test_no_battle_or_observer_does_not_report_zero_general_losses():
    assert policy.summary(None)['generals_lost'] is None
    assert policy.summary({'stats': {'generals_lost': 0}})['generals_lost'] is None

def test_route_order_is_not_evidence_of_a_castle_capture():
    from docich.hanjuku_screen import Battle, Screen
    mem = {'chapter': 1, 'launched': {'キカンドン': {'general': 'どうし', 'step': '1-A1'}}}
    screen = Screen(lines=[], hand=None, text='', battle=Battle('ミント', 0, 'どうし', 90), kind='battle')
    policy.battle_step(screen, mem); policy.battle_step(screen, mem)
    policy.battle_end(mem, 'map'); policy.battle_end(mem, 'map')
    assert mem['stats']['wins'] == 1
    assert not mem.get('captured')
    assert mem['_records'][-1]['resulting_event'] == 'win'


def test_boss_win_does_not_invent_a_new_chapter():
    mem = {'chapter': 1, 'battle': {'enemy': 'クイーン', 'ally': 'どうし', 'enemy_hp': 0,
                                   'ally_hp': 70, 'castle': None, 'side': None, 'cards_used': []}}
    policy.battle_end(mem, 'map'); policy.battle_end(mem, 'map')
    assert mem['chapter'] == 1
    assert mem['_records'][-1]['resulting_event'] == 'chapter_1_boss_defeated'
    assert mem['_records'][-1]['resulting_stage'] is None


def test_visible_chapter_transition_clears_old_route_state_and_records_evidence():
    from docich.hanjuku_screen import Screen
    mem = {'chapter': 1, 'active': '1-A2', 'orders': {'1-A2': 'launched'},
           'captured': ['キカンドン'], 'launched': {'ゴーメン': {'general': 'どうし'}},
           'cursor': [295, 606], 'shop': {'key': '1-5'}, 'retries': {'1-A2': 2},
           'name': {'target': 'どうし', 'done': True}, 'stats': {'wins': 4}}
    screen = Screen(lines=[], hand=None, text='', kind='main_menu',
                    header={'chapter': 2, 'year': 1, 'month': 6, 'gold': 294})
    policy.observe_events(screen, mem)
    assert mem['chapter'] == 2 and mem['variant'] == 'chart_unavailable'
    assert mem['name']['done'] and mem['stats']['wins'] == 4
    for key in ('active', 'orders', 'captured', 'launched', 'cursor', 'shop', 'retries'):
        assert not mem.get(key)
    rec = next(r for r in mem['_records'] if r['decision'] == 'chapter_seen')
    assert rec['observed_metric'] == {'chapter': 2}
    assert rec['resulting_stage'] == 2 and rec['previous_stage'] == 1
    assert all(r['chapter'] == 2 for r in mem['_records'])
    mem['orders'] = {'2-A1': 'launched'}
    policy.observe_events(screen, mem)
    assert mem['orders'] == {'2-A1': 'launched'}


@pytest.mark.parametrize('change', ['lease', 'game', 'terminal'])
def test_narration_rechecks_identity_after_thread_dispatch(tmp_path, monkeypatch, change):
    from types import SimpleNamespace
    from docich.agent import fence
    from docich.game_switch import atomic_write_json
    identity = {'game': 'hanjuku-hero', 'runtime_id': 'g1-test', 'generation': 1, 'lease_id': 'lease-1'}
    active = dict(identity)
    atomic_write_json(tmp_path / 'hanjuku_run.json', identity)
    monkeypatch.setattr(fence, 'read_canonical', lambda _: {'active': active})
    ready, release = threading.Event(), threading.Event()
    original = fence.shared_section
    def delayed(root, fn, **kwargs):
        ready.set()
        assert release.wait(2)
        return original(root, fn, **kwargs)
    monkeypatch.setattr(fence, 'shared_section', delayed)
    sent = []
    write_candidates(tmp_path, [{'seq': 1, 'at': time.time(), 'key': 'a', 'text': '実況', **identity}])
    start = time.monotonic()
    hanjuku_narration.consider(SimpleNamespace(state_dir=tmp_path), Game(), tmp_path,
                               enqueue=lambda *a, **k: sent.append(a))
    assert time.monotonic() - start < 1
    try:
        assert ready.wait(1)
        if change == 'lease':
            active['lease_id'] = 'lease-2'
        elif change == 'game':
            active['game'] = 'sorengame'
        else:
            atomic_write_json(tmp_path / 'hanjuku_run.json', {**identity, 'terminal_reason': 'game_over'})
    finally:
        release.set()
    log_path = tmp_path / 'hanjuku_narration.jsonl'
    for _ in range(50):
        if log_path.exists():
            break
        time.sleep(.02)
    assert not sent
    record = json.loads(log_path.read_text().splitlines()[-1])
    assert record['status'] == ('skipped:terminal' if change == 'terminal' else 'skipped:fence_lost')


def test_bot_records_plans_separately_from_sent_input_with_full_identity(tmp_path):
    import importlib.util
    path = Path(__file__).resolve().parents[1] / 'brains/hanjuku/bot.py'
    spec = importlib.util.spec_from_file_location('hanjuku_bot_entry_trace_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    identity = {'game': 'hanjuku-hero', 'runtime_id': 'g1-test', 'generation': 1, 'lease_id': 'lease-1'}
    state = {'step': 7, 'screen_kind': 'map', 'policy': {'active': '1-A1', 'variant': 'chart'}}
    actions = [{'type': 'pad', 'buttons': ['right'], 'hold_ms': 100}]
    module.persist(tmp_path, state, [{'decision': 'order_start', 'chart_step': '1-A1',
                   'general': 'どうし', 'source': 'ほんじょう', 'target': 'キカンドン', 'cards': [], 'reason': 'チャート順'}],
                   {'hanjuku': identity}, actions=actions, frame_sha256='b'*64)
    plans = [json.loads(x) for x in (tmp_path/'hanjuku_decisions.jsonl').read_text().splitlines()]
    assert plans[0]['dispatch_status'] == 'planned_not_yet_sent'
    assert plans[0]['planned_actions'] == actions
    assert plans[0]['decision_id'] == plans[1]['decision_id'] == state['decision_trace']['decision_id']
    assert plans[1]['frame_sha256'] == 'b'*64
    candidate = json.loads((tmp_path/'hanjuku_commentary.jsonl').read_text())
    assert all(candidate[k] == v for k, v in identity.items())
    assert not (tmp_path/'hanjuku_events.jsonl').exists()


def test_name_confirmation_keeps_the_exact_decision_frame(tmp_path):
    import importlib.util
    path = Path(__file__).resolve().parents[1] / 'brains/hanjuku/bot.py'
    spec = importlib.util.spec_from_file_location('hanjuku_name_snapshot_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    canvas = Canvas()
    canvas.text(64, 55, 'どうし')
    frame = canvas.frame()
    state = {'step': 123, 'screen_kind': 'name_entry'}
    module.persist(tmp_path, state, [{'decision': 'name_confirm', 'typed': 'どうし'}],
                   {'hanjuku': {'game': 'hanjuku-hero', 'runtime_id': 'g1-test',
                    'generation': 1, 'lease_id': 'lease'}},
                   actions=[{'type': 'pad', 'buttons': ['start'], 'hold_ms': 100}],
                   frame_sha256=frame.digest(), frame=frame)
    record = json.loads((tmp_path/'hanjuku_decisions.jsonl').read_text().splitlines()[-1])
    from docich.hanjuku_pixels import read_png
    assert read_png(tmp_path/'hanjuku_frames'/record['snapshot']).digest() == record['frame_sha256']
    assert record['snapshot'] == 'decision-003.png'


@pytest.mark.parametrize('decision', [None, 'battle_card', 'battle_card_selected',
                                     'battle_card_used', 'battle_card_missing',
                                     'battle_card_unclassified', 'barrier_removed'])
def test_card_flow_and_event_frames_are_linked_from_action_plan(tmp_path, decision):
    import importlib.util
    from docich.hanjuku_pixels import read_png
    path = Path(__file__).resolve().parents[1] / 'brains/hanjuku/bot.py'
    spec = importlib.util.spec_from_file_location('hanjuku_card_snapshot_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    canvas = Canvas()
    canvas.text(64, 55, 'イッテツーン')
    frame = canvas.frame()
    state = {'step': 124, 'screen_kind': 'text', 'phase': 'event',
             'policy': {'battle': {'card_flow': {'card': 'イッテツーン', 'stage': 'announce'}
                                  if decision is None else None}}}
    records = [] if decision is None else [{'decision': decision, 'card': 'イッテツーン',
                                           'enemy': 'だいじん', 'enemy_hp': 90, 'reason': '開幕'}]
    module.persist(tmp_path, state, records, {'hanjuku': {'game': 'hanjuku-hero',
                   'runtime_id': 'g1-test', 'generation': 1, 'lease_id': 'lease'}},
                   actions=[], frame_sha256=frame.digest(), frame=frame)
    entries = [json.loads(line) for line in (tmp_path/'hanjuku_decisions.jsonl').read_text().splitlines()]
    plan = entries[0]
    assert plan['event'] == 'action_plan' and plan['snapshot'] == 'decision-004.png'
    assert plan['dispatch_status'] == 'planned_not_yet_sent'
    assert read_png(tmp_path/'hanjuku_frames'/plan['snapshot']).digest() == plan['frame_sha256']
    assert plan['frame_sha256'] == state['decision_trace']['frame_sha256']
    assert plan['decision_id'] == state['decision_trace']['decision_id']
    if decision is None:
        assert len(entries) == 1
    else:
        assert entries[1]['snapshot'] == plan['snapshot']


@pytest.mark.parametrize('in_battle', [False, True])
def test_action_plan_and_summary_share_current_battle_strategy(tmp_path, in_battle):
    import importlib.util
    path = Path(__file__).resolve().parents[1] / 'brains/hanjuku/bot.py'
    spec = importlib.util.spec_from_file_location('hanjuku_strategy_trace_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    mem = {'active': '1-A2', 'variant': 'chart'}
    expected = {'chart_step': '1-A2', 'strategy_variant': 'chart', 'deviation_reason': None}
    if in_battle:
        mem['battle'] = {'step': '1-A1', 'strategy_variant': 'retry_with_opening_cards',
                         'deviation_reason': '白兵で敗北したため開幕切り札で再攻撃'}
        expected = {'chart_step': '1-A1', 'strategy_variant': 'retry_with_opening_cards',
                    'deviation_reason': mem['battle']['deviation_reason']}
    record = {'decision': 'battle_card_candidate', **expected}
    state = {'step': 12, 'screen_kind': 'battle' if in_battle else 'map', 'policy': mem}
    module.persist(tmp_path, state, [record], {'hanjuku': {'game': 'hanjuku-hero',
                   'runtime_id': 'g1-test', 'generation': 1, 'lease_id': 'lease'}},
                   actions=[], frame_sha256='d'*64)
    entries = [json.loads(line) for line in (tmp_path/'hanjuku_decisions.jsonl').read_text().splitlines()]
    plan, decision = entries
    view = policy.summary(mem)
    for key in ('chart_step', 'strategy_variant'):
        assert plan[key] == view[key] == decision[key] == expected[key]
    assert plan['deviation_reason'] == decision['deviation_reason'] == expected['deviation_reason']
    assert record == {'decision': 'battle_card_candidate', **expected}


@pytest.mark.parametrize('input_kind', ['barrier_removed', 'battle_card_selected'])
def test_input_context_precedes_battle_but_informational_records_do_not(tmp_path, input_kind):
    import importlib.util
    path = Path(__file__).resolve().parents[1] / 'brains/hanjuku/bot.py'
    spec = importlib.util.spec_from_file_location('hanjuku_input_context_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    battle = {'step': '1-A1', 'strategy_variant': 'retry_with_opening_cards',
              'deviation_reason': '白兵敗北後の再攻撃'}
    mem = {'active': '1-A2', 'variant': 'chart', 'battle': battle}
    context = ({'chart_step': '1-barrier', 'strategy_variant': 'chart', 'deviation_reason': None}
               if input_kind == 'barrier_removed' else
               {'chart_step': battle['step'], 'strategy_variant': battle['strategy_variant'],
                'deviation_reason': battle['deviation_reason']})
    records = [{'decision': input_kind, **context},
               {'decision': 'metric_invalidated', 'chart_step': 'wrong', 'strategy_variant': 'wrong'},
               {'decision': 'battle_card_evidence_migrated', 'chart_step': 'wrong', 'strategy_variant': 'wrong'}]
    module.persist(tmp_path, {'step': 8, 'policy': mem}, records, {'hanjuku': {}},
                   actions=[{'type': 'pad', 'buttons': ['a'], 'hold_ms': 100}], frame_sha256='e'*64)
    entries = [json.loads(line) for line in (tmp_path/'hanjuku_decisions.jsonl').read_text().splitlines()]
    for key, value in context.items():
        assert entries[0][key] == entries[1][key] == value
    assert entries[-1]['strategy_variant'] == 'wrong'  # original records stay intact
    assert policy.summary(mem)['chart_step'] == '1-A1'
    assert policy.summary(mem)['strategy_variant'] == 'retry_with_opening_cards'


@pytest.mark.parametrize('decision', ['order_launched', 'order_failed', 'attack_observed', 'defense_observed'])
def test_input_context_survives_order_cleanup_and_later_information(tmp_path, decision):
    import importlib.util
    path = Path(__file__).resolve().parents[1] / 'brains/hanjuku/bot.py'
    spec = importlib.util.spec_from_file_location('hanjuku_finished_order_trace_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    record = {'decision': decision, 'chart_step': '1-C1',
              'strategy_variant': 'retry_with_opening_cards', 'deviation_reason': '白兵敗北後の再攻撃',
              'general': 'ゼウス', 'castle': 'ジョンリギ'}
    module.persist(tmp_path, {'step': 9, 'policy': {'active': None, 'variant': 'chart'}},
                   [record, {'decision': 'metric_invalidated', 'chart_step': 'wrong'}],
                   {'hanjuku': {}}, actions=[{'type': 'pad', 'buttons': ['a'], 'hold_ms': 100}],
                   frame_sha256='f'*64)
    plan = json.loads((tmp_path/'hanjuku_decisions.jsonl').read_text().splitlines()[0])
    for key in ('chart_step', 'strategy_variant', 'deviation_reason'):
        assert plan[key] == record[key]


def test_failed_menu_drops_cursor_estimate_and_stops_sea_a_spam():
    """g340: A on open water with a stale ほんじょう estimate looped forever."""
    from docich.hanjuku_pixels import Frame
    from docich.hanjuku_screen import Screen
    frame = Frame(256, 224, bytes(256 * 224 * 3))
    mem = {'chapter': 1, 'variant': 'chart', 'active': '1-A1',
           'orders': {'1-A1': 'pending'}, 'picked': [],
           'cursor': list(policy.chart.castles(1)['ほんじょう']),
           'expect_menu': True, '_records': []}
    screen = Screen(lines=[], hand=None, text='', kind='map',
                    cursor=(200, 160))  # bracket on water, not a roof cell
    actions = policy.map_step(screen, mem, frame)
    assert actions == []
    kinds = [r['decision'] for r in mem['_records']]
    assert 'localize' in kinds and 'nav_reset' in kinds
    assert not mem.get('cursor') and not mem.get('nav_last')
    assert mem['menu_miss'] == 1 and mem['uncertain'] is True
    # Without a roof anchor, a later arrived must not re-send A.
    mem['_records'] = []
    mem['expect_menu'] = False
    # Simulate update_world wrongly still "at goal" without anchor.
    mem['cursor'] = list(policy.chart.castles(1)['ほんじょう'])
    monkey_arrived = Screen(lines=[], hand=None, text='', kind='map', cursor=(200, 160))
    # menu_miss path: arrived but no anchor → hold, no A
    def arrived(*_a, **_k):
        return 'arrived'
    import docich.hanjuku_policy as pol
    old = pol.nav_step
    pol.nav_step = arrived
    try:
        actions = policy.map_step(monkey_arrived, mem, frame)
    finally:
        pol.nav_step = old
    assert actions == []
    assert [r['decision'] for r in mem['_records']] == ['situation_held']
    assert not any(a.get('buttons') == ['a'] for a in actions)


def test_castle_menu_seen_clears_menu_miss():
    state = {'policy': {'chapter': 1, 'menu_miss': 2, 'expect_menu': True,
                        'orders': {}, 'picked': []}}
    c = Canvas()
    c.text(64, 31, 'しゅつげき')
    c.text(64, 47, 'ステータス')
    c.hand(40, 29)
    actions, state = decide(c.frame(), state)
    assert 'menu_miss' not in state['policy']
    assert 'expect_menu' not in state['policy']


def test_chart_adjust_request_commentary_states_ai_is_generating():
    key, text = hanjuku_commentary.compose(
        {'decision': 'chart_adjust_request', 'off_chart_reason': 'orders_exhausted'})
    assert key == 'chart_adjust_request'
    assert 'AI' in text and 'チャート' in text
    assert 'chart_adjust_request' in hanjuku_commentary.SPOKEN


def test_chart_adjust_applied_commentary_lists_order_content():
    key, text = hanjuku_commentary.compose({
        'decision': 'chart_adjust_applied',
        'order_digest': [
            {'general': 'ココット', 'source': 'ほんじょう', 'target': 'ジョンリギ', 'cards': []},
            {'general': 'ヴィーナス', 'source': 'ナキューメラ', 'target': 'カストーラ', 'cards': ['フットバース']},
            {'general': 'どうし', 'source': 'ゴーメン', 'target': 'スペンソニア', 'cards': []},
            {'general': 'どうし', 'source': 'スペンソニア', 'target': 'けっかい', 'cards': ['クースカン']},
        ],
        'local_steps': ['J1', 'J2', 'J3', 'J4'],
    })
    assert key == 'chart_adjust_applied'
    assert 'AI' in text and 'ココット' in text and 'ジョンリギ' in text
    assert len(text) <= 120
    assert 'chart_adjust_applied' in hanjuku_commentary.SPOKEN
    # Long plans collapse to a bounded line instead of exceeding MAX_TEXT.
    long_digest = [{'general': f'将軍{i}', 'source': 'ほんじょう', 'target': f'城{i}', 'cards': []}
                   for i in range(8)]
    _, long_text = hanjuku_commentary.compose(
        {'decision': 'chart_adjust_applied', 'order_digest': long_digest})
    assert len(long_text) <= 120 and 'など' in long_text and '8手' in long_text


def test_jev_interim_commentary_includes_choice_and_confidence():
    key, text = hanjuku_commentary.compose({
        'decision': 'chart_interim_order', 'general': 'どうし', 'target': 'スペンソニア',
        'source': 'ほんじょう', 'confidence': 0.85})
    assert key == 'jev_interim:スペンソニア'
    assert 'JEV' in text and 'スペンソニア' in text and '0.85' in text
    assert 'chart_interim_order' in hanjuku_commentary.SPOKEN

    # Fallback (no hold): always announces the sortie, never見送り.
    fb_key, fb = hanjuku_commentary.compose({
        'decision': 'chart_interim_order', 'general': 'ココット', 'target': 'ジョンリギ',
        'confidence': None, 'strategy_variant': 'chart_interim_fallback'})
    assert fb_key == 'jev_interim:ジョンリギ'
    assert 'ココット' in fb and '再攻撃' in fb and '見送' not in fb
    assert len(fb) <= 120

    _, hold = hanjuku_commentary.compose(
        {'decision': 'chart_interim_hold', 'deviation_reason': 'interim_no_candidates'})
    assert '調整チャート' in hold and '見送' not in hold
    assert 'chart_interim_hold' in hanjuku_commentary.SPOKEN


def monster_menu_frame(skills, *, ally=None, enemy=None, cursor=0, menu_left=True):
    """Bottom command box plus stacked HP panel, as measured on live frames.

    The command box sits left (x=40) or right (x=176) and the HP panel always
    on the opposite side; the knight sprite sits immediately left of the
    selected row. The drop mark names the ally (top, red) and enemy (bottom,
    blue) side of the panel.
    """
    c = Canvas()
    mx = 40 if menu_left else 176
    for i, skill in enumerate(skills):
        c.text(mx, 176 + 16 * i, skill)
    c.text(mx, 176 + 16 * len(skills), 'たまごに もどれ')
    if cursor is not None:
        ky = 176 + 16 * cursor
        for dy in range(14):
            for dx in range(12):
                c.put(mx - 30 + dx, ky - 6 + dy, (230, 105, 74))
    px0 = 128 if menu_left else 0
    for row, unit in ((0, ally), (1, enemy)):
        if not unit:
            continue
        name, hp = unit
        y = 176 + 24 * row
        for yy in range(y - 8, y + 8):
            for xx in range(px0, px0 + 120):
                c.put(xx, yy, (238, 238, 238))
        mark = (222, 72, 65) if row == 0 else (57, 121, 189)
        for yy in range(y - 6, y + 6):
            for xx in range(px0 + 1, px0 + 17):
                c.put(xx, yy, mark)
        c.text(24 if px0 == 0 else 136, y, name, (32, 32, 32))
        hp_text = str(hp)
        c.text((120 if px0 == 0 else 240) - 8 * len(hp_text), y, hp_text, (32, 32, 32))
    return c.frame()


def test_monster_menu_reads_option_rows_knight_and_summoned_panel():
    frame = monster_menu_frame(['ダッシュプレス', 'メガトンプレス'],
                               ally=('ローラーキラー', 348), enemy=('クイーン', 70), cursor=0)
    s = parse(frame)
    assert s.kind == 'monster_menu'
    assert [line.y for line in s.menu_rows] == [176, 192, 208]
    assert s.menu_rows[2].words() == ['たまごに', 'もどれ']
    assert s.menu_cursor == 176
    assert [(e.name, e.hp, e.side, e.y) for e in s.egg_rows] == [
        ('ローラーキラー', 348, 'ally', 176), ('クイーン', 70, 'enemy', 200)]


def test_monster_menu_uses_the_first_skill_on_our_turn():
    frame = monster_menu_frame(['ダッシュプレス', 'メガトンプレス'],
                               ally=('ローラーキラー', 348), enemy=('クイーン', 70), cursor=0)
    state = {'policy': {'chapter': 1, 'battle': {'enemy': 'クイーン', 'ally': 'ゼウス',
                                                 'enemy_hp': 70, 'ally_hp': 8, 'step': '1-A3'}}}
    actions, state = decide(frame, state)
    assert state['screen_kind'] == 'monster_menu'
    assert actions[0]['buttons'] == ['a']
    choice = next(r for r in state['_records'] if r['decision'] == 'monster_menu_choice')
    assert choice['strategy_variant'] == 'monster_menu_skill1'
    independent = state['policy']['battle']['independent']
    assert independent['kind'] == 'monster_menu' and independent['action'] == 'skill1'
    assert independent['key'].startswith('monster_menu|1|クイーン|ゼウス|1-A3|')
    # Same menu: the choice is held instead of being re-decided.
    actions, state = decide(frame, state)
    assert actions[0]['buttons'] == ['a']
    assert not [r for r in state['_records'] if r['decision'] == 'monster_menu_choice']


def test_monster_menu_retreats_at_half_hp_or_less():
    frame = monster_menu_frame(['ダッシュプレス', 'メガトンプレス'],
                               ally=('ローラーキラー', 40), enemy=('クイーン', 100), cursor=0)
    actions, state = decide(frame, {'policy': {'chapter': 1}})
    choice = next(r for r in state['_records'] if r['decision'] == 'monster_menu_choice')
    assert choice['strategy_variant'] == 'monster_menu_retreat'
    # The knight sits on row 0, so the retreat row needs a step down.
    assert actions[0]['buttons'] == ['down']
    # HP exactly at half still retreats; above half does not.
    ok = monster_menu_frame(['ダッシュプレス'], ally=('ローラーキラー', 51),
                            enemy=('クイーン', 100), cursor=0)
    _, state = decide(ok, {'policy': {'chapter': 1}})
    choice = next(r for r in state['_records'] if r['decision'] == 'monster_menu_choice')
    assert choice['strategy_variant'] == 'monster_menu_skill1'


def test_monster_menu_prefers_the_effectful_second_skill_while_behind():
    # とけこむそー is how the live reader renders the table's とけこむぞー.
    frame = monster_menu_frame(['たべちゃうぞー', 'とけこむそー'],
                               ally=('カメレオンマン', 60), enemy=('ダークエルフ', 100), cursor=0)
    actions, state = decide(frame, {'policy': {'chapter': 1}})
    choice = next(r for r in state['_records'] if r['decision'] == 'monster_menu_choice')
    assert choice['strategy_variant'] == 'monster_menu_skill2'
    assert choice['observed_metric']['menu'][1] == 'とけこむそー'
    assert actions[0]['buttons'] == ['down']


def test_monster_menu_owner_matches_the_skill_table_despite_a_dropped_dakuten():
    frame = monster_menu_frame(['たべちゃうぞー', 'とけこむそー'],
                               ally=('どうし', 90), enemy=('カメレオンマン', 180), cursor=0)
    actions, state = decide(frame, {'policy': {'chapter': 1}})
    # The skills belong to the enemy summon: wait for its AI instead of acting.
    assert actions == []
    assert state['policy']['monster_menu_hold'] == 1
    wait = next(r for r in state['_records'] if r['decision'] == 'monster_menu_wait')
    assert wait['observed_metric']['enemy'] == 'カメレオンマン'
    assert not [r for r in state['_records'] if r['decision'] == 'monster_menu_choice']


def test_monster_menu_holds_for_the_enemy_ai_then_act_beyond_the_limit():
    frame = monster_menu_frame(['ダイナマイト', 'ミサイルくん'],
                               ally=('どうし', 90), enemy=('セクシーボンバー', 77), cursor=0)
    state = {'policy': {'chapter': 1}}
    records = []
    actions, state = decide(frame, state)
    records += state['_records']
    assert actions == []
    for _ in range(policy.MONSTER_MENU_HOLD_LIMIT - 1):
        actions, state = decide(frame, state)
        records += state['_records']
    assert actions == []
    assert len([r for r in records if r['decision'] == 'monster_menu_wait']) == 1
    # A menu stuck on the enemy side beyond the limit must not freeze the bot.
    actions, state = decide(frame, state)
    records += state['_records']
    assert actions[0]['buttons'] == ['a']
    stuck = [r for r in records if r['decision'] == 'monster_menu_wait']
    assert stuck[-1]['deviation_reason'] == 'enemy_menu_stuck'
    assert [r for r in records if r['decision'] == 'monster_menu_choice']


def test_monster_menu_without_a_knight_steps_rows_then_confirms():
    frame = monster_menu_frame(['ダッシュプレス', 'メガトンプレス'],
                               ally=('ローラーキラー', 40), enemy=('クイーン', 100), cursor=None)
    state = {'policy': {'chapter': 1}}
    for expected in ('down', 'down', 'a'):
        actions, state = decide(frame, state)
        assert actions[0]['buttons'] == [expected]
    assert state['policy']['monster_menu_choice'] == 'retreat'


def test_monster_menu_memory_is_cleared_once_the_menu_leaves():
    frame = monster_menu_frame(['ダッシュプレス'], ally=('ローラーキラー', 348),
                               enemy=('クイーン', 70), cursor=0)
    _, state = decide(frame, {'policy': {'chapter': 1}})
    assert state['policy']['monster_menu_choice'] == 'skill1'
    assert state['policy']['monster_panel']['enemy'] == 'クイーン'
    m = Canvas()
    m.text(48, 15, '1ねん 1のつき 100G')
    _, state = decide(m.frame(), state)
    for key in ('monster_menu_key', 'monster_menu_cursor', 'monster_menu_hold',
                'monster_menu_choice', 'monster_menu_choice_key', 'monster_panel'):
        assert key not in state['policy']


def test_monster_menu_situation_key_uses_the_summoned_panel():
    from docich import hanjuku_experience as exp_mod
    mem = {'chapter': 1, 'battle': {'enemy': 'クイーン', 'ally': 'ゼウス', 'step': '1-A3'},
           'monster_panel': {'ally': 'ローラーキラー', 'enemy': 'ヒュドラ',
                             'ally_hp': 348, 'enemy_hp': 135}}
    assert exp_mod.situation_key('monster_menu', mem) == (
        'monster_menu|1|クイーン|ゼウス|1-A3|ローラーキラー|ヒュドラ|ahead')
