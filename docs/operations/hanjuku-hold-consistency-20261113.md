# 半熟英雄: 待ち時計は武装した人数のときだけ残す（v786、2026-11-13）

## 症状（実測 g1163 2026-11-13 20:5x）

前線 `ドンドン` には3人（ページ読み: カシス / マサラ / ヴィーナス）いるのに、
古い `front_guard_hold`（143007）と `front_mass_count`（0）が残っていました。

```
garrison['ドンドン'] = []                 ← 完全リストは空（古い）
garrison_partial['ドンドン'] = ["カシス","マサラ","ヴィーナス"]
front_guard_hold  = {"ドンドン": 143007}
front_mass_count  = {"ドンドン": 0}
```

この状態から前線が1人に減ると、**古い1人待ちの時計がそのまま使われ**、
上限超過と判定されて即単独出撃する危険がありました。また、2人待ちが
満たされているのに人数カウントが古いままでした。

## 原因

待ち時計は「武装した人数」のときだけ意味を持ちますが、人数が増えて
条件を外れたときに削除していませんでした（空のときは v785 で削除）。

## 修正（v786 `hanjuku-chart-v786-hold-consistency`）

- 前線の人数が `_keep` を超えるとき、`front_guard_hold` と
  `front_guard_expired_tick` を削除する（1人待ちはもう適用外）。
- 前線の人数が `_mass` 以上になったとき、`front_mass_hold` /
  `front_mass_expired_tick` / `front_mass_count` も削除する。
- 空の前線の全削除（v785）はそのまま。

## 検証

- 新規 `test_a_full_front_drops_the_one_general_hold`: 3人の前線で古い
  1人待ちの時計が消える。
- 新規 `test_a_pair_ready_front_drops_the_mass_clocks`: 2人待ちが満たされた
  前線で、2人待ちの時計と人数カウントが消える。
- corridor / 前線 / capture guard / 派遣 関連 **182件 passed**。
- 修正前の `corridor.py` では新テスト2件が失敗することを確認。

## 本番反映後に実測すること

- 3人以上の前線に `front_guard_hold` が残らないこと。
- 2人待ちが満たされた前線に `front_mass_hold` / `front_mass_count` が
  残らないこと。
- 前線が1人に減ったとき、新しい待ち時計から始まること。

## 残件

- 人数は `_front_names`（完全リスト/ページの新しい方）に依存する。
- 第9話の進行と12話ボス撃破は引き続き実測で確認する。
