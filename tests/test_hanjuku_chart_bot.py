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
from docich.hanjuku_screen import Screen

MASH = [action for _ in range(policy.POWER_TAPS)
        for action in (policy.pad('a', 3), {'type': 'wait', 'ms': 50})]
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


@pytest.mark.parametrize('size', [(299, 224), (897, 672)])
def test_small_aspect_preserved_capture_reads_name_and_confirms_only_correct_text(tmp_path, size):
    """Exercise capture normalization, glyphs, cursor and policy together."""
    from docich.hanjuku_pixels import read_png

    state = {}
    for typed, cell, button in [('ああ', 'あ', 'b'), ('', 'ど', 'a'),
                                ('ど', 'う', 'a'), ('どう', 'し', 'a'),
                                ('どうし', 'し', 'start')]:
        path = tmp_path / 'capture.png'
        path.write_bytes(name_screen(typed, cell=cell).resized(*size).png_bytes())
        normalized = read_png(path).resized()
        assert parse(normalized).kind == 'name_entry'
        actions, state = decide(normalized, state)
        assert actions == [policy.pad(button)]
        assert state['policy']['name']['typed'] == typed
        assert state['policy']['name']['done'] == (typed == 'どうし')


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
    assert full['soldiers'] == 41 - policy.WAGE_RESERVE
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
    assert policy.next_order({'chapter': 3, 'orders': {}}) is None


def test_month_plan_commentary_follows_the_actual_plan():
    key, text = hanjuku_commentary.compose({
        'decision': 'month_plan', 'month': '1-5', 'gold': 171,
        'deviation_reason': '所持金171Gがチャート想定214G未満',
        'plan': {'cards': [['クースカン', 1], ['イッテツーン', 6]], 'soldiers': 40}})
    assert '214' in text and 'クースカン1個' in text and '兵士を40人補充します' in text
    assert '優先順で買える分' in text
    # A chart-uncovered month must not claim it buys the boss kit (g460 1年6月:
    # the commentary said it would buy クースカン/ノリウツール with an empty plan).
    _, text = hanjuku_commentary.compose({
        'decision': 'month_plan', 'month': '1-6', 'gold': 185,
        'deviation_reason': 'chart_month_uncovered',
        'plan': {'cards': [], 'soldiers': 0}})
    assert 'チャートの購入予定がない' in text and '切り札の購入はありません' in text
    assert 'クースカン' not in text


def test_charted_boss_kit_waits_for_clash_then_chains_cards():
    mem = {'chapter': 1, 'attack': {'general': 'どうし', 'castle': 'けっかい', 'side': 'attack', 'step': '1-B1'}}
    from docich.hanjuku_screen import Battle, Screen
    screen = lambda hp: Screen(lines=[], hand=None, text='', battle=Battle('クイーン', hp, 'どうし', 90), kind='battle')
    assert policy.battle_step(screen(70), mem) == []          # first reading: wait for a stable one
    # Follow the actual chart: one measured clash, then the ordered kit.
    assert policy.battle_step(screen(70), mem)[0]['buttons'] == ['a']
    assert not mem['battle'].get('card_flow')
    assert policy.battle_step(screen(68), mem) == [policy.pad('b')]
    assert mem['battle']['card_flow']['card'] == 'クースカン'
    assert mem['battle'].get('strategy_variant') != 'clash_kit_open_timing'
    # The menu ask is retried instead of handing the turn back to melee, and
    # it stays bounded (g462 17:59:31: two idle frames dropped the first card).
    for _ in range(policy.CARD_MENU_OPEN_RETRIES):
        assert policy.battle_step(screen(70), mem) == [policy.pad('b')]
    assert policy.battle_step(screen(60), mem) == []
    assert mem['battle'].get('card_flow') is None
    assert mem['battle']['cards_unclassified'] == []
    assert not mem.get('kit_spent')
    # Opening never reached a selection: retry the first chart card, without
    # falsely unlocking the dependent ノリウツール.
    assert policy.battle_step(screen(30), mem) == [policy.pad('b')]
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


def test_decline_the_general_trade_prompt():
    # Owner rule 2026-09-28: 花いちもんめ (将軍トレード) is always declined —
    # the offers are almost always unfair. Question wording measured on
    # 倒転王国's monthly-event page.
    c = Canvas()
    c.text(24, 183, 'しょうぐんどうしのトレードだ!')
    c.text(184, 183, 'うむッ!')
    c.text(184, 199, 'いかんッ!')
    c.hand(162, 177)
    mem = {'_records': []}
    # The cursor starts on うむッ! (accept): step down to いかんッ! first.
    assert policy.yes_no_step(parse(c.frame()), mem)[0]['buttons'] == ['down']
    c2 = Canvas()
    c2.text(24, 183, 'しょうぐんどうしのトレードだ!')
    c2.text(184, 183, 'うむッ!')
    c2.text(184, 199, 'いかんッ!')
    c2.hand(162, 193)
    assert policy.yes_no_step(parse(c2.frame()), mem)[0]['buttons'] == ['a']
    rec = mem['_records'][-1]
    assert rec['strategy_variant'] == 'decline_general_trade'
    assert rec['choice'] == 'いかんッ!'
    # An unclassified prompt still proceeds by default.
    c3 = Canvas()
    c3.text(24, 183, 'たまごを つかいますか?')
    c3.text(184, 183, 'うむッ!')
    c3.text(184, 199, 'いかんッ!')
    c3.hand(162, 177)
    mem3 = {'_records': []}
    assert policy.yes_no_step(parse(c3.frame()), mem3)[0]['buttons'] == ['a']
    assert mem3['_records'][-1]['strategy_variant'] == 'unclassified_prompt'


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
    assert lost['observed_metric'] == {'captured': [], 'after': ['captured', 'ジョンリギ'],
                                      'source': 'ジョンリギ'}
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
    (91, 90, '体力で上回っています'),
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
    assert state['bot_version'] == 'hanjuku-chart-v124-camp-recall-evidence'
    assert '_records' not in state['policy']


def test_bot_version_marks_camp_recall_evidence_release():
    from docich.hanjuku_bot import BOT_VERSION
    assert BOT_VERSION == 'hanjuku-chart-v124-camp-recall-evidence'


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


def test_a_heal_first_monster_inflates_then_shouts():
    # バルーンフィンチ: ふくらむ to the tracked max, then シャウト while at max
    # (owner 2026-09-29); damage re-enables the heal.
    def run(ally_hp, state=None):
        frame = monster_menu_frame(['ふくらむ', 'シャウト'],
                                   ally=('バルーンフィンチ', ally_hp), enemy=('クミン', 40), cursor=0)
        state = state or {'policy': {'chapter': 1, 'battle': {
            'enemy': 'クミン', 'ally': 'バルーンフィンチ', 'enemy_hp': 40,
            'ally_hp': ally_hp, 'step': None}}}
        return decide(frame, state)
    _, state = run(30)                                             # hurt: inflate first
    assert state['policy']['monster_menu_choice'] == 'skill1'
    _, state = run(120, state)                                     # healed to the new max
    assert state['policy']['monster_menu_choice'] == 'skill2'      # shout at full
    _, state = run(30, state)                                      # damaged again
    assert state['policy']['monster_menu_choice'] == 'skill1'


def test_egg_summon_menu_falls_back_to_attack_when_the_egg_is_spent():
    # 2026-09-28 live incident: a general whose egg is already spent draws
    # たまごをつかう greyed out (dropped from OCR) on the enemy-summon menu.
    # Chasing a label that never appears held the corner forever (viewer
    # report: 持ってないタマゴを使おうとして止まっている). The bot must
    # answer with a plain attack instead of repeating a dead search.
    c = Canvas()
    c.text(176, 175, 'こうげき')
    c.text(176, 191, 'もうこうげき')
    c.text(176, 207, 'たまごをつかう')
    actions, state = decide(c.frame(), {'policy': {'chapter': 1}})
    assert state['screen_kind'] == 'egg_battle_menu'
    assert state['policy']['egg_action'] == 'use_egg'
    spent = Canvas()
    spent.text(176, 175, 'こうげき')
    spent.text(176, 191, 'もうこうげき')
    actions, state = decide(spent.frame(), state)
    assert state['screen_kind'] == 'egg_battle_menu'
    # With no cards left the bot tries the retreat once (owner 2026-09-29)
    # instead of attacking into a summon it cannot answer.
    assert actions[0]['buttons'] == ['b']
    assert state['policy']['egg_battle_row_dead'] is True
    # Three bounded return attempts; no immediate A after an unconfirmed B.
    for _ in range(2):
        actions, state = decide(spent.frame(), state)
        assert actions[0]['buttons'] == ['b']
    actions, state = decide(spent.frame(), state)
    assert actions[0]['buttons'] == ['a']
    actions, state = decide(spent.frame(), state)
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
    # User 2026-09-30: preserve the chart's actual HP gates.
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
        # The gate is not reached yet: push the melee toward it instead of
        # holding (an unbounded hold never reaches the HP gate).
        assert actions and actions[0]['buttons'] == ['a']
        assert not mem['battle'].get('card_flow')
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


def test_a_chained_hp_card_does_not_early_fire_for_the_egg():
    # 3-B1 プリンス: ゼンマイン (HP25) follows クースカン open/after_card, so it
    # keeps its gate, as do the other HP-gated chart tactics.
    from docich.hanjuku_screen import Battle, Screen
    mem = {'chapter': 3, 'attack': {'general': 'どうし', 'castle': None, 'side': 'attack', 'step': '3-B1'}}
    screen = Screen(lines=[], hand=None, text='', battle=Battle('プリンス', 100, 'どうし', 90), kind='battle')
    assert policy.battle_step(screen, mem) == []
    actions = policy.battle_step(screen, mem)
    assert actions and actions[0]['buttons'] == ['b']
    assert mem['battle']['card_flow']['card'] == 'クースカン'
    assert 'ゼンマイン' not in [r.get('card') for r in mem['_records']
                                if r.get('decision') == 'battle_card']


def test_missing_and_selected_cards_do_not_confirm_use_but_chain_the_charted_follow_up():
    mem, screen = _card_evidence_battle()
    cur = mem['battle']
    # The opening kit already claimed this fight's deviation slot (v82), so the
    # missing-card reporting is checked on a fight with no deviation of its own.
    cur.pop('strategy_variant', None); cur.pop('deviation_reason', None)
    cur['card_flow'] = {'card': 'クースカン', 'stage': 'list'}
    assert policy.card_list_step(_card_screen(['ノリウツール']), mem) == []
    assert policy.card_list_step(_card_screen(['ノリウツール']), mem)
    assert cur['cards_used'] == [] and cur['cards_missing'] == ['クースカン']
    missing = mem['_records'][-1]
    assert missing['decision'] == 'battle_card_missing' and missing['card'] == 'クースカン'
    assert missing['expected_metric'] == {'carried_card': 'クースカン'}
    assert missing['strategy_variant'] == 'chart_card_unavailable' and missing['deviation_reason']
    # No ノリウツール without a confirmed クースカン; the clash proceeds.
    assert cur.get('card_flow') is None
    assert policy.battle_step(screen, mem)[0]['buttons'] == ['a']
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
    # No calibrated use receipt exists, so the charted follow-up continues on
    # the selection record and is recorded as a deviation.
    assert policy.battle_step(screen, mem)[0]['buttons'] == ['b']
    assert cur['card_flow']['card'] == 'ノリウツール'
    assert '未校正' in mem['_records'][-1]['reason']


@pytest.mark.parametrize('statement', ['クースカンをつかった', 'クースカンをしようした'])
def test_uncalibrated_card_text_never_confirms_use_but_chains_the_charted_follow_up(statement):
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
    # The charted follow-up continues on the selection record (deviation);
    # consumption itself stays unclassified.
    assert policy.battle_step(screen, mem)[0]['buttons'] == ['b']
    assert cur['card_flow']['card'] == 'ノリウツール'
    assert policy.summary(mem)['cards_used'] is None
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
    # The opening stage keeps asking the panel for the menu it never got
    # (g462 17:59:29); every other stage is already past the menu ask and
    # gives up as soon as the second observation carries no receipt.
    for _ in range(policy.CARD_MENU_OPEN_RETRIES + 1 if stage == 'menu' else 2):
        policy.battle_step(screen, mem)
    assert cur['card_flow'] is None
    assert cur['cards_used'] == []
    assert cur['cards_unclassified'] == []
    assert not mem.get('kit_spent')
    assert mem['_records'][-1]['decision'] == 'battle_card_open_unclassified'

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
    assert mem['chapter'] == 2 and mem['variant'] == 'chart'
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
           'expect_menu': True, '_records': [], 'select_used': True}
    screen = Screen(lines=[], hand=None, text='', kind='map',
                    cursor=(200, 160))  # bracket on water, not a roof cell
    actions = policy.map_step(screen, mem, frame)
    # g358: dropping the estimate froze the camera on open water. The
    # cursor keeps steering (toward land) but never confirms with A.
    assert actions and all(a['buttons'][0] in ('up', 'down', 'left', 'right') for a in actions)
    kinds = [r['decision'] for r in mem['_records']]
    assert 'localize' in kinds and 'nav_reset' in kinds
    assert mem.get('cursor') and not mem.get('anchor') and mem.get('nav_search') is True
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


def test_failed_menu_on_open_sea_steers_inland_until_roofs_reanchor(monkeypatch):
    """g358: after nav_reset on open water every map frame held input (140 s)."""
    monkeypatch.setattr(policy, 'Y_JUMP_OFFSET', {})     # exercises roof navigation, not Y jumps
    from docich.hanjuku_pixels import Frame
    from docich.hanjuku_screen import Screen
    frame = Frame(256, 224, bytes(256 * 224 * 3))
    monkeypatch.setattr(policy, 'castle_roofs', lambda *_a, **_k: [])
    home = policy.chart.castles(1)['ほんじょう']
    mem = {'chapter': 1, 'variant': 'chart', 'active': '1-A1',
           'orders': {'1-A1': 'pending'}, 'picked': [],
           'cursor': [home[0] - 3, home[1]], 'expect_menu': True, '_records': [],
           'select_used': True}
    centroid = policy._search_goal(mem)
    distance = lambda: abs(mem['cursor'][0] - centroid[0]) + abs(mem['cursor'][1] - centroid[1])
    screen = lambda x, y: Screen(lines=[], hand=None, text='', kind='map', cursor=(x, y))

    start = distance()
    actions = policy.map_step(screen(232, 200), mem, frame)   # pinned at the EDGE cell
    assert {a['buttons'][0] for a in actions} == {'left', 'up'}
    for x, y in ((204, 172), (176, 144), (148, 116)):
        mem['_records'] = []
        actions = policy.map_step(screen(x, y), mem, frame)
        assert actions and not any(a['buttons'] == ['a'] for a in actions)
        assert 'situation_held' not in [r['decision'] for r in mem['_records']]
    assert distance() < start - 3 * 28
    assert mem['uncertain'] is True and mem['menu_miss'] == 1

    # Land in view: two roofs agree on the camera, so the estimate re-anchors,
    # search ends and confirming a cell is allowed again.
    kikan, nakyu = policy.chart.castles(1)['キカンドン'], policy.chart.castles(1)['ナキューメラ']
    cam = (500, 600)
    roofs = [{'target': (kikan[0] - cam[0], kikan[1] - cam[1]), 'clipped': False},
             {'target': (nakyu[0] - cam[0], nakyu[1] - cam[1]), 'clipped': False}]
    monkeypatch.setattr(policy, 'castle_roofs', lambda *_a, **_k: roofs)
    mem['_records'] = []
    actions = policy.map_step(screen(120, 100), mem, frame)
    assert mem['cursor'] == [cam[0] + 120, cam[1] + 100]
    assert mem['uncertain'] is False and 'nav_search' not in mem
    assert mem['menu_miss'] == 0
    # Normal navigation resumes toward the order's source (ほんじょう).
    assert {a['buttons'][0] for a in actions} == {'right', 'down'}


def test_leaving_the_map_discards_pending_motion():
    state = {'policy': {'chapter': 1, 'cursor': [500, 500], 'nav_search': True,
                        'nav_last': {'screen': [100, 100], 'expected': [28, 0], 'search': True},
                        'orders': {}, 'picked': []}}
    _, state = decide(Canvas().frame(), state)      # an off-map (unreadable) screen
    assert state['policy']['uncertain'] is True
    assert 'nav_last' not in state['policy']


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
    assert 'ココット' in fb and '攻めます' in fb and '見送' not in fb
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


def test_monster_skill_named_like_an_okunote_choice_is_still_our_monster_turn():
    """g401 21:58: かみつく/おどす/たまごに もどれ was read as okunote and stalled 300 s."""
    frame = monster_menu_frame(['かみつく', 'おどす'], menu_left=False,
                               ally=('コマイス', 158), enemy=('カメレオンマン', 75), cursor=0)
    screen = parse(frame)
    assert screen.menu_cursor is not None
    assert screen.kind == 'monster_menu'
    state = {'policy': {'chapter': 1, 'battle': {'enemy': 'カメレオンマン', 'ally': 'コマイス',
                                                 'enemy_hp': 75, 'ally_hp': 158, 'step': None}}}
    actions, state = decide(frame, state)
    assert state['screen_kind'] == 'monster_menu' and actions[0]['buttons'] == ['a']


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


def test_monster_menu_skips_a_full_hp_heal_first_skill_for_damage():
    # g407: バルーンフィンチ chose ふくらむ 71 times at 9999HP while the enemy
    # sat at 12HP, so the battle never ended. While ahead a heal-first skill
    # is skipped in favor of the damaging second skill.
    frame = monster_menu_frame(['ふくらむ', 'シャウト'],
                               ally=('バルーンフィンチ', 9999), enemy=('ダークエルフ', 12), cursor=0)
    actions, state = decide(frame, {'policy': {'chapter': 1}})
    choice = next(r for r in state['_records'] if r['decision'] == 'monster_menu_choice')
    assert choice['strategy_variant'] == 'monster_menu_skill2'
    assert choice['observed_metric']['action'] == 'skill2'
    assert choice['observed_metric']['menu'] == ['ふくらむ', 'シャウト', 'たまごにもどれ']
    assert '回復技' in choice['reason']
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


def _discharge_canvas(month=8, gold=None):
    c = Canvas()
    # A negative balance prints as ー1G, which the digit-only header regex
    # cannot match: gold=None here stands for the still-in-debt screen.
    c.text(16, 15, f'2ねん{month}のつき{gold or ""}')
    c.text(24, 47, 'ミント')
    c.text(24, 63, 'ゼウス')
    c.text(16, 191, 'どのしょうぐんをかいこに')
    c.hand(2, 41)
    return c


def test_forced_discharge_list_confirms_default_cursor_instead_of_b():
    """g358 16:49-17:41: debt forced「どのしょうぐんをかいこに?」, read as shop → 2700 B."""
    state = {'policy': {'chapter': 1, 'orders': {}, 'picked': []}}
    actions, state = decide(_discharge_canvas().frame(), state)
    assert state['screen_kind'] == 'discharge_menu'
    assert actions == [{'type': 'pad', 'buttons': ['a'], 'hold_ms': 100}]
    record = state['_records'][-1]
    assert record['decision'] == 'discharge_general' and record['general'] == 'ミント'
    assert record['observed_metric']['month'] == '2-8'


def test_forced_discharge_without_month_header_initializes_state_and_confirms():
    """A missed month OCR must not leave the discharge counters uninitialized."""
    canvas = Canvas()
    canvas.text(24, 47, 'ミント')
    canvas.text(24, 63, 'ゼウス')
    canvas.text(16, 191, 'どのしょうぐんをかいこに')
    canvas.hand(2, 41)
    state = {'policy': {'chapter': 1, 'orders': {}, 'picked': []}}

    actions, state = decide(canvas.frame(), state)

    assert state['screen_kind'] == 'discharge_menu'
    assert actions == [{'type': 'pad', 'buttons': ['a'], 'hold_ms': 100}]
    assert state['policy']['discharge'] == {'key': None, 'presses': 1, 'exits': 0}
    record = state['_records'][-1]
    assert record['decision'] == 'discharge_general'
    assert record['observed_metric']['month'] is None


def test_paid_up_discharge_list_leaves_with_b_instead_of_discharging():
    """g407 04:xx: the balance was already +32G yet the bot dismissed six more
    generals (DISCHARGE_LIMIT) and held. Once the header gold parses (not in
    debt) the list must be left with B, never with another dismissal."""
    state = {'policy': {'chapter': 1, 'orders': {}, 'picked': []}}
    for _ in range(policy.DISCHARGE_EXIT_LIMIT):
        actions, state = decide(_discharge_canvas(gold='32G').frame(), state)
        assert actions and actions[0]['buttons'] == ['b']
    exit_record = next(r for r in state['_records'] if r['decision'] == 'discharge_exit')
    assert exit_record['observed_metric']['gold'] == 32
    assert not [r for r in state['_records'] if r['decision'] == 'discharge_general']
    actions, state = decide(_discharge_canvas(gold='32G').frame(), state)
    assert actions == []
    assert 'situation_held' in [r['decision'] for r in state['_records']]
    # A still-negative month discharges again; the exit counter is per month.
    actions, state = decide(_discharge_canvas(month=9).frame(), state)
    assert actions and actions[0]['buttons'] == ['a']


def test_forced_discharge_holds_after_monthly_limit_and_resets_next_month():
    state = {'policy': {'chapter': 1, 'orders': {}, 'picked': []}}
    for _ in range(policy.DISCHARGE_LIMIT):
        actions, state = decide(_discharge_canvas().frame(), state)
        assert actions and actions[0]['buttons'] == ['a']
    actions, state = decide(_discharge_canvas().frame(), state)
    assert actions == []
    assert 'situation_held' in [r['decision'] for r in state['_records']]
    actions, state = decide(_discharge_canvas().frame(), state)
    assert actions == [] and state['_records'] == []          # held once, not per frame
    actions, state = decide(_discharge_canvas(month=9).frame(), state)
    assert actions and actions[0]['buttons'] == ['a']
# ---------------------------------------------------------------- 月一: 卵の回復・将軍募集
MONTH_GRID = {'しょうにん': (48, 47), 'しょうぐんぼしゅう': (144, 47),
              'へいしほじゅう': (48, 63), 'しょうぐんかいこ': (144, 63),
              'ちくじょう': (48, 79), 'たまごのかいふく': (144, 79),
              'メインメニュー': (48, 95), 'も〜おしまい!': (144, 95)}


def month_canvas(gold, on='しょうにん', month=7):
    c = Canvas()
    c.text(48, 15, f'1ねん {month}のつき {gold}G')
    for label, (x, y) in MONTH_GRID.items():
        c.text(x, y, label)
    x, y = MONTH_GRID[on]
    c.hand(x - 22, y - 6)
    return c.frame()


def sortie_canvas(general, uses):
    c = Canvas()
    c.text(16, 31, general)
    c.text(80, 31, 'しょうぐん')
    c.text(136, 31, 'きりふだセレクト')
    c.text(16, 111, 'たまご')
    c.text(48, 111, 'エラベルエッグ')
    c.text(112, 111, str(uses))
    return parse(c.frame())


def test_sortie_screens_record_each_generals_remaining_egg_uses():
    mem = {'chapter': 1}
    screen = sortie_canvas('どうし', 4)
    assert screen.kind == 'card_select'
    policy.observe_events(screen, mem)
    policy.observe_events(sortie_canvas('ココット', 0), mem)
    policy.observe_events(sortie_canvas('ココット', 0), mem)       # unchanged: one record
    assert mem['egg_uses'] == {'どうし': 4, 'ココット': 0}
    assert [r['decision'] for r in mem['_records']].count('egg_uses_seen') == 2


def test_an_empty_egg_reserves_its_recovery_ahead_of_soldiers():
    # Owner 2026-09-29: eggs go first only with this month's army counted at 50+.
    mem = {'chapter': 1, 'egg_uses': {'どうし': 4, 'ココット': 0}, 'soldiers_seen': 60, 'soldiers_seen_key': '1-7'}
    shop = policy._plan(mem, {'year': 1, 'month': 7, 'gold': 130})
    assert (shop['soldiers'] == 80 - policy.WAGE_RESERVE and shop['egg'] == 'pending'
            and shop['recruit'] == 'deferred_roster')
    # Army not counted this month: soldiers first, the egg from what is left.
    shop = policy._plan({'chapter': 1, 'egg_uses': {'ココット': 0}}, {'year': 1, 'month': 7, 'gold': 130})
    assert shop['soldiers'] == 99 and shop['egg'] == 'check' and shop['reserve'] == 0
    # No empty egg: soldiers keep the whole gold as before.
    shop = policy._plan({'chapter': 1, 'egg_uses': {'どうし': 4}}, {'year': 1, 'month': 7, 'gold': 130})
    assert shop['soldiers'] == 99 and shop['egg'] is None and shop['recruit'] == 'deferred_roster'
    # The owner now prioritizes affordable egg recovery even before a later chart purchase.
    mem = {'chapter': 3, 'egg_uses': {'どうし': 0}, 'soldiers_seen': 60, 'soldiers_seen_key': '1-7'}
    shop = policy._plan(mem, {'year': 1, 'month': 7, 'gold': 475})
    assert shop['egg'] == 'pending' and shop['reserve'] == 50 and shop['recruit'] == 'deferred_roster'


def test_month_menu_recovers_egg_then_defers_recruit_until_actual_roster_is_known():
    home = {'year': 1, 'month': 7, 'gold': 300}
    mem = {'chapter': 1, 'egg_uses': {'ココット': 0}, 'orders': {}, 'picked': []}
    shop = policy._plan(mem, home)
    shop['soldiers_done'] = True                                   # refill already bought 99
    assert shop['soldiers'] == 99
    state = {'policy': mem}
    actions, state = decide(month_canvas(201), state)
    assert actions[0]['buttons'] == ['down']                       # toward たまごのかいふく
    actions, state = decide(month_canvas(201, on='たまごのかいふく'), state)
    assert actions[0]['buttons'] == ['a'] and state['policy']['month_sub']['kind'] == 'egg'
    # Unmeasured inner screens: A, and うむッ! on a confirmation.
    c = Canvas((0, 0, 0))
    c.text(24, 183, 'たまごを かいふく しますぞ')
    actions, state = decide(c.frame(), state)
    assert actions[0]['buttons'] == ['a']
    c = Canvas()
    c.text(32, 47, 'ぜんかいふく')
    c.text(32, 71, 'ココット')
    c.hand(10, 41)
    actions, state = decide(c.frame(), state)
    assert actions == [policy.pad('a')]
    c = Canvas()
    c.text(72, 15, '1ねん 7のつき 201G')
    c.text(24, 183, '1こで50Gになりまんな')
    c.text(184, 183, 'うむッ!')
    c.text(184, 199, 'いかんッ!')
    c.hand(162, 193)                                               # on いかんッ!
    actions, state = decide(c.frame(), state)
    assert actions[0]['buttons'] == ['up']
    for y in range(193,206):
        for x in range(162,180): c.put(x,y,GREEN)
    c.hand(162,177)
    actions, state = decide(c.frame(), state)
    assert actions == [policy.pad('a')]
    # Back on the month menu with 50G fewer: done, then recruit (99 soldiers, 151G left).
    actions, state = decide(month_canvas(151, on='たまごのかいふく'), state)
    mem = state['policy']
    assert mem['shop']['egg'] == 'done' and mem['egg_uses'] == {} and 'month_sub' not in mem
    assert [r for r in state['_records'] if r['decision'] == 'egg_recover'][0]['deviation_reason'] is None
    assert mem['shop']['recruit'] == 'deferred_roster'
    assert not mem.get('month_sub')
    assert not [r for r in state['_records'] if r['decision'] == 'month_sub_open' and r.get('choice') == 'しょうぐんぼしゅう']


def test_unknown_roster_defers_recruit_even_with_sufficient_cash_and_soldiers():
    for soldiers, gold in ((99, 49), (99, 79), (60, 200)):
        mem = {'chapter': 1, 'shop': {'key': '1-7', 'items': [], 'soldiers': soldiers,
                                      'merchant_done': True, 'soldiers_done': True,
                                      'egg': None, 'recruit': 'check', 'gold_start': 300}}
        actions = policy.month_step(parse(month_canvas(gold)), mem)
        assert mem['shop']['recruit'] == 'deferred_roster'
        assert [r['decision'] for r in mem['_records']][:1] == ['recruit_roster_unknown']
        assert actions[0]['buttons'] != ['a'] or mem['month_exit']


def test_unmeasured_month_sub_screen_leaves_with_b_after_the_limit():
    mem = {'chapter': 1, 'month_sub': {'kind': 'recruit', 'gold_before': 151, 'presses': 0, 'key': '1-7'},
           'orders': {}, 'picked': []}
    state = {'policy': mem}
    c = Canvas((0, 0, 0))
    c.text(24, 183, 'オーディション')
    for _ in range(policy.MONTH_SUB_LIMIT):
        actions, state = decide(c.frame(), state)
        assert actions[0]['buttons'] == ['a']
    actions, state = decide(c.frame(), state)
    assert actions[0]['buttons'] == ['b']
    assert 'month_sub_abort' in [r['decision'] for r in state['_records']]


def test_month_sub_is_dropped_when_the_map_returns():
    from docich.hanjuku_screen import Screen
    mem = {'month_sub': {'kind': 'egg', 'gold_before': 60, 'presses': 2, 'key': '1-7'}}
    screen = Screen(lines=[], hand=None, text='', kind='map', cursor=(100, 100))
    assert policy.month_sub_step(screen, mem) is None
    assert 'month_sub' not in mem and mem['_records'][-1]['decision'] == 'month_sub_lost'


def test_tutorial_sword_practice_mashes_a_to_push_instead_of_idling():
    """g389 16:30: the sword practice (だいじん 90 vs どうし 90) was lost 0-15 with no input.

    Isolated libretro replay of the same state: idle lost 0-15, A mash won
    1..19-0 at every cadence tried (per-frame and 4..10 taps per decision).
    """
    from docich.hanjuku_screen import Battle, Screen
    mem = {}                                 # before chapter 1: no chart, no orders
    screen = Screen(lines=[], hand=None, text='', battle=Battle('だいじん', 90, 'どうし', 90), kind='battle')
    assert policy.battle_step(screen, mem) == []           # first reading: wait for a stable one
    assert policy.battle_step(screen, mem) == MASH
    assert [r['decision'] for r in mem['_records']] == ['battle_start', 'battle_power', 'battle_melee']
    assert policy.battle_step(screen, mem) == MASH         # every decision keeps pushing
    assert [r['decision'] for r in mem['_records']].count('battle_power') == 1
    over = Screen(lines=[], hand=None, text='', battle=Battle('だいじん', 0, 'どうし', 20), kind='battle')
    assert policy.battle_step(over, mem) == []              # the panel has ended: stop


def _menu_without_egg_row(cursor=None):
    c = Canvas((0, 0, 0))
    c.text(176, 192, 'きりふだ')
    c.text(176, 208, 'たいきゃく')
    if cursor is not None:
        for y in range(168 + 16 * cursor, 180 + 16 * cursor):
            for x in range(152, 164):
                c.put(x, y, (230, 105, 74))
    return c.frame()


def test_menu_with_greyed_egg_row_is_the_battle_menu_and_reaches_the_card():
    """g389 16:48: たまごをつかう greyed out → read as text → A on the dead row for minutes."""
    from docich.hanjuku_screen import parse
    assert parse(_menu_without_egg_row()).kind == 'battle_menu'
    mem = {'chapter': 1, 'battle': {'enemy': 'ガルバンゾー', 'ally': 'どうし', 'cards_used': [],
                                    'card_flow': {'card': 'フットバース', 'stage': 'menu', 'note': ''}}}
    actions, state = decide(_menu_without_egg_row(cursor=0), {'policy': mem})
    assert actions[0]['buttons'] == ['down']                # off the dead egg row to きりふだ
    actions, state = decide(_menu_without_egg_row(cursor=1), state)
    assert actions[0]['buttons'] == ['a']


def test_independent_egg_choice_backs_out_when_the_egg_row_is_dead():
    mem = {'chapter': 1, 'battle': {'enemy': 'ミント', 'ally': 'どうし', 'cards_used': [],
                                    'enemy_hp': 60, 'ally_hp': 20},
           'indep_menu': True, 'indep_menu_action': 'use_egg'}
    actions, state = decide(_menu_without_egg_row(), {'policy': mem})
    assert actions[0]['buttons'] == ['b']
    assert 'situation_held' in [r['decision'] for r in state['_records']]


def test_open_sea_search_spirals_around_the_centroid_and_records_each_leg(monkeypatch):
    """g389 16:41: at the centroid (by dead reckoning) the old nudge only bobbed up/down."""
    monkeypatch.setattr(policy, 'Y_JUMP_OFFSET', {})     # exercises roof navigation, not Y jumps
    from docich.hanjuku_pixels import Frame
    from docich.hanjuku_screen import Screen
    frame = Frame(256, 224, bytes(256 * 224 * 3))
    monkeypatch.setattr(policy, 'castle_roofs', lambda *_a, **_k: [])
    mem = {'chapter': 1, 'variant': 'chart', 'active': '1-A1', 'orders': {'1-A1': 'pending'},
           'picked': [], 'uncertain': True, 'nav_search': True, 'menu_miss': 1, '_records': [],
           'select_used': True}
    centroid = policy._search_goal(mem)
    mem['cursor'] = list(centroid)
    screen = Screen(lines=[], hand=None, text='', kind='map', cursor=(120, 120))
    actions = policy.map_step(screen, mem, frame)
    assert mem['nav_search_leg'] == 1
    assert [a['buttons'][0] for a in actions] == ['up']            # first leg: 120 px north
    leg = [r for r in mem['_records'] if r['decision'] == 'nav_search_leg'][0]
    assert leg['observed_metric']['roofs'] == 0 and leg['observed_metric']['screen_cursor'] == [120, 120]
    assert policy._search_goal(mem) == (centroid[0], centroid[1] - policy.SEARCH_RING_PX)
    mem['nav_search_leg'] = 5                                       # second ring, north again
    assert policy._search_goal(mem) == (centroid[0], centroid[1] - 2 * policy.SEARCH_RING_PX)
    assert not any(a['buttons'] == ['a'] for a in actions)


@pytest.mark.parametrize('uses', [0, 1, 2, 3])
def test_partial_egg_reserves_full_recovery_cost(uses):
    mem = {'chapter': 1, 'egg_uses': {'ココット': uses}, 'soldiers_seen': 60, 'soldiers_seen_key': '1-7'}
    shop = policy._plan(mem, {'year': 1, 'month': 7, 'gold': 130})
    assert (shop['reserve'] == 50 and shop['soldiers'] == 80 - policy.WAGE_RESERVE
            and shop['egg'] == 'pending')


def test_all_depleted_eggs_are_budgeted_and_invalid_or_full_counts_are_ignored():
    mem = {'chapter': 1, 'egg_uses': {'どうし': 3, 'ココット': 1, 'ヴィーナス': 4,
           'bad': True, 'unknown': None, 'negative': -1, 'boosted': 5, 'one-shot': 1},
           'egg_types': {'one-shot': 'いっぱつエッグ'}, 'soldiers_seen': 60, 'soldiers_seen_key': '1-7'}
    shop = policy._plan(mem, {'year': 1, 'month': 7, 'gold': 180})
    assert shop['reserve'] == 100 and shop['soldiers'] == 80 - policy.WAGE_RESERVE
    assert policy._egg_recovery_targets(mem) == ['どうし', 'ココット']
    poor = {'chapter': 1, 'egg_uses': {'どうし': 3, 'ココット': 1}, 'soldiers_seen': 60, 'soldiers_seen_key': '1-7'}
    shop = policy._plan(poor, {'year': 1, 'month': 7, 'gold': 99})
    assert shop['egg'] == 'pending' and shop['soldiers'] == 0


def test_soldiers_never_spend_the_wage_reserve():
    # g358/g407: spending the month down to 0G made the month-boundary wage
    # payment negative and the game forced dismissals (owner 2026-09-28:
    # そもそも将軍解雇はしないで欲しい). The reserve stays back.
    mem = {'chapter': 1}
    shop = policy._plan(mem, {'year': 2, 'month': 4, 'gold': 40})
    assert shop['soldiers'] == 40 - policy.WAGE_RESERVE
    shop = policy._plan({'chapter': 1}, {'year': 2, 'month': 4, 'gold': policy.WAGE_RESERVE})
    assert shop['soldiers'] == 0


def test_attempted_summon_rechecks_stale_full_sortie_count():
    mem = {'battle': {'ally': 'どうし'}, 'egg_uses': {'どうし': 4}, 'soldiers_seen': 60, 'soldiers_seen_key': '1-7'}
    policy._egg_recheck(mem)
    policy._egg_recheck(mem)
    assert mem['egg_recheck'] == ['どうし']
    assert mem['egg_uses'] == {'どうし': 4}  # never invent a consumed quantity
    assert policy._extras_reserve(mem, {'year': 1, 'month': 7, 'gold': 50})[0] == 50


def test_full_recovery_moves_from_an_individual_egg_to_full_option():
    mem = {'month_sub': {'kind': 'egg', 'gold_before': 119, 'presses': 0}}
    c = Canvas()
    c.text(32,47,'ぜんかいふく'); c.text(32,71,'ココット'); c.text(104,71,'3')
    c.hand(10,65)
    assert policy.month_sub_step(parse(c.frame()), mem) == [policy.pad('up')]
    assert not mem['month_sub'].get('full_selected')


@pytest.mark.parametrize('gold,quote,selected,expected', [
    (100,'2こで100Gになりまんな',True,'a'),
    (99,'2こで100Gになりまんな',True,'b'),
    (100,'2こで100Gになりまんな',False,'b'),
    (100,'2こで50Gになりまんな',True,'b'),
    (100,'よろしいでっか?',True,'b'),
])
def test_full_recovery_confirms_only_observed_affordable_total(gold, quote, selected, expected):
    c = Canvas()
    c.text(72,15,f'1ねん 7のつき {gold}G')
    c.text(24,183,quote); c.text(184,183,'うむッ!'); c.text(184,199,'いかんッ!')
    c.hand(162,177)
    mem = {'month_sub': {'kind':'egg','gold_before':gold,'presses':0,'full_selected':selected}}
    assert policy.month_sub_step(parse(c.frame()), mem) == [policy.pad(expected)]
    assert mem['month_sub'].get('stage') == ('recovering' if expected == 'a' else None)


def test_paid_recovery_ritual_has_bounded_longer_wait_without_inferring_stock():
    mem = {'month_sub': {'kind':'egg','gold_before':100,'presses':8,
                        'full_selected':True,'quoted_cost':100,'stage':'recovering'},
           'egg_uses':{'どうし':1,'ココット':3}}
    c=Canvas((0,0,0)); c.text(24,183,'ほんだららった')
    assert policy.month_sub_step(parse(c.frame()), mem) == [policy.pad('a')]
    assert mem['egg_uses'] == {'どうし':1,'ココット':3}
    mem['month_sub']['presses']=policy.EGG_RITUAL_LIMIT
    assert policy.month_sub_step(parse(c.frame()), mem) == [policy.pad('b')]


def test_recovery_only_month_preserves_later_chart_budget(monkeypatch):
    monkeypatch.setattr(policy, '_charted_purchase_ahead', lambda *args: True)
    monkeypatch.setattr(policy.chart, 'purchase_for', lambda *args: None)
    mem={'chapter':3,'egg_uses':{'どうし':2}, 'soldiers_seen': 60, 'soldiers_seen_key': '1-7'}
    shop=policy._plan(mem,{'year':1,'month':7,'gold':300})
    assert shop['reserve']==50 and shop['egg']=='pending'
    assert shop['items']==[] and shop['soldiers']==0 and shop['recruit']=='deferred_roster'


def test_no_recovery_needed_message_is_closed_without_paying_or_reopening():
    mem={'chapter':1,'egg_uses':{'ココット':3},'egg_recheck':['ココット']}
    policy._plan(mem,{'year':1,'month':7,'gold':130})
    mem['shop']['soldiers_done']=True
    mem['shop']['egg']='opened'
    mem['month_sub']={'kind':'egg','gold_before':50,'presses':0}
    screen=parse(month_canvas(50,on='たまごのかいふく'))
    screen.text+='わがぐんにはいまおはらいのひつようなたまごはありませんぞ!'
    assert policy.month_step(screen,mem)==[policy.pad('a')]
    assert mem['shop']['egg']=='not_needed' and 'month_sub' not in mem
    assert 'egg_recheck' not in mem


def test_unreadable_egg_row_does_not_crash_or_invent_an_egg_type():
    from docich.hanjuku_screen import Screen
    mem = {}
    policy.observe_events(Screen([],None,'',kind='card_select'),mem)
    assert mem.get('egg_uses') == {} and not mem.get('egg_types')


def test_new_sortie_reading_clears_the_old_summon_recheck():
    mem={'egg_recheck':['どうし'],'egg_uses':{'どうし':4}}
    policy.observe_events(sortie_canvas('どうし',3),mem)
    assert mem['egg_recheck']==[] and policy._egg_recovery_targets(mem)==['どうし']


def test_egg_ritual_fade_holds_input_before_returning_to_month_menu():
    state = {'policy': {'chapter': 1, 'month_sub': {
        'kind': 'egg', 'gold_before': 50, 'presses': 20,
        'full_selected': True, 'quoted_cost': 50, 'stage': 'recovering'}}}
    actions, state = decide(Canvas((0, 0, 0)).frame(), state)
    assert actions == []
    assert state['policy']['month_sub']['stage'] == 'recovering'
    actions, state = decide(month_canvas(0, on='しょうにん'), state)
    assert 'month_sub' not in state['policy']
    assert actions != [policy.pad('a')]  # do not open the merchant


def recruit_overlay_screen():
    from docich.hanjuku_font import TextLine
    screen = parse(month_canvas(158, on='しょうぐんぼしゅう'))
    screen.hand = None
    words = ['ども!しょうぐんえんごかいのものです。', 'しょうぐんのぼしゅうでございますね?']
    screen.lines.extend(TextLine(183+16*i, tuple((8+8*j,ch) for j,ch in enumerate(w)))
                        for i,w in enumerate(words))
    screen.text += ''.join(words)
    return screen


def recruit_overlay_memory():
    return {'chapter':1,'shop':{'key':'1-7','items':[],'soldiers':99,'soldiers_done':True,
            'merchant_done':False,'egg':'done','recruit':'unverified','closed':True,'gold_start':307}}


def test_recruit_intro_restores_lost_tracking_only_from_measured_dialogue():
    mem=recruit_overlay_memory()
    assert policy.month_step(recruit_overlay_screen(),mem)==[policy.pad('a')]
    assert mem['month_sub']['kind']=='recruit' and mem['month_sub']['gold_before']==158
    assert mem['shop']['recruit']=='opened'
    assert mem['_records'][0]['decision']=='month_sub_resumed'


def test_the_untracked_chikujou_overlay_is_closed_with_b(monkeypatch):
    # g462 18:06:10-18:11:11: legacy A presses on the ちくじょう exit screen
    # changed nothing for 300 s and the run watchdog ended the corner.
    from docich import hanjuku_screen
    sc = Screen(lines=[], hand=None, kind='text', header=None,
                text='アルマムーン3これいじょうのぞうちくはできませんぞ!!どのしろをぞうちくなさいますか?')
    monkeypatch.setattr(hanjuku_screen, 'parse', lambda *a, **k: sc)
    actions, state = decide(month_canvas(75), {'policy': {'chapter': 1, '_records': []}})
    assert actions == [policy.pad('b')]
    assert state['_records'][0]['decision'] == 'chikujou_leftover'


def test_month_background_does_not_end_active_recruit_dialogue(monkeypatch):
    from docich import hanjuku_screen
    sc=recruit_overlay_screen();mem=recruit_overlay_memory()
    mem['shop']['recruit']='opened'
    mem['month_sub']={'kind':'recruit','gold_before':158,'presses':0,'key':'1-7'}
    monkeypatch.setattr(hanjuku_screen,'parse',lambda *a,**k:sc)
    actions,state=decide(month_canvas(158),{'policy':mem})
    assert actions==[policy.pad('a')]
    assert state['policy']['month_sub']['presses']==1
    assert state['policy']['shop']['recruit']=='opened'
    assert not any(r['decision']=='recruit' for r in state['_records'])
    sc.hand=(160,177,177,189)  # a bottom dialogue cursor is also not the menu
    assert not policy.month_menu_ready(sc)
    sc.hand=(160,41,177,53)
    assert not policy.month_menu_ready(sc)  # g496 retained this background hand
    sc.lines=[line for line in sc.lines if line.y < 175]
    assert policy.month_menu_ready(sc)


def test_recruit_overlay_recovery_keeps_army_and_budget_guards():
    for gold,soldiers in ((49,99),(158,60)):
        sc=recruit_overlay_screen();sc.header['gold']=gold
        mem=recruit_overlay_memory();mem['shop']['soldiers']=soldiers
        assert policy.month_step(sc,mem)==[]
        assert not mem.get('month_sub')


def test_unknown_month_dialogue_is_not_a_recruit_recovery():
    sc=recruit_overlay_screen();sc.lines=sc.lines[:-2]
    mem=recruit_overlay_memory()
    assert policy.month_step(sc,mem)==[]
    assert not mem.get('month_sub')


def paid_recruit_screen(gold=108, name='ラズベリー'):
    from docich.hanjuku_screen import Screen
    from docich.hanjuku_font import TextLine
    words=['「わたしのなは'+name+'ともうします。','HPー45Pたまごーなし',
           'せんとうー8Pないせいー2P','ちんぎんー7G']
    lines=[TextLine(151+16*i,tuple((8+8*j,c) for j,c in enumerate(w))) for i,w in enumerate(words)]
    return Screen(lines=lines,hand=None,text=''.join(words),kind='text',header={'gold':gold})


def test_paid_candidate_recovers_previous_abort_once_with_new_finite_bound():
    mem={'month_sub':{'kind':'recruit','gold_before':158,'presses':70,'aborted':True}}
    sc=paid_recruit_screen()
    assert policy.month_sub_step(sc,mem)==[policy.pad('a')]
    assert mem['month_sub']['presses']==1 and not mem['month_sub'].get('aborted')
    for _ in range(policy.RECRUIT_CANDIDATE_LIMIT-1):
        assert policy.month_sub_step(sc,mem)==[policy.pad('a')]
    assert policy.month_sub_step(sc,mem)==[policy.pad('b')]
    assert policy.month_sub_step(sc,mem)==[policy.pad('b')]  # same biography cannot reset the bound


def test_unpaid_or_unreadable_candidate_never_resets_aborted_flow():
    from docich.hanjuku_font import UNKNOWN
    for sc in (paid_recruit_screen(gold=158),paid_recruit_screen(name='ラズ'+UNKNOWN+'ベリー')):
        mem={'month_sub':{'kind':'recruit','gold_before':158,'presses':70,'aborted':True}}
        assert policy.month_sub_step(sc,mem)==[policy.pad('b')]
        assert not mem['month_sub'].get('paid_candidates')


def test_commentary_separates_plan_unknown_departure_and_observation():
    rec = {'decision': 'order_start', 'chart_step': 'I:test:1', 'general': 'ココット',
           'source': 'ジョンリギ', 'target': 'スペンソニア', 'cards': []}
    assert '計画です' in hanjuku_commentary.compose(rec)[1]
    rec.update(decision='order_launched_unconfirmed', target=None, planned_target='スペンソニア')
    assert 'order_launched_unconfirmed' in hanjuku_commentary.SPOKEN
    text = hanjuku_commentary.compose(rec)[1]
    assert '成否と行き先を確認' in text and 'スペンソニア' not in text
    _, text = hanjuku_commentary.compose({'decision': 'battle_start', 'ally': 'どうし',
        'enemy': 'ミント', 'ally_hp': 90, 'enemy_hp': 32})
    assert '温存' not in text and '90対32' in text


def _camp_frame():
    """A map frame with our camping tent (probe-measured sprite)."""
    c = Canvas()
    for yy in range(72, 81):
        for xx in range(30, 39):
            c.put(xx, yy, (238, 198, 65) if yy < 76 else (238, 113, 57))
    for yy in (77, 78):                       # light-blue base band
        for xx in range(31, 39):
            c.put(xx, yy, (131, 198, 222))
    for p in ((34, 68), (35, 68), (34, 69)):
        c.put(p[0], p[1], (255, 0, 0))
    return c.frame()


def _clipped_camp_frame():
    """A tent touching the top edge: flag off-screen (g421 live frame)."""
    c = Canvas()
    for yy in range(0, 4):
        for xx in range(30, 39):
            c.put(xx, yy, (238, 198, 65))
    for yy in range(4, 8):
        for xx in range(30, 39):
            c.put(xx, yy, (238, 113, 57))
    for xx in range(31, 39):
        c.put(xx, 8, (131, 198, 222))
    return c.frame()


def _own_roof_frame():
    """A map frame with one own castle roof (selecting cell (165, 117))."""
    c = Canvas()
    for yy in range(105, 126):
        for xx in range(150, 181):
            c.put(xx, yy, (230, 56, 90))
    return c.frame()


def test_own_camps_finds_the_tent_and_skips_roof_reds():
    from docich.hanjuku_screen import own_camps
    camps = own_camps(_camp_frame())
    assert [c['target'] for c in camps] == [(26, 66)]
    assert camps[0]['clipped'] is False
    assert own_camps(_own_roof_frame()) == []


def test_own_camps_accepts_a_tent_clipped_by_the_top_edge():
    from docich.hanjuku_screen import own_camps
    camps = own_camps(_clipped_camp_frame())
    assert len(camps) == 1
    assert camps[0]['clipped'] is True
    # selecting cell above the screen: the servo must scroll the camera up
    assert camps[0]['target'] == (24, -6)


def test_own_camps_rejects_a_full_tent_without_a_visible_flag():
    from docich.hanjuku_screen import own_camps
    c = Canvas()
    for yy in range(20, 24):
        for xx in range(30, 39):
            c.put(xx, yy, (238, 198, 65))
    for yy in range(24, 28):
        for xx in range(30, 39):
            c.put(xx, yy, (238, 113, 57))
    for xx in range(31, 39):
        c.put(xx, 28, (131, 198, 222))
    assert own_camps(c.frame()) == []


def test_camp_recall_scrolls_the_camera_for_a_tent_clipped_at_the_top():
    frame = _clipped_camp_frame()
    mem = {'chapter': 1, '_records': []}
    screen = Screen(lines=[], hand=None, text='', kind='map', cursor=(100, 100))
    actions = policy.camp_recall_step(screen, mem, frame)
    assert actions == [policy.pad('up', 8)]
    assert mem['recall']['stage'] == 'to_camp'


def camp_menu(selected):
    from docich.hanjuku_font import TextLine
    rows = ['いどう', 'ステータス', 'キャンプ', 'きかん']
    return Screen([TextLine(39+16*i, tuple((80+8*j,ch) for j,ch in enumerate(word)))
                   for i,word in enumerate(rows)], (58,33+16*selected,75,45+16*selected),
                  ''.join(rows), kind='text')


def test_camp_recall_walks_cursor_menu_and_own_castle(monkeypatch):
    frame = _camp_frame()
    mem = {'chapter': 1, '_records': []}
    screen = Screen(lines=[], hand=None, text='', kind='map', cursor=(80, 90))
    assert policy.camp_recall_step(screen, mem, frame) == [policy.pad('left', 8)]
    assert mem['recall']['stage'] == 'to_camp'
    assert [r['decision'] for r in mem['_records']] == ['camp_found']

    screen = Screen(lines=[], hand=None, text='', kind='map', cursor=(27, 67))
    mem['recall']['steps'] = 89                      # the long scroll may eat the budget
    assert policy.camp_recall_step(screen, mem, frame) == [policy.pad('a')]
    assert mem['recall']['stage'] == 'menu'
    assert mem['recall']['steps'] == 0               # each stage restarts the budget
    assert [r['decision'] for r in mem['_records']][-1] == 'camp_enter'

    for i in range(3):
        assert policy.camp_recall_step(camp_menu(i), mem, frame) == [policy.pad('down')]
    assert policy.camp_recall_step(camp_menu(3), mem, frame) == [policy.pad('a')]
    assert mem['recall']['stage'] == 'dest'
    assert mem['recall']['steps'] == 0               # destination gets a full budget too

    monkeypatch.setattr(policy, 'castle_roofs',
                        lambda frame, exclude=None: [{'kind': 'own', 'target': (165, 117), 'clipped': False}])
    far = Screen(lines=[], hand=None, text='', kind='map_target', marker=(160, 110))
    assert policy.camp_recall_step(far, mem, frame) == [policy.pad('down', 6)]
    near = Screen(lines=[], hand=None, text='', kind='map_target', marker=(163, 115))
    assert policy.camp_recall_step(near, mem, frame) == [policy.pad('a')]
    assert mem['recall']['stage'] == 'await_dispatch'
    assert [r['decision'] for r in mem['_records']][-1] == 'camp_recall_requested'
    assert not any(r['decision'] == 'camp_recall' for r in mem['_records'])


def test_camp_recall_cancels_when_no_own_castle_is_visible():
    frame = _camp_frame()
    mem = {'chapter': 1, '_records': [], 'recall': {'stage': 'dest', 'target': [26, 66], 'steps': 1}}
    marker_only = Screen(lines=[], hand=None, text='', kind='map_target', marker=(100, 100))
    assert policy.camp_recall_step(marker_only, mem, frame) == [policy.pad('b')]
    assert 'recall' not in mem
    assert [r['decision'] for r in mem['_records']][-1] == 'camp_recall_skipped'


def test_map_step_recalls_a_visible_camp_before_any_order():
    # g421: the recall never started because it waited for a chartless idle
    # map. A visible camp must win over any order state.
    frame = _camp_frame()
    mem = {'chapter': 1, 'variant': 'chart', 'picked': [], '_records': [],
           'tick': 500, 'world_map_tick': 400, 'active': 'I:test:1'}
    screen = Screen(lines=[], hand=None, text='', kind='map', cursor=(80, 90))
    assert policy.map_step(screen, mem, frame) == [policy.pad('left', 8)]
    assert mem['recall']['stage'] == 'to_camp'
    assert [r['decision'] for r in mem['_records']][-1] == 'camp_found'


def test_map_step_does_not_start_a_camp_recall_while_a_y_jump_view_is_opening():
    frame = _camp_frame()
    mem = {'chapter': 1, 'variant': 'chart', 'picked': [], '_records': [],
           'tick': 500, 'world_map_tick': 400,
           'y_jump': {'goal': 'ジョンリギ', 'mode': 'map', 'step': 'I:test:1',
                      'moves': 0, 'wait': 0, 'tick': 500}}
    screen = Screen(lines=[], hand=None, text='', kind='map', cursor=(80, 90))
    policy.map_step(screen, mem, frame)
    assert 'recall' not in mem
    assert not [r for r in mem['_records'] if r['decision'] == 'camp_found']


def test_map_step_refocuses_on_the_hero_periodically():
    # Owner rule (2026-09-28): periodically SELECT to the hero's position.
    # The first map frame only seeds the interval; the press comes later.
    frame = _own_roof_frame()
    mem = {'chapter': 1, 'variant': 'chart', 'picked': [], '_records': [],
           'tick': 500, 'world_map_tick': 500}
    screen = Screen(lines=[], hand=None, text='', kind='map', cursor=(80, 90))
    actions = policy.map_step(screen, mem, frame)
    assert actions != [policy.pad('select')]
    assert mem['select_focus_tick'] == 500
    mem['tick'] = 500 + policy.SELECT_FOCUS_INTERVAL
    mem['_records'] = []
    actions = policy.map_step(screen, mem, frame)
    assert actions == [policy.pad('select')]
    assert 'select_focus' in [r['decision'] for r in mem['_records']]
    assert mem['nav_last'] is None
    assert mem['uncertain'] is True


def test_soldier_count_is_read_from_the_army_total_line():
    from docich.hanjuku_screen import Screen as S
    mem = {'_records': []}
    screen = S(lines=[], hand=None, kind='text',
               text='げんざい わがぐんの へいしすうは 60めいです')
    policy.observe_events(screen, mem)
    assert mem['soldiers_seen'] == 60
    assert [r['decision'] for r in mem['_records']] == ['soldiers_seen']
    policy.observe_events(screen, mem)          # unchanged: no duplicate record
    assert [r['decision'] for r in mem['_records']].count('soldiers_seen') == 1


def test_a_depleted_hero_egg_reserves_before_soldiers():
    # g458 15:45: the hero's egg was spent, the months kept buying soldiers
    # and the recovery was skipped until the fatal defense (no retreat there).
    mem = {'chapter': 1, 'egg_uses': {'どうし': 1}}
    reserve, _ = policy._extras_reserve(mem, {'year': 1, 'month': 8, 'gold': 100})
    assert reserve == 50
    shop = policy._plan(dict(mem), {'year': 1, 'month': 8, 'gold': 100})
    assert shop['reserve'] == 50 and shop['egg'] == 'pending' and shop['soldiers'] == 20
    # Another general's egg without the hero and without the count: unchanged.
    other = {'chapter': 1, 'egg_uses': {'ココット': 1}}
    assert policy._extras_reserve(other, {'year': 1, 'month': 8, 'gold': 100})[0] == 0


def test_egg_recovery_holds_the_gold_over_more_soldiers_when_army_is_big():
    # Owner rule 2026-09-28: 50+ soldiers → egg recovery wins; no soldiers.
    mem = {'chapter': 1, 'egg_uses': {'ココット': 0}, 'soldiers_seen': 60, 'soldiers_seen_key': '2-4'}
    # gold is short of the 50G cost but above the wage reserve: hold it all
    # for the egg and buy no soldiers (today the gap (30, 50) was eaten).
    reserve, _ = policy._extras_reserve(mem, {'year': 2, 'month': 4, 'gold': 45})
    assert reserve == 45 - policy.WAGE_RESERVE
    shop = policy._plan(dict(mem), {'year': 2, 'month': 4, 'gold': 45})
    assert shop['soldiers'] == 0 and shop['egg'] == 'pending'
    # a weak army (<50) keeps today's behaviour: no hold below the cost
    mem2 = {'chapter': 1, 'egg_uses': {'ココット': 0}, 'soldiers_seen': 40}
    assert policy._extras_reserve(mem2, {'year': 2, 'month': 4, 'gold': 45})[0] == 0
    # full cost still reserves normally
    assert policy._extras_reserve(dict(mem), {'year': 2, 'month': 4, 'gold': 130})[0] == 50


def test_summarize_recap_counts_only_the_runs_own_story(tmp_path):
    from docich.hanjuku_commentary import summarize_recap
    rows = [
        {'event': 'decision', 'decision': 'month_seen', 'month': '2-7'},
        {'event': 'decision', 'decision': 'order_launched'},
        {'event': 'decision', 'decision': 'order_launched'},
        {'event': 'decision', 'decision': 'order_launched_unconfirmed'},
        {'event': 'decision', 'decision': 'discharge_general'},
        {'event': 'decision', 'decision': 'castle_owned_observed',
         'resulting_event': 'captured:ジョンリギ'},
        {'event': 'decision', 'decision': 'world_map_owners',
         'resulting_event': ['captured:ゴーメン', 'lost:ココット']},
        {'event': 'decision', 'chapter': 2},
        {'event': 'action_plan', 'chapter': 5},          # not a decision: ignored
    ]
    (tmp_path / 'hanjuku_decisions.jsonl').write_text(
        ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
    key, text = summarize_recap(tmp_path, {'battles_finished': 42})
    assert key == 'game_over_recap'
    assert text == ('ゲームオーバー。第2章まで進み、2城を獲得、2回出撃と42回戦闘を重ね、'
                    '2年7月まで戦いました（将軍の解雇1回）。今回の挑戦はここまでです。')
    assert len(text) <= 120
    _, bare = summarize_recap(tmp_path, {})
    assert bare.startswith('ゲームオーバー。')


def test_narration_delivers_only_the_game_over_recap_at_terminal(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from docich.game_switch import atomic_write_json
    identity = {'game': 'hanjuku-hero', 'runtime_id': 'g9-test', 'generation': 9,
                'lease_id': 'lease-9'}
    g = SimpleNamespace(state_dir=tmp_path)
    atomic_write_json(tmp_path / 'hanjuku_run.json',
                      {**identity, 'terminal_reason': 'game_over'})
    monkeypatch.setattr('docich.agent.fence.read_canonical', lambda _: {'active': identity})
    sent, now = [], time.time()
    write_candidates(tmp_path, [
        {'seq': 1, 'at': now, 'key': 'battle:x', 'text': 'たたかいの実況', **identity},
        {'seq': 2, 'at': now, 'key': 'game_over_recap',
         'text': 'ゲームオーバー。第1章までの記録でした。', 'terminal_recap': True, **identity},
    ])
    hanjuku_narration.consider(g, Game(), tmp_path, terminal=True, now=now,
                               enqueue=lambda *a, **k: sent.append((a, k)))
    log_path = tmp_path / 'hanjuku_narration.jsonl'
    for _ in range(100):
        try:
            log = [json.loads(x) for x in log_path.read_text().splitlines()]
        except FileNotFoundError:
            log = []
        if len(log) >= 2 and len(sent) >= 1:
            break
        time.sleep(0.02)
    statuses = {i['seq']: i['status'] for i in log}
    assert statuses[1] == 'skipped:terminal'          # the ordinary line stays silent
    assert statuses[2] == 'enqueued'
    assert len(sent) == 1
    args, kwargs = sent[0]
    assert kwargs.get('runtime_fence') is None        # teardown-safe delivery


def test_narration_stays_silent_at_terminal_without_a_recap(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from docich.game_switch import atomic_write_json
    identity = {'game': 'hanjuku-hero', 'runtime_id': 'g10-test', 'generation': 10,
                'lease_id': 'lease-10'}
    g = SimpleNamespace(state_dir=tmp_path)
    atomic_write_json(tmp_path / 'hanjuku_run.json',
                      {**identity, 'terminal_reason': 'game_over'})
    monkeypatch.setattr('docich.agent.fence.read_canonical', lambda _: {'active': identity})
    sent, now = [], time.time()
    write_candidates(tmp_path, [{'seq': 1, 'at': now, 'key': 'a', 'text': '通常の実況', **identity}])
    hanjuku_narration.consider(g, Game(), tmp_path, terminal=True, now=now,
                               enqueue=lambda *a, **k: sent.append(a))
    log_path = tmp_path / 'hanjuku_narration.jsonl'
    for _ in range(100):
        if log_path.exists():
            break
        time.sleep(0.02)
    log = [json.loads(x) for x in log_path.read_text().splitlines()]
    assert log and log[0]['status'] == 'skipped:terminal'
    assert not sent


@pytest.mark.parametrize('selected,enemy_hp,expected_card', [(True, 34, 'ノリウツール'),
                                                         (False, 34, 'クースカン'),
                                                         (True, 68, 'クースカン')])
def test_boss_selected_card_hp_drop_chains_without_idle_frames(selected, enemy_hp, expected_card):
    from docich.hanjuku_screen import Battle, Screen
    mem = {'chapter': 1, 'attack': {'general': 'どうし', 'castle': 'けっかい',
                                  'side': 'attack', 'step': '1-B1'}}
    def screen(hp):
        return Screen(lines=[], hand=None, text='', kind='battle',
                      battle=Battle('クイーン', hp, 'どうし', 90))
    policy.battle_step(screen(70), mem)
    policy.battle_step(screen(70), mem)
    policy.battle_step(screen(68), mem)
    cur = mem['battle']
    cur['card_flow']['stage'] = 'announce'
    cur['cards_selected'] = ['クースカン'] if selected else []
    out = policy.battle_step(screen(enemy_hp), mem)
    assert cur['card_flow']['card'] == expected_card
    assert cur['cards_used'] == []
    if expected_card == 'ノリウツール':
        assert out == [policy.pad('b')]
        assert cur['cards_unclassified'] == ['クースカン']
        assert cur['card_consumption_complete'] is False
    else:
        assert out == []


def test_month_gift_request_with_three_prices_is_not_a_merchant_list():
    from docich.hanjuku_screen import parse
    c = Canvas((20, 20, 20))
    c.text(16, 176, 'だからなんかかって。')
    for y, label in [(176, 'エンドマン50G'), (192, 'みずまき100G'), (208, 'スカラーベ200G')]:
        c.text(160, y, label)
    screen = parse(c.frame())
    assert screen.kind == 'gift_request'
    mem = {'chapter': 1, '_records': []}
    screen.hand = (138, 169, 156, 181)
    assert policy.gift_step(screen, mem) == [policy.pad('a')]
    assert mem['_records'][-1]['decision'] == 'gift'
    assert mem['_records'][-1]['price'] == 50


@pytest.mark.parametrize('stage', ['menu', 'down', 'list', 'announce'])
def test_unselected_card_return_never_spends_kit_or_unlocks_after_card(stage):
    mem, screen = _card_evidence_battle()
    cur = mem['battle']
    tid = cur['card_flow']['tactic_id']
    cur['tactics_done'] = [tid]
    cur['card_flow'] = {'card': 'クースカン', 'stage': stage, 'tactic_id': tid}
    policy._card_use_unclassified(mem, cur, '白兵へ復帰')
    assert cur['cards_selected'] == []
    assert cur['cards_unclassified'] == []
    assert not mem.get('kit_spent')
    assert cur['card_flow'] is None
    assert cur['tactics_done'] == []
    assert mem['_records'][-1]['decision'] == 'battle_card_open_unclassified'
    assert mem['_records'][-1]['observed_metric']['retry_allowed'] is True
    out = policy.battle_step(screen, mem)
    assert out == [policy.pad('b')]
    assert cur['card_flow']['card'] == 'クースカン'  # not dependent ノリウツール


def test_unselected_chart_open_retry_is_bounded_and_keeps_the_carried_card():
    mem, screen = _card_evidence_battle()
    cur = mem['battle']
    tid = cur['card_flow']['tactic_id']
    cur['tactics_done'] = [tid]
    for attempt in (1, 2):
        cur['card_flow'] = {'card': 'クースカン', 'stage': 'menu', 'tactic_id': tid}
        policy._card_use_unclassified(mem, cur, '表示待ち上限')
        assert cur['card_open_failures'][tid] == attempt
        if attempt == 1:
            assert policy.battle_step(screen, mem) == [policy.pad('b')]
    assert cur['tactics_done'] == [tid]
    assert not mem.get('kit_spent')
    assert cur['cards_unclassified'] == []
    assert mem['_records'][-1]['observed_metric']['retry_allowed'] is False
    for _ in range(5):
        policy.battle_step(screen, mem)
        assert cur.get('card_flow') is None


def test_selected_unconfirmed_card_still_spends_and_chains_once():
    mem, screen = _card_evidence_battle()
    cur = mem['battle']
    tid = cur['card_flow']['tactic_id']
    cur['tactics_done'] = [tid]
    cur['card_flow'] = {'card': 'クースカン', 'stage': 'list'}
    policy.card_list_step(_card_screen(['クースカン']), mem)
    policy._card_use_unclassified(mem, cur, '告知未確認')
    assert mem['kit_spent']['1-B1'] == ['クースカン']
    assert cur['cards_unclassified'] == ['クースカン']
    assert cur['tactics_done'] == [tid]
    assert policy.battle_step(screen, mem) == [policy.pad('b')]
    assert cur['card_flow']['card'] == 'ノリウツール'



def test_recruit_intro_with_upper_background_hand_resumes_only_guarded_flow():
    sc = recruit_overlay_screen()
    sc.hand = (115, 41, 132, 54)
    mem = recruit_overlay_memory()
    assert policy.month_step(sc, mem) == [policy.pad('a')]
    assert mem['month_sub']['kind'] == 'recruit'
    assert not policy.month_menu_ready(sc)
    for gold, soldiers in ((49, 99), (158, 60)):
        sc.header['gold'] = gold
        guarded = recruit_overlay_memory()
        guarded['shop']['soldiers'] = soldiers
        assert policy.month_step(sc, guarded) == []
        assert not guarded.get('month_sub')


@pytest.mark.parametrize('hand', [None, (115, 41, 132, 54)])
def test_measured_recruit_goodbye_closes_without_navigating_background(hand):
    from docich.hanjuku_font import TextLine
    sc = recruit_overlay_screen()
    sc.lines = [line for line in sc.lines if line.y < 175]
    sc.lines.extend(TextLine(183+16*i, tuple((8+8*j,ch) for j,ch in enumerate(w)))
                    for i,w in enumerate(['それではまたのきかいに。', 'ごようのさいはいつでもおまかせを。']))
    sc.hand = hand
    mem = recruit_overlay_memory()
    assert not policy.month_menu_ready(sc)
    assert policy.month_step(sc, mem) == [policy.pad('a')]
    assert mem['_records'][-1]['decision'] == 'month_recruit_goodbye'


def test_powerless_allied_monster_returns_before_wasting_attack_turns():
    for hp in (120, 46, 11):
        frame = monster_menu_frame(['なぐれっ!', 'かきむしれ!'],
                                   ally=('ウゴカザル', hp), enemy=('ピスタチオ', 49), cursor=0)
        actions, state = decide(frame, {'policy': {'chapter': 2}})
        choice = next(r for r in state['_records'] if r['decision'] == 'monster_menu_choice')
        assert choice['observed_metric']['owner'] == 'ally'
        assert choice['observed_metric']['action'] == 'retreat'
        assert '攻撃性能がない' in choice['reason']
        assert actions[0]['buttons'] == ['down']


def test_powerless_enemy_monster_is_left_to_enemy_ai():
    frame = monster_menu_frame(['なぐれっ!', 'かきむしれ!'],
                               ally=('ピスタチオ', 49), enemy=('ウゴカザル', 120), cursor=0)
    actions, state = decide(frame, {'policy': {'chapter': 2}})
    assert actions == []
    assert state['_records'][0]['decision'] == 'monster_menu_wait'
    assert state['_records'][0]['observed_metric']['owner'] == 'enemy'


def test_powerless_return_navigates_to_measured_return_row():
    state = {'policy': {'chapter': 2}}
    for cursor, expected in ((0, 'down'), (1, 'down'), (2, 'a')):
        frame = monster_menu_frame(['なぐれっ!', 'かきむしれ!'],
                                   ally=('ウゴカザル', 120), enemy=('ピスタチオ', 49), cursor=cursor)
        actions, state = decide(frame, state)
        assert actions[0]['buttons'] == [expected]


def _short_recruit_memory():
    return {'chapter': 1, 'month': '1-7', 'tick': 100,
            'garrison': {'ほんじょう': ['どうし', 'ゼウス']}, 'orders': {}, 'picked': [],
            'recruit_roster': {'chapter': 1, 'month': '1-7', 'tick': 95,
                'names': ['どうし', 'ゼウス'], 'wages': {'どうし': 0, 'ゼウス': 4}, 'complete': True},
            'castle_income': {'アルマムーン': {'chapter': 1, 'month': '1-7', 'tick': 95, 'income': 30}}}


@pytest.mark.parametrize('gold,soldiers,reserved', [(154, 74, 50), (101, 21, 50), (79, 0, 49), (46, 0, 16)])
def test_short_generals_reserve_fee_before_soldiers(gold, soldiers, reserved):
    mem = _short_recruit_memory()
    shop = policy._plan(mem, {'year': 1, 'month': 7, 'gold': gold})
    assert shop['soldiers'] == soldiers
    assert shop['recruit_priority'] and shop['recruit_reserve'] == reserved
    assert shop['recruit'] == 'check'
    assert policy._recruit_shortage(mem)['total_roster'] == 2


def test_short_generals_recruit_without_the_old_99_refill_gate():
    mem = _short_recruit_memory()
    actions = policy.month_step(parse(month_canvas(101, on='しょうぐんぼしゅう')), mem)
    assert actions == [policy.pad('a')]
    assert mem['shop']['soldiers'] == 21 and not mem['shop']['soldiers_done']
    assert mem['month_sub']['kind'] == 'recruit'
    # Stale background menu cannot launch soldiers while recruitment is opening.
    assert policy.month_step(parse(month_canvas(101, on='しょうぐんぼしゅう')), mem) == []
    # Payment is observed before the remaining soldier refill opens.
    actions = policy.month_step(parse(month_canvas(51, on='しょうぐんぼしゅう')), mem)
    assert mem['shop']['recruit'] == 'done' and mem['shop']['recruit_reserve'] == 0
    assert mem['shop']['soldiers'] == 21
    assert 'month_sub' not in mem


def test_short_generals_save_insufficient_fee_without_building_it_away():
    mem = _short_recruit_memory()
    policy.month_step(parse(month_canvas(79, on='しょうぐんぼしゅう')), mem)
    assert mem['shop']['soldiers'] == 0
    assert mem['shop']['recruit'] == 'skipped'
    assert mem['shop']['chikujou'] == 'skipped'
    assert 'month_sub' not in mem


def test_same_month_cached_shop_is_migrated_only_once():
    mem = _short_recruit_memory()
    mem['shop'] = {'key': '1-7', 'items': [], 'soldiers': 71, 'gold_start': 101,
                   'soldiers_done': False, 'merchant_done': True, 'egg': None,
                   'recruit': 'skipped', 'chikujou': 'check'}
    policy.month_step(parse(month_canvas(101)), mem)
    policy.month_step(parse(month_canvas(101)), mem)
    assert mem['shop']['soldiers'] == 21
    assert sum(r['decision'] == 'recruit_priority_plan' for r in mem['_records']) == 1


def test_garrisons_and_sortie_records_do_not_replace_fresh_global_roster():
    mem = _short_recruit_memory()
    mem['captured'] = ['キカンドン']
    mem['garrison']['キカンドン'] = ['ココット', 'ヴィーナス', 'クミン']
    mem['sorties'] = {'x': {'general': 'シャルドネ', 'status': 'en_route', 'tick': 99}}
    assert policy._recruit_shortage(mem)['total_roster'] == 2
    mem.pop('recruit_roster')
    assert policy._recruit_shortage(mem) is None


def test_short_generals_keep_hero_egg_and_wage_reserves():
    mem = _short_recruit_memory(); mem['egg_uses'] = {'どうし': 0}
    shop = policy._plan(mem, {'year': 1, 'month': 7, 'gold': 130})
    assert shop['reserve'] == 50 and shop['recruit_reserve'] == 50 and shop['soldiers'] == 0
    assert policy.month_step(parse(month_canvas(130, on='しょうぐんぼしゅう')), mem) == [policy.pad('a')]
    mem = _short_recruit_memory(); mem['egg_uses'] = {'どうし': 0}
    policy.month_step(parse(month_canvas(100, on='しょうぐんぼしゅう')), mem)
    assert mem['shop']['recruit'] == 'check'  # defer until the real egg cost is known
    assert mem['shop']['soldiers'] == 0


def test_short_generals_can_reserve_even_before_a_future_chart_purchase():
    mem = _short_recruit_memory(); mem['chapter'] = 3; mem['month'] = '1-6'
    mem['recruit_roster'].update(chapter=3, month='1-6')
    mem['castle_income']['アルマムーン'].update(chapter=3, month='1-6')
    shop = policy._plan(mem, {'year': 1, 'month': 6, 'gold': 79})
    assert shop['recruit_priority'] and shop['recruit_reserve'] == 49
    assert shop['soldiers'] == 0


def test_recruit_join_is_verified_at_the_heros_observed_castle():
    mem = {'chapter': 1, 'garrison': {'ほんじょう': [], 'ジョンリギ': ['どうし']},
           'month_sub': {'kind': 'recruit', 'gold_before': 100, 'key': '1-7',
                         'candidate_names': ['ラズベリー'], 'generals_before': ['どうし']}}
    policy._finish_month_sub(parse(month_canvas(50)), mem, {'recruit': 'opened'})
    assert 'ジョンリギ' not in mem['garrison']
    c = Canvas(); c.text(64, 31, 'しゅつげき'); c.text(64, 47, 'ステータス')
    c.text(144, 39, 'どうし'); c.text(144, 55, 'ラズベリー'); c.hand(122, 33)
    policy._observe_garrison(parse(c.frame()), mem,
                             {'step': 'x', 'source': 'ジョンリギ', 'target': 'キカンドン',
                              'general': 'どうし', 'cards': [], 'note': 'verification'})
    assert any(r['decision'] == 'recruit_join_observed' and r['castle'] == 'ジョンリギ' for r in mem['_records'])
    assert 'recruit_verification' not in mem


def test_cached_paid_recruit_does_not_earmark_another_fee():
    mem = _short_recruit_memory()
    mem['shop'] = {'key': '1-7', 'items': [], 'soldiers': 21, 'gold_start': 101,
                   'soldiers_done': False, 'merchant_done': True, 'egg': None,
                   'recruit': 'done', 'chikujou': 'check'}
    policy.month_step(parse(month_canvas(51)), mem)
    assert mem['shop'].get('recruit_reserve', 0) == 0 and mem['shop']['soldiers'] == 21
    assert mem.get('month_sub', {}).get('kind') != 'recruit'


def test_overestimated_egg_cost_defers_recruit_then_uses_real_balance():
    # g498 month 1-6: reserve 150, quote/pay 50; 166G remained but v95
    # had already permanently skipped recruiting before recovery.
    mem = _short_recruit_memory()
    shop = {'key': '1-6', 'recruit_priority': True, 'recruit': 'check',
            'egg': 'pending', 'reserve': 150, 'recruit_reserve': 36,
            'soldiers': 0, 'chikujou': 'check'}
    screen = parse(month_canvas(216, on='しょうぐんぼしゅう'))
    assert policy._month_extra(screen, mem, shop, recruit_only=True) is None
    assert shop['recruit'] == 'check'
    assert mem['_records'][-1]['decision'] == 'recruit_deferred_egg'
    # Paid full recovery is measured rather than replacing the reserve by
    # an invented quote; then the same month's real 166G affords recruitment.
    shop['egg'] = 'opened'
    mem['month_sub'] = {'kind': 'egg', 'gold_before': 216, 'quoted_cost': 50,
                        'full_selected': True, 'left_menu': True, 'key': '1-6'}
    screen = parse(month_canvas(166, on='しょうぐんぼしゅう'))
    assert policy._finish_month_sub(screen, mem, shop)
    assert policy._month_extra(screen, mem, shop) == [policy.pad('a')]
    assert mem['month_sub']['kind'] == 'recruit'


@pytest.mark.parametrize('enemy', ['ダークエルフ', 'だいまおう'])
def test_excalibur_uses_four_hits_against_known_enemy_monsters(enemy):
    frame = monster_menu_frame(['エクスカリバる', 'マサムネる'],
                               ally=('エクスカリバー', 282), enemy=(enemy, 240), cursor=0)
    actions, state = decide(frame, {'policy': {'chapter': 1}})
    assert actions[0]['buttons'] == ['down']
    assert state['policy']['monster_menu_choice'] == 'skill2'
    # The old cached first skill must also migrate on a same-HP hotload.
    state['policy']['monster_menu_choice'] = 'skill1'
    actions, state = decide(frame, state)
    assert state['policy']['monster_menu_choice'] == 'skill2'


def test_excalibur_keeps_first_skill_against_a_general_and_waits_on_enemy_menu():
    frame = monster_menu_frame(['エクスカリバる', 'マサムネる'],
                               ally=('エクスカリバー', 299), enemy=('カシュー', 39), cursor=0)
    actions, state = decide(frame, {'policy': {'chapter': 1}})
    assert actions[0]['buttons'] == ['a']
    frame = monster_menu_frame(['エクスカリバる', 'マサムネる'],
                               ally=('どうし', 90), enemy=('エクスカリバー', 299), cursor=0)
    actions, state = decide(frame, {'policy': {'chapter': 1}})
    assert actions == [] and 'monster_menu_choice' not in state['policy']


def test_excalibur_low_hp_still_returns_instead_of_forced_four_hits():
    frame = monster_menu_frame(['エクスカリバる', 'マサムネる'],
                               ally=('エクスカリバー', 80), enemy=('ダークエルフ', 240), cursor=0)
    _, state = decide(frame, {'policy': {'chapter': 1}})
    assert state['policy']['monster_menu_choice'] == 'retreat'


def test_deferred_recruit_does_not_replace_the_egg_payment_tracker():
    mem = _short_recruit_memory()
    mem['shop'] = {'key': '1-7', 'gold_start': 216, 'items': [], 'merchant_done': True,
                   'soldiers': 0, 'soldiers_done': True, 'reserve': 150,
                   'recruit_priority': True, 'recruit_budget_version': 1,
                   'recruit_measured_budget': True, 'recruit_reserve': 36,
                   'recruit': 'check', 'egg': 'opened', 'chikujou': 'check'}
    mem['month_sub'] = {'kind': 'egg', 'gold_before': 216, 'quoted_cost': 50,
                        'full_selected': True, 'left_menu': False, 'key': '1-7'}
    assert policy.month_step(parse(month_canvas(216, on='しょうぐんぼしゅう')), mem) == []
    assert mem['month_sub']['kind'] == 'egg'
    assert policy.month_step(parse(month_canvas(166, on='しょうぐんぼしゅう')), mem) == [policy.pad('a')]
    assert mem['shop']['egg'] == 'done' and mem['month_sub']['kind'] == 'recruit'
    assert any(r['decision'] == 'egg_recover' for r in mem['_records'])


def test_paid_recruit_receipt_survives_later_deduction_and_tracks_actual_join_name():
    mem = {'chapter': 1, 'month_sub': {'kind': 'recruit', 'gold_before': 158, 'key': '1-7'}}
    policy.month_sub_step(paid_recruit_screen(gold=108), mem)
    c = Canvas(); c.text(16, 15, '1ねん7のつき108G')
    c.text(16, 151, 'チコリがはいかにくわわった!')
    policy.month_sub_step(parse(c.frame()), mem)
    assert mem['month_sub']['joined_names'] == ['チコリ']
    assert any(r['decision'] == 'recruit_join_announced' for r in mem['_records'])
    shop = {'recruit': 'opened', 'recruit_reserve': 50}
    policy._finish_month_sub(parse(month_canvas(106)), mem, shop)
    assert shop['recruit'] == 'done' and shop['recruit_reserve'] == 0
    assert 'チコリ' in mem['recruit_verification']['candidates']
    assert 'recruit_join_observed' not in [r['decision'] for r in mem['_records']]


def test_recruit_larger_balance_drop_alone_does_not_prove_payment():
    mem = {'chapter': 1, 'month_sub': {'kind': 'recruit', 'gold_before': 158, 'key': '1-7',
                                    'left_menu': True}}
    shop = {'recruit': 'opened'}
    policy._finish_month_sub(parse(month_canvas(106)), mem, shop)
    assert shop['recruit'] == 'unverified' and 'recruit_verification' not in mem


def _broken_hero_memory():
    mem = _short_recruit_memory()
    mem['house_eggs'] = {'どうし': {'broken': True, 'month': '1-6'}}
    return mem


def test_broken_hero_waits_for_real_repair_status_not_house_departure():
    mem = _broken_hero_memory()
    mem['garrison']['ほんじょう'] = ['どうし']
    order = {'step': 'x', 'general': 'どうし', 'source': 'ほんじょう',
             'target': 'ゴーメン', 'cards': ['フットバース'], 'after': None}
    assert not policy._ready(order, mem)
    mem['house'] = {'phase': 'travel', 'general': 'どうし', 'purchased': False}
    assert not policy._ready(order, mem)
    mem.pop('house')  # travel timeout does not clear the observed broken egg
    assert not policy._ready(order, mem)
    mem['house_eggs']['どうし'] = {'broken': False, 'uses': 4, 'month': '1-7'}
    assert policy._ready(order, mem)


def test_broken_hero_allows_other_general_and_friendly_staffing_move():
    mem = _broken_hero_memory()
    order = {'step': 'x', 'general': 'ゼウス', 'target': 'ゴーメン', 'after': None}
    assert policy._ready(order, mem)
    assert policy._ready({**order, 'general': 'どうし', 'purpose': 'move'}, mem)
    mem['house_eggs']['どうし'] = {'broken': None}
    assert policy._ready({**order, 'general': 'どうし'}, mem)


def test_interim_attack_uses_another_general_instead_of_broken_hero():
    mem = _broken_hero_memory()
    assert policy._interim_source(mem, 'ゴーメン', None, {'ほんじょう'}, set()) == ('ほんじょう', 'ゼウス')
    mem['garrison']['ほんじょう'] = ['どうし']
    assert policy._interim_source(mem, 'ゴーメン', None, {'ほんじょう'}, set()) is None


@pytest.mark.parametrize('kind', ['card_select', 'sortie_confirm', 'map_target'])
def test_hotloaded_broken_hero_sortie_is_cancelled_before_commit(kind):
    mem = _broken_hero_memory()
    mem['active'] = '1-A1'; mem['orders']['1-A1'] = 'pending'
    mem['sortie_attempt'] = {'step': '1-A1'}
    screen = parse(Canvas().frame()); screen.kind = kind
    actions = (policy.target_step(screen, mem, None) if kind == 'map_target'
               else policy.deploy_step(screen, mem))
    assert actions == [policy.pad('b')]
    assert mem['active'] is None and mem['orders']['1-A1'] == 'pending'
    assert 'sortie_attempt' not in mem
    assert mem['_records'][0]['decision'] == 'hero_broken_egg_sortie_held'


@pytest.mark.parametrize('gold,soldiers,recruit', [(136, 6, 50), (101, 0, 21), (79, 0, 0)])
def test_broken_hero_keeps_house_fee_before_recruit_and_soldiers(gold, soldiers, recruit):
    mem = _broken_hero_memory()
    shop = policy._plan(mem, {'year': 1, 'month': 7, 'gold': gold})
    assert shop['hero_repair_reserve'] == 50
    assert shop['soldiers'] == soldiers and shop['recruit_reserve'] == recruit
    assert shop['recruit_priority']


def test_recruit_and_castle_cannot_spend_broken_hero_house_fund():
    mem = _broken_hero_memory()
    screen = parse(month_canvas(101, on='しょうぐんぼしゅう'))
    policy.month_step(screen, mem)
    assert mem['shop']['recruit'] == 'skipped'
    assert mem['shop']['soldiers'] == 0 and mem['shop']['chikujou'] == 'skipped'
    assert 'month_sub' not in mem


def test_cached_month_plan_protects_newly_observed_broken_hero_once():
    mem = _broken_hero_memory()
    mem['shop'] = {'key': '1-7', 'gold_start': 136, 'items': [], 'merchant_done': True,
                   'soldiers': 56, 'soldiers_done': False, 'reserve': 0,
                   'recruit_priority': True, 'recruit_budget_version': 1,
                   'recruit_reserve': 50, 'recruit': 'check', 'egg': None}
    for _ in range(2): policy._plan(mem, {'year': 1, 'month': 7, 'gold': 136})
    assert mem['shop']['soldiers'] == 6 and mem['shop']['hero_repair_reserve'] == 50
    assert sum(r['decision'] == 'hero_repair_funds_reserved' for r in mem['_records']) == 1


def test_post_recruit_extra_deduction_reclamps_soldiers_to_keep_house_and_wage():
    mem = _broken_hero_memory()
    shop = policy._plan(mem, {'year': 1, 'month': 7, 'gold': 136})
    assert shop['soldiers'] == 6
    shop['recruit'] = 'opened'
    mem['month_sub'] = {'kind': 'recruit', 'gold_before': 136, 'recruit_paid_gold': 86,
                        'left_menu': True, 'key': '1-7'}
    policy._finish_month_sub(parse(month_canvas(84)), mem, shop)
    assert shop['soldiers'] == 4 and shop['recruit_reserve'] == 0
    assert 84 - shop['soldiers'] == 50 + policy.WAGE_RESERVE


def test_actual_castle_upgrade_quote_cannot_consume_hero_repair_fund():
    mem = _broken_hero_memory(); mem['shop'] = {'hero_repair_reserve': 50, 'recruit_reserve': 0}
    c = Canvas(); c.text(16, 15, '1ねん7のつき120G')
    c.text(16, 151, '80Gかかりますがよろしいですかな')
    c.text(32, 191, 'うむッ!'); c.text(136, 191, 'いかんッ!')
    sub = {}
    policy._chikujou_step(parse(c.frame()), mem, sub)
    assert sub['declined'] and 'quoted_cost' not in sub


def test_actual_egg_recovery_quote_cannot_consume_hero_repair_fund():
    mem = _broken_hero_memory(); mem['shop'] = {'hero_repair_reserve': 50}
    c = Canvas(); c.text(16, 15, '1ねん7のつき180G')
    c.text(16, 151, '3こで150Gになりまんな')
    c.text(32, 191, 'うむッ!'); c.text(136, 191, 'いかんッ!')
    sub = {'full_selected': True}
    assert policy._egg_recovery_step(parse(c.frame()), mem, sub) == [policy.pad('b')]
    assert sub['aborted'] and 'quoted_cost' not in sub


def test_egg_quote_keeps_wage_as_well_as_house_fee():
    mem = _broken_hero_memory(); mem['shop'] = {'hero_repair_reserve': 50}
    c = Canvas(); c.text(16, 15, '1ねん7のつき150G')
    c.text(16, 151, '2こで100Gになりまんな')
    c.text(32, 191, 'うむッ!'); c.text(136, 191, 'いかんッ!')
    sub = {'full_selected': True}
    assert policy._egg_recovery_step(parse(c.frame()), mem, sub) == [policy.pad('b')]
    assert sub['aborted']


def egg_choice_frame(names=('ぼーぼーどり', 'ゲーラス', 'ガーコイル'), cursor=0, x=176):
    c = Canvas()
    for i, name in enumerate(names):
        c.text(x, 176 + 16 * i, name)
    if cursor is not None:
        for dy in range(14):
            for dx in range(12):
                c.put(x - 30 + dx, 170 + 16 * cursor + dy, (230, 105, 74))
    return c.frame()


@pytest.mark.parametrize('cursor', [0, 1, 2])
def test_elabel_choice_uses_actual_names_and_knight_cursor(cursor):
    s = parse(egg_choice_frame(cursor=cursor))
    assert s.kind == 'egg_choice_menu'
    assert s.menu_cursor == 176 + 16 * cursor
    mem = {}
    assert policy.egg_choice_step(s, mem) == [policy.pad('a' if cursor == 0 else 'up')]
    assert bool(mem.get('_records')) == (cursor == 0)


@pytest.mark.parametrize('names,x', [
    (('ぼーぼーどり', 'ゲーラス'), 176),
    (('ぼーぼーどり', 'ゲーラス', 'しらない'), 176),
    (('ぼーぼーどり', 'ゲーラス', 'ガーコイル'), 40),
    (('こうげき', 'もうこうげき', 'たまごをつかう'), 176),
])
def test_elabel_choice_does_not_reinterpret_other_or_incomplete_menus(names, x):
    assert parse(egg_choice_frame(names, x=x)).kind != 'egg_choice_menu'


def test_elabel_choice_never_confirms_without_cursor():
    s = parse(egg_choice_frame(cursor=None))
    assert s.kind == 'egg_choice_menu'
    assert policy.egg_choice_step(s, {}) == []


def test_elabel_choice_routes_before_field_fallback_and_keeps_battle():
    battle = {'ally': 'どうし', 'enemy': 'クミン', 'ally_hp': 90, 'enemy_hp': 26}
    actions, state = decide(egg_choice_frame(), {'policy': {'chapter': 1, 'battle': battle}})
    assert actions == [policy.pad('a')]
    assert state['screen_kind'] == 'egg_choice_menu'
    assert state['policy']['battle'] == battle
    assert state['_records'][-1]['resulting_event'] == 'summon_selected_not_yet_confirmed'
    _, state = decide(Canvas().frame(), state)
    assert 'egg_choice' not in state['policy']


def test_elabel_choice_bounds_confirmation_retries_without_claiming_summoned():
    mem = {}
    s = parse(egg_choice_frame())
    assert policy.egg_choice_step(s, mem) == [policy.pad('a')]
    for _ in range(3):
        assert policy.egg_choice_step(s, mem) == []
    assert policy.egg_choice_step(s, mem) == [policy.pad('a')]
    for _ in range(5):
        assert policy.egg_choice_step(s, mem) == []
    assert [r['decision'] for r in mem['_records']] == ['egg_choice_select', 'egg_choice_select', 'egg_choice_unconfirmed']


def test_elabel_choice_interrupts_house_menu_even_without_prior_battle_panel():
    state = {'policy': {'chapter': 1, 'house': {'phase': 'open_roster', 'age': 0, 'total': 0, 'chapter': 1}}}
    actions, updated = decide(egg_choice_frame(), state)
    assert actions == [policy.pad('a')]
    assert updated['policy']['house']['interrupted'] is True


def test_elabel_choice_is_an_exit_from_unrelated_month_transaction():
    assert 'egg_choice_menu' in policy.MONTH_SUB_EXIT_KINDS


@pytest.mark.parametrize('cursor', [0, 1, 2])
def test_elabel_choice_matches_actual_ascii_digit_but_preserves_visible_labels(cursor):
    from docich.hanjuku_screen import egg_choice_names
    names = ('ユニコーン', 'てつじん8ごう', 'ドラゴンパピー')
    s = parse(egg_choice_frame(names, cursor=cursor))
    assert s.kind == 'egg_choice_menu'
    assert egg_choice_names(s) == list(names)
    assert policy.egg_choice_step(s, {}) == [policy.pad('a' if cursor == 0 else 'up')]


def test_elabel_digit_fold_does_not_accept_a_different_monster_number():
    assert parse(egg_choice_frame(('ユニコーン', 'てつじん9ごう', 'ドラゴンパピー'))).kind != 'egg_choice_menu'


def test_recruit_priority_waits_for_the_first_menu_cursor_instead_of_skipping():
    mem = _short_recruit_memory()
    incomplete = parse(month_canvas(166))
    incomplete.hand = None
    assert policy.month_step(incomplete, mem) == []
    assert mem['shop']['recruit'] == 'check'
    assert not mem['shop']['soldiers_done']
    ready = parse(month_canvas(166, on='しょうぐんぼしゅう'))
    assert policy.month_step(ready, mem) == [policy.pad('a')]
    assert mem['month_sub']['kind'] == 'recruit'
    assert not mem['shop']['soldiers_done']


def test_missing_priority_recruit_menu_has_a_bound_and_keeps_the_fee():
    mem = _short_recruit_memory()
    sc = parse(month_canvas(166, on='へいしほじゅう'))
    sc.lines = [line for line in sc.lines if 'しょうぐんぼしゅう' not in line.known]
    for _ in range(6):
        assert policy.month_step(sc, mem) == []
    assert policy.month_step(sc, mem) == [policy.pad('a')]
    assert mem['shop']['recruit'] == 'unverified'
    assert mem['shop']['recruit_reserve'] == 50
    assert mem['shop']['soldiers'] == 86
    assert any(r['decision'] == 'recruit_menu_unconfirmed' for r in mem['_records'])


@pytest.mark.parametrize('after,expected', [(106, 76), (94, 64), (30, 0), (18, 0)])
def test_paid_recruit_actual_balance_always_protects_wages_without_broken_hero(after, expected):
    mem = _short_recruit_memory()
    shop = policy._plan(mem, {'year': 1, 'month': 7, 'gold': 156})
    assert shop['soldiers'] == 76 and not shop.get('hero_repair_reserve')
    shop['recruit'] = 'opened'
    mem['month_sub'] = {'kind': 'recruit', 'gold_before': 156, 'recruit_paid_gold': 106,
                        'left_menu': True, 'key': '1-7'}
    assert policy._finish_month_sub(parse(month_canvas(after)), mem, shop)
    assert shop['recruit'] == 'done' and shop['recruit_reserve'] == 0
    assert shop['soldiers'] == expected
    assert shop['soldiers_done'] == (expected == 0)
    assert after - expected >= min(after, policy.WAGE_RESERVE)


def test_unverified_recruit_cannot_release_its_reserved_fee_or_rewrite_soldiers():
    mem = _short_recruit_memory()
    shop = policy._plan(mem, {'year': 1, 'month': 7, 'gold': 156})
    shop['recruit'] = 'opened'
    mem['month_sub'] = {'kind': 'recruit', 'gold_before': 156, 'left_menu': True, 'key': '1-7'}
    assert policy._finish_month_sub(parse(month_canvas(94)), mem, shop)
    assert shop['recruit'] == 'unverified' and shop['recruit_reserve'] == 50
    assert shop['soldiers'] == 76


def test_paid_recruit_never_refills_again_after_soldiers_already_finished():
    mem = _short_recruit_memory()
    shop = policy._plan(mem, {'year': 1, 'month': 7, 'gold': 156})
    shop.update(recruit='opened', soldiers_done=True)
    mem['month_sub'] = {'kind': 'recruit', 'gold_before': 156, 'recruit_paid_gold': 106,
                        'left_menu': True, 'key': '1-7'}
    assert policy._finish_month_sub(parse(month_canvas(94)), mem, shop)
    assert shop['soldiers_done'] and shop['soldiers'] == 76


def test_chart_card_command_without_cursor_never_confirms_and_has_a_bound():
    mem = {'chapter': 1, 'battle': {'enemy': 'ガルバンゾー', 'ally': 'どうし',
           'cards_used': [], 'card_flow': {'card': 'フットバース', 'stage': 'menu'}}}
    state = {'policy': mem}
    for _ in range(8):
        actions, state = decide(_menu_without_egg_row(), state)
        assert actions == []
    actions, state = decide(_menu_without_egg_row(), state)
    assert actions == [policy.pad('b')]
    assert state['policy']['battle']['card_flow'] is None


@pytest.mark.parametrize('x,y,expected', [(176,192,'battle_menu_pending'),
    (176,208,'battle_menu_pending'), (176,216,'battle_menu_pending'),
    (40,192,'text'), (176,200,'text')])
def test_clipped_command_requires_measured_position_and_never_confirms_without_cursor(x,y,expected):
    c = Canvas((0,0,0));c.text(x,y,'たまごをつかう')
    s = parse(c.frame())
    assert s.kind == expected
    if expected == 'battle_menu_pending':
        actions, state = decide(c.frame(), {'policy': {'chapter': 1}})
        assert actions == []


@pytest.mark.parametrize('y', [192, 196])
def test_measured_boss_two_line_command_uses_live_card_row_instead_of_egg(y):
    c = Canvas((0,0,0)); c.text(176,y,'たまごをつかう'); c.text(176,y+16,'きりふだ')
    for yy in range(y-8,y+4):
        for xx in range(152,164): c.put(xx,yy,(230,105,74))
    s = parse(c.frame()); assert s.kind == 'battle_menu' and s.menu_cursor == y
    mem = {'chapter':1,'battle':{'ally':'ゼウス','enemy':'クイーン','ally_hp':35,
        'enemy_hp':70,'cards_used':[], 'card_flow':{'card':'イッテツーン','stage':'menu'}}}
    actions,state=decide(c.frame(), {'policy':mem})
    assert actions == [policy.pad('down')]
    assert state['policy']['battle']['card_flow']['stage'] == 'down'
    # Moving the actual cursor to the card row authorizes A.
    c = Canvas((0,0,0)); c.text(176,y,'たまごをつかう'); c.text(176,y+16,'きりふだ')
    for yy in range(y+8,y+20):
        for xx in range(152,164): c.put(xx,yy,(230,105,74))
    assert parse(c.frame()).menu_cursor == y + 16
    actions,state=decide(c.frame(),state)
    assert actions == [policy.pad('a')]
    assert state['policy']['battle']['card_flow']['stage'] == 'list'


@pytest.mark.parametrize('y',[196,212])
def test_measured_shifted_single_egg_command_waits_for_the_second_row(y):
    c=Canvas((0,0,0));c.text(176,y,'たまごをつかう')
    assert parse(c.frame()).kind == 'battle_menu_pending'
    assert decide(c.frame(),{'policy':{'chapter':1}})[0] == []


@pytest.mark.parametrize('y',[188,200,204])
def test_unmeasured_boss_pair_offset_does_not_guess_a_human_command(y):
    c=Canvas((0,0,0));c.text(176,y,'たまごをつかう');c.text(176,y+16,'きりふだ')
    assert parse(c.frame()).kind != 'battle_menu'


def test_native_list_hero_priority_waits_for_companion_cursor_before_a():
    def frame(selected):
        c = Canvas()
        c.text(64, 31, 'しゅつげき')
        c.text(64, 47, 'ステータス')
        c.text(144, 39, 'どうし')
        c.text(144, 55, 'ゼウス')
        c.hand(122, 33 + 16*selected)
        return parse(c.frame())
    mem = {'chapter': 1, 'active': '1-B1', 'orders': {'1-B1': 'pending'}}
    assert policy.deploy_step(frame(0), mem) == [policy.pad('down')]
    assert not mem.get('sortie_general')
    assert policy.deploy_step(frame(1), mem) == [policy.pad('a')]
    assert mem['sortie_general']['1-B1'] == 'ゼウス'
