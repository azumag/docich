# 半熟英雄: 到着確認の巡回を月の巡回予算から切り離す（v761、2026-11-13）

## 症状（v760 反映後 2026-11-13 13:19〜13:30）

v760 で「移動を受け入れた城の一覧を読む」経路を追加しましたが、本番では
発火しませんでした。

- `corridor_probe`: `count=6`（月の巡回予算を使い切り）、
  `mint_order.source=サヌト`（枯渇巡回のまま）。
- `world.garrison['ドンドン']` は1人（ヘパイストス）のまま。
- `front_wait_mass` は解除されず、奪還の2人待ちが続いていました。

## 原因

`orders()` は枯渇巡回のプローブを発行する前に、

```python
if out and not _probe_budget_left(mem):
    return out
```

で打ち切っていました。v760 で追加した「移動を受け入れた城」の判定は
この後の `_roster_probe()` の中にあったため、**月の巡回予算を使い切って
いると到着確認そのものが発行されない**状態でした。到着確認は月の巡回
（所在不明将軍の探索）ではなく、移動の受領証です。同じ予算で縛るのは
誤りでした。

## 修正（v761 `hanjuku-chart-v761-due-probe-budget`）

- `_due_probe()` を切り出し、`orders()` の**予算チェックより前**に呼ぶ。
- 到着確認プローブは `roster_probe_due` として記録し、`budgeted=False` で
  月の巡回予算を消費しない（`corridor_probe.count` を増やさない）。
- 同じ城の連続読取は `routes.probe_due` の `PROBE_INTERVAL` が抑える。
- 枯渇巡回（v758 の名前ベース）は従来どおり予算内で動く。

## 検証

- 新規 `test_a_due_destination_is_read_even_when_the_tour_budget_is_spent`:
  予算を使い切っていても到着確認プローブが出て、`count` が増えない。
- `test_a_castle_a_transfer_was_sent_into_is_read_first` は
  `roster_probe_due` を確認するよう更新。
- 第9話 corridor / 派遣 / 城ルート / 前線 / capture guard 関連
  **194 件 passed**。
- 修正前の `corridor.py` では新テストが失敗することを確認。

## 本番反映後に実測すること

- `roster_probe_due` が記録され、`mint_order.source` が `ドンドン` になること。
- 行先一覧の読みで `world.garrison['ドンドン']` が複数人に更新され、
  `front_wait_mass` が解除されること。
- `オオキク` へ2人以上が同時に出撃すること。

## 残件

- 行先一覧を読んでも在城者が増えない場合、移動が途中の戦闘で失われている。
  道路の戦闘・再出撃の経路を別途直す。
- 第9話の進行と12話ボス撃破は引き続き実測で確認する。
