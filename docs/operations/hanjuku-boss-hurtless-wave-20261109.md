# 半熟英雄: ボスにダメージを与えられなかった将軍を次の波で後回しにする（v587、2026-11-09）

## 背景（実測）

第7話ボス（ランパイア）は `boss_hp_seen` が **426 → 298 → 75** と減りましたが、
`boss_hp_history`（v585）を見ると、**入城してもHPが動かない波**が混ざっていました。
従来は「入城した将軍」と「実際に削った将軍」を区別できず、同じ将軍を繰り返し送る
可能性がありました。

## 変更（v587 `hanjuku-chart-v587-boss-hurtless-wave`）

- `note_boss_monster_hp`: 新しい読みが**直前と同じHP**で、**攻撃者が変わっている**場合、
  直前の攻撃者を `boss_no_damage`（最大8名）に記録します。
- `_boss_wave_pick`: `boss_no_damage` の将軍を、HP0・卵ランクより**後**に並べます
  （他の条件が同じなら、まだ試していない将軍を先に送る）。

## 確認

- `tests/test_hanjuku_boss_hp_note.py`: 426（ココット）→426（ミモザ）で
  ココットが `boss_no_damage` に入る。
- `tests/test_hanjuku_boss_wave_solo.py`: `boss_no_damage` の将軍より未試行の将軍が選ばれる。
- ボスHP・ボス波・計画・診断 166件 passed。
- 本番反映後: 第8話ボスで「削れない将軍」が後回しになり、HP減少が速くなることを実測する。
