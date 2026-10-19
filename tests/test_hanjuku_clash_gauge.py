"""ぶつかり合いの連打ゲージ (青ゲージ) の画素読取と卵落予測との対比 (#1553)。

実測アンカー (g809 decision-001/026/029/031、g684 frame-089/094 の白兵パネル):
- 位置: 敵側 x=24..86 / 味方側 x=152..214、y=195..203 (各HP表示の下)。
- 赤系 (205,40,16)/(222,80,16) はゲージ残存、緑系 (189,206,156)+(148,133,139)
  は敵側の消耗しきり、(164,32,16) は特殊演出フレームで両側に出た暗い赤。
- 接触時の「青」(gcgx: ぶつかった際にバーが青くなる) は保存実機フレーム480枚
  のどこにもない。CLASH_GAUGE_BLUE が空の間は parser が青を一切報告しない
  (fail-closed)。校正は ``python3 -m docich.hanjuku_clash_gauge`` で実機リング
  から行い、校正後にだけ state 'blue' が流れる。
"""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich import hanjuku_policy as policy
from docich.hanjuku_screen import (CLASH_GAUGE_BLUE, CLASH_GAUGE_BOX, CLASH_GAUGE_DARK_RED,
                                   CLASH_GAUGE_GREEN, CLASH_GAUGE_RED, ClashGauge,
                                   _clash_gauge, parse)
from docich.hanjuku_clash_gauge import calibrate_blue, gauge_report
from test_hanjuku_chart_bot import Canvas
from test_hanjuku_survival import battle, memory


# 実測アンカー: 座標とパレットは本番フレームからそのまま転記する。
MEASURED_BOXES = {'enemy': (24, 195, 86, 203), 'ally': (152, 195, 214, 203)}


def battle_canvas(*, enemy=None, ally=None):
    c = Canvas((238, 238, 238))
    c.text(16, 176, 'ミント', (32, 32, 32))
    c.text(96, 176, '32', (32, 32, 32))
    c.text(144, 176, 'どうし', (32, 32, 32))
    c.text(224, 176, '90', (32, 32, 32))
    for side, tone in (('enemy', enemy), ('ally', ally)):
        if tone:
            x0, x1 = CLASH_GAUGE_BOX[side]
            for y in range(195, 204):
                for x in range(x0, x1 + 1):
                    c.put(x, y, tone)
    return c


def test_measured_geometry_and_states_are_recorded_verbatim():
    assert CLASH_GAUGE_BOX == {'enemy': (24, 86), 'ally': (152, 214)}
    assert CLASH_GAUGE_RED == ((205, 40, 16), (222, 80, 16))
    assert CLASH_GAUGE_GREEN == ((189, 206, 156), (148, 133, 139))
    assert CLASH_GAUGE_DARK_RED == ((164, 32, 16),)
    # 接触の青は未校正: 表は空のまま出荷する (#1180 Phase 1 の契約)。
    assert CLASH_GAUGE_BLUE == ()


def test_battle_panel_bars_read_with_measured_states():
    s = parse(battle_canvas(enemy=(205, 40, 16), ally=(222, 80, 16)).frame())
    assert s.battle is not None and s.battle.enemy == 'ミント'
    assert [(g.side, g.state, g.box, g.filled) for g in s.clash_gauges] == [
        ('enemy', 'red', MEASURED_BOXES['enemy'], 1.0),
        ('ally', 'red', MEASURED_BOXES['ally'], 1.0)]
    s = parse(battle_canvas(enemy=(189, 206, 156), ally=(205, 40, 16)).frame())
    assert [(g.side, g.state) for g in s.clash_gauges] == [
        ('enemy', 'green'), ('ally', 'red')]
    s = parse(battle_canvas(enemy=(164, 32, 16), ally=(164, 32, 16)).frame())
    assert [(g.side, g.state) for g in s.clash_gauges] == [
        ('enemy', 'dark_red'), ('ally', 'dark_red')]
    # 緑の縞模様 (189,206,156)+(148,133,139) も最頻色で緑に分類する。
    c = battle_canvas(ally=(205, 40, 16))
    for y in range(195, 204):
        for x in range(24, 87):
            c.put(x, y, (189, 206, 156) if (x + y) % 4 else (148, 133, 139))
    assert [(g.side, g.state, g.filled) for g in parse(c.frame()).clash_gauges][0] == ('enemy', 'green', 1.0)


def test_absent_or_hidden_bars_report_no_gauge():
    s = parse(battle_canvas().frame())
    assert s.battle is not None and s.clash_gauges == []
    # 枠の半分以下しか彩色されていない観測 (メニューで隠れた等) は状態にしない。
    c = battle_canvas(ally=(205, 40, 16))
    for y in range(195, 204):
        for x in range(24, 55):
            c.put(x, y, (205, 40, 16))
    gauges = {g.side: g for g in parse(c.frame()).clash_gauges}
    assert 'enemy' not in gauges and gauges['ally'].state == 'red'
    assert _clash_gauge(c.frame(), 'enemy') is None


def test_blue_is_fail_closed_until_the_live_palette_is_calibrated(monkeypatch):
    blue = (57, 121, 189)                    # 想定パレット (未校正なので本番には無い)
    s = parse(battle_canvas(enemy=blue, ally=blue).frame())
    assert [(g.side, g.state, g.tones) for g in s.clash_gauges] == [
        ('enemy', None, (blue,)), ('ally', None, (blue,))]
    # 校正後にだけ青を報告する (ゲートの動作証明)。
    monkeypatch.setattr('docich.hanjuku.os_layer.screen.CLASH_GAUGE_BLUE', (blue,))
    s = parse(battle_canvas(enemy=blue, ally=blue).frame())
    assert [(g.side, g.state) for g in s.clash_gauges] == [
        ('enemy', 'blue'), ('ally', 'blue')]


def test_ring_frames_reproduce_through_the_calibration_harness(tmp_path):
    """hanjuku_frames/ のリングから充填状態を再現する (#1553 の完了条件の道)。"""
    frames = [battle_canvas(enemy=(205, 40, 16), ally=(222, 80, 16)).frame(),
              battle_canvas(enemy=(189, 206, 156), ally=(222, 80, 16)).frame()]
    for number, frame in enumerate(frames):
        (tmp_path / f'frame-{number:03d}.png').write_bytes(frame.png_bytes())
    plain = Canvas((238, 238, 238))          # 戦闘パネルの無いフレームは除外
    (tmp_path / 'frame-002.png').write_bytes(plain.frame().png_bytes())
    report = gauge_report([tmp_path])
    assert [row['frame'] for row in report] == ['frame-000.png', 'frame-001.png']
    assert [row['gauges']['enemy']['state'] for row in report] == ['red', 'green']
    assert report[1]['gauges']['enemy']['filled'] == 1.0
    assert report[0]['gauges']['ally']['box'] == list(MEASURED_BOXES['ally'])
    # 単一PNGも読める (実機リングの特定フレームの再現)。
    assert gauge_report([tmp_path / 'frame-001.png'])[0]['gauges']['enemy']['state'] == 'green'


def test_blue_calibration_refuses_tones_the_measured_tables_explain(tmp_path):
    blue = (57, 121, 189)
    red = battle_canvas(enemy=(205, 40, 16))
    other = battle_canvas(enemy=blue)
    (tmp_path / 'frame-000.png').write_bytes(red.frame().png_bytes())
    (tmp_path / 'frame-001.png').write_bytes(other.frame().png_bytes())
    with pytest.raises(ValueError):
        calibrate_blue(['frame-000.png'], [tmp_path])          # side 名が不正
    calibration = calibrate_blue(['frame-000.png:enemy'], [tmp_path])
    assert calibration['tones'] and all(tone['explained'] for tone in calibration['tones'])
    calibration = calibrate_blue(['frame-001.png:enemy'], [tmp_path])
    assert [(tone['name'], tone['explained']) for tone in calibration['tones']] == [
        ([*blue], False)]


def gauge_screen(enemy_gauge='red', enemy_hp=32, ally_hp=90):
    """戦闘パネル Screen に実測相当のゲージ観測を付けた状態を作る。"""
    mem = memory(hp=ally_hp, enemy=enemy_hp)
    state = {'red': 'red', 'green': 'green', 'blue': 'blue'}[enemy_gauge]
    gauge = ClashGauge('enemy', MEASURED_BOXES['enemy'], 1.0, ((57, 121, 189),), state)
    screen = battle(mem)
    screen.clash_gauges = [gauge]
    return mem, screen


def records(mem, kind):
    return [r for r in mem['_records'] if r['decision'] == kind]


def test_melee_record_carries_the_gauge_and_blue_never_flips_the_prediction():
    # parser は未校正の青を報告しない (test_blue_is_fail_closed...)。ここでは
    # 校正済みの 'blue' 観測が流れた時の対比だけを扱う: 記録は一度だけ、
    # 卵落予測は測定で書き換えず、制御も変わらない (#1553 の対比契約)。
    mem, screen = gauge_screen('blue')
    cur = mem['battle']
    cur['enemy_egg_drop_expected'] = True
    policy.battle_step(screen, mem)
    melee = records(mem, 'battle_melee')
    assert melee and melee[0]['clash_gauge'] == {
        'enemy': {'state': 'blue', 'filled': 1.0, 'box': list(MEASURED_BOXES['enemy']),
                  'tones': [[57, 121, 189]]}}
    cross = records(mem, 'battle_clash_gauge_cross_check')
    assert len(cross) == 1
    assert cross[0]['verdict'] == 'unclassified'
    assert cross[0]['observed_metric']['blue_side'] == 'enemy'
    assert cross[0]['expected_metric']['enemy_egg_drop_expected'] is True
    assert cross[0]['expected_metric']['egg_drop_watch'] is None
    policy.battle_step(screen, mem)
    assert len(records(mem, 'battle_clash_gauge_cross_check')) == 1   # 戦闘中1回だけ
    assert cur['enemy_egg_drop_expected'] is True                    # 予測は維持
    # 青以外の観測では対比を記録しない。
    mem, screen = gauge_screen('red')
    policy.battle_step(screen, mem)
    assert records(mem, 'battle_clash_gauge_cross_check') == []
