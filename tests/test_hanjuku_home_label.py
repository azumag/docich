"""Chapter 1's home castle carries the name the game itself writes.

The chart used to label it ほんじょう while the game always wrote アルマムーン,
and the alias leaked out:

- gcgx 第1話 城情報 lists アルマムーン as the castle holding 主人公・ゼウス・ヴィーナス・
  ココット; ほんじょう and 本城 never appear on the page (0 hits), and the castle
  rows match this chart's chapter 1 list one for one.
- g436 21:19 read 「アルマムーンじょうステータス」, so every home sortie went through
  a name check and 1-A1/1-V1 failed before leaving (v53-v59).
- g436 21:33: a defense of アルマムーン read as "only the next chapter has it" and
  advanced a chapter 1 game to chapter 2 cells (v44-v60).
- g550 frame-034 (the 築城 confirmation) reads
  「2ねん2のつき70Gアルマムーン2キカンドン1てきにんしゃはゼウスしょうぐんです
  などのしろをぞうちくなさいますか?」.

The alias also reached the viewers: the narration said 「どうし将軍をほんじょうから
キカンドン城へ」 while the screen said アルマムーン, and chapter 1 sortie after sortie
starts at home, so it was heard all the time.

Synthetic frames and documents only; no ROM images or screenshots.
"""
import copy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich import hanjuku_chart as chart
from docich import hanjuku_chart_adjust as adjust
from docich import hanjuku_commentary
from docich import hanjuku_policy as policy
from docich.hanjuku_bot import decide
from docich.hanjuku_pixels import Frame

FRAME = Frame(256, 224, bytes(256 * 224 * 3))
RETIRED = 'ほんじょう'
HOME = 'アルマムーン'


def test_chapter_1_home_carry_the_name_the_game_writes():
    assert chart.home_castle(1) == HOME
    assert chart.CASTLE_NAMES[1][0] == HOME
    # The measured cursor cell is unchanged: only the label was renamed.
    assert chart.CASTLES[1][HOME] == (731, 805)
    # The screen name reads straight back as the chart label: no alias needed.
    assert policy._castle_label({'chapter': 1}, HOME) == HOME
    assert not policy.STATUS_NAMES.get(HOME)


def test_no_chapter_encodes_the_retired_home_alias():
    labels = set()
    for chapter in range(1, 13):
        labels.update(chart.CASTLES.get(chapter, ()))
        labels.update(chart.CASTLE_NAMES.get(chapter, ()))
        labels.add(chart.home_castle(chapter))
        labels.add(chart.boss_castle(chapter))
        for order in chart.all_orders(chapter):
            labels.add(order['source'])
            labels.add(order['target'])
    assert RETIRED not in labels
    from_home = {order['step'] for order in chart.orders(1)
                 if order['source'] == chart.home_castle(1)}
    assert from_home == {'1-A1', '1-V1', '1-C1'}


def test_sortie_commentary_names_the_home_castle_as_the_game_does():
    rec = {'decision': 'order_start', 'chart_step': '1-A1', 'general': 'どうし',
           'source': chart.home_castle(1), 'target': 'キカンドン', 'cards': []}
    _, text = hanjuku_commentary.compose(rec)
    assert text == ('出撃準備の予定です。どうし将軍をアルマムーンからキカンドン城へ'
                    '向かわせる計画です。持たせる切り札は切り札なしです。')
    assert RETIRED not in text


def test_migrate_legacy_labels_renames_keys_values_and_notes():
    state = {'policy': {'chapter': 1,
                        'garrison': {RETIRED: ['どうし'], 'キカンドン': []},
                        'launched_orders': {'1-A1': {'source': RETIRED, 'target': 'ジョンリギ',
                                                     'note': f'{RETIRED}駐留のどうしでゴーメンへ'}},
                        'lost': [RETIRED]},
             'decision_trace': {'chart_step': '1-A1'}}
    before = copy.deepcopy(state['decision_trace'])

    assert chart.migrate_legacy_labels(state) is state

    policy_state = state['policy']
    assert policy_state['garrison'] == {HOME: ['どうし'], 'キカンドン': []}
    order = policy_state['launched_orders']['1-A1']
    assert order['source'] == HOME and order['target'] == 'ジョンリギ'
    assert order['note'] == 'アルマムーン駐留のどうしでゴーメンへ'
    assert policy_state['lost'] == [HOME]
    assert state['decision_trace'] == before
    assert RETIRED not in json.dumps(state, ensure_ascii=False)


def test_migrate_legacy_labels_merges_a_key_that_already_exists():
    state = {'garrison': {HOME: ['ゼウス'], RETIRED: ['どうし']}}
    chart.migrate_legacy_labels(state)
    assert state['garrison'] == {HOME: ['ゼウス', 'どうし']}


def test_migrate_legacy_labels_leaves_current_state_untouched():
    state = {'policy': {'garrison': {HOME: ['どうし']}, 'chapter': 1}, 'step': 7}
    before = copy.deepcopy(state)
    assert chart.migrate_legacy_labels(state) is state
    assert state == before


def test_decide_renames_state_written_before_the_label_change():
    state = {'policy': {'chapter': 1, 'garrison': {RETIRED: ['どうし']},
                        'launched_orders': {'1-A1': {'step': '1-A1', 'general': 'どうし',
                                                     'source': RETIRED, 'target': 'キカンドン',
                                                     'cards': []}}}}
    _, updated = decide(FRAME, state)
    assert updated['policy']['garrison'] == {HOME: ['どうし']}
    assert updated['policy']['launched_orders']['1-A1']['source'] == HOME
    assert RETIRED not in json.dumps(updated, ensure_ascii=False)


def _adjusted_doc(source):
    return {'schema': 1, 'chapter': 1, 'request_id': 'a' * 16,
            'orders': [{'step': 'J1', 'general': 'どうし', 'source': source,
                        'target': 'キカンドン', 'cards': [], 'after': None,
                        'note': f'{source}からの一手'}],
            'recruitment': None, 'purchases': None}


def test_adjusted_chart_adopted_before_the_rename_still_loads(tmp_path):
    # Without the rename the plan names a castle chapter 1 no longer has and
    # validate() rejects it, silently dropping the adopted adjustment.
    try:
        adjust.validate(_adjusted_doc(RETIRED))
    except ValueError:
        pass
    else:
        raise AssertionError('the retired label must not validate')

    (tmp_path / adjust.ADJUSTED_FILE).write_text(
        json.dumps(_adjusted_doc(RETIRED), ensure_ascii=False), encoding='utf-8')
    loaded = adjust.load(tmp_path)
    assert loaded is not None
    assert loaded['orders'][0]['source'] == HOME and loaded['orders'][0]['target'] == 'キカンドン'
    # validate() caps note/reason by length and the rename only lengthens text,
    # so the prose is left as written here; policy memory — which has no cap
    # and is migrated in full by decide() — renames it before a record quotes it.
    assert loaded['orders'][0]['note'] == f'{RETIRED}からの一手'


def test_adjusted_chart_reason_at_the_length_cap_still_loads(tmp_path):
    # Live g550: the adopted reason was exactly 200 characters (the cap) and
    # named ほんじょう, so migrating the prose there made it 201 and rejected a
    # plan that was otherwise fine.
    doc = _adjusted_doc(RETIRED)
    doc['reason'] = RETIRED + 'あ' * (200 - len(RETIRED))
    assert len(doc['reason']) == 200
    assert len(chart.migrate_legacy_labels(json.loads(json.dumps(doc)))['reason']) > 200

    (tmp_path / adjust.ADJUSTED_FILE).write_text(
        json.dumps(doc, ensure_ascii=False), encoding='utf-8')
    loaded = adjust.load(tmp_path)
    assert loaded is not None
    assert loaded['orders'][0]['source'] == HOME
    assert len(loaded['reason']) == 200
