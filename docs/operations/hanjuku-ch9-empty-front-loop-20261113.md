# 半熟英雄: 第9話の空の前線で同じ出撃を繰り返し、城一覧の巡回が実行されない（v756、2026-11-13）

## 症状（実測 g1163 2026-11-13 09:5x〜10:2x）

第9話・36年2月、前線の `ドンドン` から `オオキク` へ出撃する計画が
**同じ内容のまま繰り返し** 発行され、そのたびに「出撃元に将軍がいない」で
閉じていました。

- 直近15分の内訳: `order_start` 37件、`order_done` 35件、
  `castle_status_not_list` 33件。1周は約11秒（城ジャンプ→ステータス→一覧→B）。
- `hanjuku_bot.json` の `world.garrison['ドンドン']` は **`[]`**（完全な読み）。
  一方 `world.garrison_partial['ドンドン']` には
  `['マサラ','アンディーブ','ヘーゼル','トレビス']` が残っていました。
- `corridor_drained['ドンドン']` は保存ファイル上では **null**。実際には
  `mark_drained` が書いた直後の同じプラン呼び出しで `_drained` が印を
  消していました（下記再現）。
- 国の将軍数は 32（scope `[9, '36-3']`）。在城0・所在不明1・行軍0で、
  **31人が所在不明**のままでした。

再現（実 state のコピーに、空読み直後の印を置いて `_drained` を呼ぶ）:

```
partial before: ['マサラ','アンディーブ','ヘーゼル','トレビス']
_drained('ドンドン', 'attack') -> False
partial after : ['マサラ','アンディーブ','ヘーゼル','トレビス']
mark after    : None
```

### 城一覧の巡回予算も1観測で尽きていた

`corridor_probe` は 10:24:30 の**同一秒**に `count` 1→6 を記録し、
その月の巡回予算（`PROBE_BUDGET=6`）を使い切っていました。1つの観測で
プランを複数回（`_orders` と `_next_order`）読むたびに `orders()` が
プローブを1件ずつ発行・計上していたためです。予算切れ後はプローブが
プランから外れ、実行されることなく月の残りを過ごします
（10:09:28、10:13:37、10:24:30 の3回とも同じ形）。

## 原因

1. `mark_drained(kind='empty')` は「一覧に誰もいない」という最新の実測を
   記録しますが、`_front_names` は `garrison` と `garrison_partial` の
   **長い方**を在城者とみなします。古いページ由来の4名が残っているため、
   `_drained` は「また人がいる」と判断して印を消し、次のプランが同じ
   出撃を再生成していました。
2. `corridor.orders()` は読み取りのつもりで呼ばれますが、プローブの発行と
   予算計上という副作用を持ちます。同一 tick 内の複数回呼び出しで別々の
   step を発行して計上するため、実行側が見る step と採用プランの step が
   ずれ、巡回が成立しません。

## 修正（v756 `hanjuku-chart-v756-ch9-empty-front-probe-reuse`）

- `corridor.mark_drained(kind='empty')`: 空の一覧は、その城について古い
  ページの下限（`garrison_partial`）を反証するので、印と同時に
  `garrison_partial[城]` を落とす。`kind='hero'` は従来どおり残す
  （v700 の「ページが完全読みに無い同僚を証明する」ケースを維持）。
- `corridor.orders()`: その tick ですでに発行したプローブを
  `corridor_probe.mint_order` に保持し、同じ観測内の再呼び出しでは
  **同じ step をそのまま返して予算を再計上しない**。実行済み
  （`launched` など）の step は再利用せず、次の観測で次の巡回を発行する。

## 検証

- `tests/test_hanjuku_corridor_drained.py`: 空読みの印が古いページ下限を
  落とし、`_drained` が `True` のままになること。hero 印は従来どおり
  ページの在城者を優先すること。
- `tests/test_hanjuku_corridor_probe_budget.py`: 同一観測の2回目の
  `orders()` が同じ step を返し、`count` が増えないこと。実行済み step は
  再利用せず次の巡回を発行すること。
- `tests/test_hanjuku_garrison_location.py`: 巡回のローテーションは
  観測（tick）が進んでから次へ移ることを明示。
- 既存の第9話 corridor / 派遣 / 城ルート関連 158 件 passed。
- 本番反映後、`ドンドン` の空読み後に `order_start`/`order_done` の
  11秒周期が消え、`corridor_probe` が1観測1件で実行されること、
  `garrison_partial` の古い名前が消えることを実測する。

## 残件

- 31人の所在不明（Issue #3963）そのものは未解決。今回の修正で城一覧の
  巡回が実行されるようになるため、城に居るなら次の巡回で在城者が回復する。
  野営中の場合は城一覧に出ないので、帰還・入城の経路（#3978）で回復させる。
- 第9話の進行と12話ボス撃破は引き続き実測で確認する。
