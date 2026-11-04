# 計画の「移動」注文が汎用の守備ルールで止まり、1時間動かなかった（v405、2026-11-05）

## 症状（実測）

第6話のボス隊を8隊出したあと、機械計画は暫定の移動2件だけになった。

- `ライム グルーポフ→オケアナ` / `ゼウス バビラリー→カバルッサ`
- 1時間以上 `pending` のまま1件も開始されず、`order_start` は 17 分間ゼロ。
- 計画は1分おきに再構築され続けていた（`chart_adjust_applied` 99件/92分）が、
  どの注文も開始されないため、ボス隊の再編成も起きなかった。

## 原因

`next_order` の `_source_spare_missing` が両方の移動を弾いていた。
出撃元の実測名簿が1人（ライム / ゼウス）なので「2人目の余剰が必要」と判定される。

本来この規則は攻撃用で、移動（`purpose == "move"`）は免除される設計
（`_reserve_source_guard` の「only explicit moves are exempt」）。
ところが `_mechanical_chart` が候補の `purpose` を計画へ写しておらず、
`chart_adjust._order` も `purpose` を落としていたため、
計画上の移動は `purpose: None` の「攻撃」として扱われていた。

## 変更

- `hanjuku_chart_adjust._order`: `purpose` を検証して保持する
  （`None` / `move` / `attack` / `retake` 以外は `invalid purpose` で拒否）。
- `strategy/replan._mechanical_chart`: 候補の `purpose` を計画の注文へ写す。
- これで `_reserve_source_guard` の移動免除が効き、1人しかいない城からも移動が出せる。
  攻撃・奪還の2人余剰ルールは変更しない。

## 検証

- `tests/test_hanjuku_plan_purpose.py`（新規5件）: 計画が `purpose` を保持する／
  未指定は `None` のまま／未知の値は拒否／移動は1人城から出せる／
  同じ注文でも `purpose` なしなら従来どおり止まる。
- 修正前は3件失敗、修正後 5 passed。関連（`chart_adjust` / `mechanical_conquest` /
  `off_chart` / 層 / `chart_bot`）を含め 513 passed。

## 未確認・残件

- 実機でこの2件の移動が開始され、その後にボス隊が再編成されるかは未確認。
- 第6話ボス城への入城は依然として未実測（v404 の候補セル並列試行の結果待ち）。
