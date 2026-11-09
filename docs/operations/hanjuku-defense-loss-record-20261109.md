# 半熟英雄: 守備戦で落ちた城を失陥として記録する（v575、2026-11-09）

## 症状（実測）

g1163 第7話 2026-11-09。17:45 に マンシー が自軍、17:58 には マンシー と リデンブロック が
敵色になりました。しかし意思決定ログには **`castle_lost_observed` が1件もありません**
（`world_map_owners` は35件あるのに、失陥の記録だけ欠落）。

## 原因

城を `lost` に積む経路は3つあります。

1. 屋根・名簿の観測（`world.py`）… `castle_lost_observed` を記録する
2. 全体マップの旗（`world.py`）… 記録する
3. **守備戦の敗北（`flows/battle.py` の `battle_end`）… `lost` に積むだけで記録しない**

第7話の失陥はこの3番目だったため、失陥の事実・守備人数・相手将軍がどこにも残りませんでした。
守備が1人で落ちるのか、将軍が不在のところを突かれたのかを後から検証できません。

## 修正（v575 `hanjuku-chart-v575-defense-loss-record`）

`battle_end` の守備敗北経路で、**駐留リストを消す前に** 控えを取り、
`castle_lost_observed` を記録します。

- `observed_metric`: `cause: defense_loss` / `garrison`（落ちる前の在城将軍、最大8名） /
  `ally` / `enemy`
- `lost` に新規追加したときだけ記録（重複なし）。

## 確認

- `tests/test_hanjuku_defense_loss_recorded.py`（新規）: 実際の `battle_end` を通して
  守備敗北→`castle_lost_observed`（garrison 付き）→`lost` 追加→駐留削除を確認。
- 失陥・計画・チャート系 436件 passed。
- 本番反映後: 失陥時に `castle_lost_observed(cause=defense_loss, garrison=[...])` が出る
  ことを実測し、**なぜ落ちたか**（守備1人／不在）を次ラウンドの対策に使う。
