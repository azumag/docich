# 半熟英雄: 自軍になった城への古い奪還・攻撃指示を片付ける（v764、2026-11-13）

## 症状（実測 g1163 2026-11-13 14:24）

`オオキク` は 14:11:36 に自軍として確認され、`captured` に入っていました。
しかし 14:24:08 に、`オオキク` を敵城として扱う古い奪還指示が決着していました。

```
14:24:08 sortie_march_overdue I:c9:ドンドン>オオキク:73 ギー
  「敵城への出撃が上限の観測内に入城も経由も観測されないため、
    オーダーを未発射に戻す」
```

`オオキク` は自軍なので、この指示は「敵城への攻撃」ではなく、城を取る前に
作られた古い retake の残骸です。出撃と行先一覧の読みを1回分浪費し、
`_relaunch_overdue_march` が期限切れとして処理していました。

## 原因

`orders.py::_relaunch_overdue_march` は、行軍の target が**すでに自軍**か
どうかを見ていませんでした。`captured` に追加された後に残った
`purpose=retake/attack` の sortie は、期限まで待ってから
「敵城への出撃」として決着していました。

## 修正（v764 `hanjuku-chart-v764-owned-target-settle`）

`_relaunch_overdue_march` の先頭で、target が `_owned(mem)` に含まれ、
purpose が `attack` / `retake` の sortie を `failed` にして
`sortie_target_owned_settled` を記録します。

- 自城への移動（purpose `move`）は従来の移動ルール（到着は行先一覧）に任せる。
- 敵城への攻撃・奪還は従来どおり。
- 指示を「失敗」にして再出撃しないため、同じ古い指示が繰り返されない。

## 検証

- 新規 `tests/test_hanjuku_owned_target_settle.py`（4件）
  - 自軍城への retake / attack は `failed` + `sortie_target_owned_settled`
  - 自軍城への move は対象外（従来の移動ルール）
  - 敵城への attack は対象外
- 既存の overdue / march-blocked / road-defeat / chapter9 関連
  **172件 passed**。
- 修正前の `orders.py` では新テスト2件が失敗することを確認。

## 本番反映後に実測すること

- 自軍城を target にした `sortie_march_overdue`（「敵城への出撃」）が
  出なくなること。
- 代わりに `sortie_target_owned_settled` が記録され、同じ指示が再出撃
  されないこと。
- 前線の指示が `ナッテ` など現在の target に集中すること。

## 残件

- 城の所有確認と指示の生成が競合する短い窓は残る（capture 直後に作られた
  指示）。今回は期限切れ時の掃除で止めた。
- 第9話の進行と12話ボス撃破は引き続き実測で確認する。
