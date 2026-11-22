# 半熟英雄: 攻撃ウェーブ中でも取得・帰還の一覧受領証を読む（v780、2026-11-13）

## 症状（実測 g1163 2026-11-13 19:1x）

`オオキク` を再奪還した後、v775 が登録した
`roster_read_due['オオキク'] = 141027` が、tick 141337 になっても
読まれていませんでした。

```
roster_read_due = {"オオキク": 141027}
tick = 141337
```

`orders()` は「攻撃ウェーブが進行中（`_attackers_marching > 0`）」の間、
`out`（ウェーブの攻撃指示）を返して**一覧受領証のプローブを飛ばして**
いました。城の取得や野営帰還の受領証はウェーブとは独立なのに、
読まれないまま残っていました。

## 修正（v780 `hanjuku-chart-v780-receipt-during-wave`）

- `_due_probe(..., roster_only=False)` を追加。`roster_only=True` では
  `routes.probe_due`（移動の到着確認）を除外し、`roster_read_due`
  （取得・帰還の受領証）だけを見る。
- `orders()` のウェーブ早期リターンの前に、受領証プローブを1回だけ
  先頭へ挿入する（`_probe_spend(..., budgeted=False)`、`_remember_probe`）。
- 受領証は1要求1回で消費される（v773/v774 の TTL・due 時刻はそのまま）。

## 検証

- 新規 `test_a_receipt_probe_is_available_during_a_wave`:
  攻撃ウェーブ中でも `roster_read_due` の城を先頭プローブで読む。
- corridor / 前線 / capture guard / probe 関連 **197件 passed**。
- 修正前の `corridor.py` では新テストが失敗することを確認。

## 本番反映後に実測すること

- 城の取得・野営帰還の直後に、ウェーブ中でも `roster_probe_due` が出ること。
- `roster_read_due` が長く残らず、取得城の `garrison` が更新されること。
- 攻撃ウェーブ自体は従来どおり続くこと（受領証は先頭1回だけ）。

## 残件

- 受領証プローブが先頭に入るため、その観測の攻撃は1回遅れる。
  受領証は要求ごとに1回なので頻度は限定的。
- 第9話の進行と12話ボス撃破は引き続き実測で確認する。
