# 半熟英雄: 防衛戦の開始でも「攻められた城」を記録する（v788、2026-11-13）

## 症状（実測 g1163 2026-11-13 21:2x）

`オオキク` への止め攻撃が進行中に、敵が `ドンドン` を攻撃しました。
`defense_seen` は「せめこまれました」のトースト表示でしか記録されないため、
**戦闘パネルから始まった防衛戦では脅威が記録されず**、その後の攻撃で
v765/v771 の「攻められた前線は強い将軍を残す」ルールが適用されませんでした。

```
21:27 オオキクへの止め攻撃（ドンドンから出撃）
21:28 ドンドン防衛戦（battle_start side=defense）
```

## 原因

`defense_seen` の書き込みは `flows/battle.py` の「せめこまれました」検出
（1817行）だけで、通常の `battle_start`（防衛側）では記録していませんでした。

## 修正（v788 `hanjuku-chart-v788-battle-start-defense-seen`）

- `_mark_defense_seen(mem, cur)` を追加。`side == 'defense'` かつ城が
  分かるとき `defense_seen[城] = tick` を記録する。
- 通常戦闘の `battle_start` 記録の直後に呼ぶ。
- v765 の「2人残す」、v771 の「最強を防衛に残す」、v781 の「最後の1人を
  出さない」が、トースト無しの防衛戦でも働く。

## 検証

- 新規 `test_a_defense_battle_start_marks_the_castle_defended`:
  防衛sideで `defense_seen` が記録され、攻撃sideでは変わらない。
- 防衛・バトル・off-chart・前線・派遣 関連 **208件 passed**。
- 修正前の `battle.py` では新テストが `ImportError` で失敗することを確認。

## 本番反映後に実測すること

- 防衛戦の開始直後に `defense_seen[城]` が入ること。
- その後の攻撃で `full_hp_leader.chosen` が2番目（最強は在城）になること。
- 前線の最後の1人が単独出撃しないこと（v781 と合わせて）。

## 残件

- `defense_seen` は900 tickの窓。防衛戦が続く間は再記録される。
- 第9話の進行と12話ボス撃破は引き続き実測で確認する。
