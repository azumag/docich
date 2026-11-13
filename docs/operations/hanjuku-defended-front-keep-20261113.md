# 半熟英雄: 敵にせめこまれた直後の前線は2人を残す（v765、2026-11-13）

## 症状（実測 g1163 2026-11-13 14:05〜14:43）

第9話で `オオキク` を14:05に奪還しましたが、14:15頃から敵の反撃を受け、
`ナッテ` への攻撃で前線が1人になったところを奪い返されました
（`lost=['オオキク']`、`captured` からも外れる）。

```
14:04:49 battle_result オオキク  ally=58 enemy=0   （奪還）
14:15〜    せめこまれました（defense_observed、敵召喚ランプータン）
14:37      sortie_target_owned_settled（タオ>サヌト）
14:41      own からオオキクが消える（再失陥）
```

`corridor` の `_keep` は「対象城が flip-prone なら2人残す」だけでした。
** empties されるのは出撃元（前線）** であり、出撃元が直近に攻撃された
事実を見ていませんでした。

## 修正（v765 `hanjuku-chart-v765-defended-front-keep`）

- `battle.py` の「せめこまれました」観測で `defense_seen[castle] = tick` を記録。
- `corridor._defended_recently(mem, castle)` を追加。
- 出撃元が `DEFENSE_WINDOW_TICKS=900` tick 以内に攻撃されていれば、
  `_keep=2`（通常1）。攻撃対象の flip-prone と同じ扱い。
- v749 の「取った直後の前線は2人目を送る」は変更しない。**攻撃された事実**
  があるときだけ2人を残す。奪還（retake）は従来どおり待たない。

## 検証

- 新規 `test_a_just_defended_front_keeps_two_home`:
  防衛記録が無ければ2人目が出撃、`defense_seen` があれば攻撃せず
  `front_guard_held`。
- 新規 `test_a_defense_message_marks_the_castle_for_the_corridor`:
  「せめこまれました」で `defense_seen` が記録される。
- 第9話 corridor / 派遣 / 城ルート / 前線 / capture guard 関連 **214件**、
  防衛・バトル系 **148件 passed**。
- 修正前の `battle.py` / `corridor.py` では新テスト2件が失敗することを確認。

## 本番反映後に実測すること

- 「せめこまれました」後に `defense_seen[城]` が記録されること。
- その城からの攻撃が2人を残して行われること（`front_guard_held` の residents=2）。
- `オオキク` が奪還後に敵の反撃で落ちないこと、`ナッテ` 攻撃に進めること。

## 残件

- 防衛窓を過ぎると1人に戻る。敵の攻撃が繰り返される場合は、防衛窓の延長や
  増援優先を別途検討する。
- 第9話の進行と12話ボス撃破は引き続き実測で確認する。
