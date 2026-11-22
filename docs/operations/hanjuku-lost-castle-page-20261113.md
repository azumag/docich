# 半熟英雄: 失った城のページ下限も捨てる（v792、2026-11-13）

## 症状（実測 g1163 2026-11-13 22:3x）

`ドンドン` が敵に落とされた後も、`garrison_partial['ドンドン']` に
`キャロット` が残っていました（自軍だった頃のページ）。城は敵の旗です。

```
lost = ['オオキク', 'ドンドン']
garrison_partial = {"ドンドン": ["キャロット"]}
```

再奪還の最初の読みで v766 の tick 比較がこの古いページを優先すると、
敵時代の在城者を自軍の人数として扱う可能性がありました。

## 原因

`garrison_lost` は完全リスト（`garrison`）を消して住人を
`general_location_unknown` に移しますが、`garrison_partial` と
`garrison_partial_tick` を残していました。

## 修正（v792 `hanjuku-chart-v792-lost-castle-page`）

`garrison_lost` が、その城の `garrison_partial` と
`garrison_partial_tick` も削除します。住人の unknown マークは従来どおり。

## 検証

- 新規 `test_a_lost_castle_drops_its_page_and_marks_members_unknown`:
  城を失うとページと tick が消え、住人が unknown になる。
- 防衛・所有・前線・corridor 関連 **116件 passed**。
- 修正前の `world.py` では新テストが失敗することを確認。

## 本番反映後に実測すること

- 失った城に `garrison_partial` が残らないこと。
- 再奪還時に古いページの人数で判断しないこと。
- 第9話の進行と12話ボス撃破は引き続き実測で確認する。
