# 半熟英雄: 左余白カードが宣言領域（world）の行軍を読めていなかった（v572、2026-11-09）

## 症状（オーナー報告 17:25）

「まだチャートでてないです　進軍数も０のまま」

配信の左余白カードが **余白進軍数: 0** のままで、出撃経路が描かれませんでした。

## 原因

docich の policy は宣言キー（`sorties` / `garrison` / `launched_orders` /
`boss_victories`）を **`world` 領域へ移行**しており、トップレベルには残りません。

soviet_now の `lib/docich_corner_stats.py` は `policy.get("sorties")` などを
**トップレベルだけ**読んでいたため、常に空でした。
実測: `hanjuku_bot.json` の policy は `sorties` をトップレベルに持たず、
`policy.world.sorties` に持っていました。

## 対応

1. **soviet_now PR #828** で `_policy_region(policy, key)` を追加し、`sorties` /
   `launched_orders` / `garrison` を「トップレベル → `world`」の順で読むように修正。
2. docich の `games/soviet_now` 参照を **68636c155** へ更新（このPR）。

## 確認

- soviet_now: 対象3ファイル 82 passed、失敗1件は**変更前の main でも失敗**する既存問題
  （`test_overdue_does_not_claim_battle`、stash して確認済み）。本変更の範囲外。
- 本番デプロイ後: 左余白カードの「余白進軍数」が実測の行軍数になり、
  ミニマップ（soviet_now #827）に出撃経路が出ることを実測する。
