# 半熟英雄: 撃破実測のない四季王stepが行軍記録を失って二度と出撃しない（v529、2026-11-09）

## 症状

第4話で残る四季王が南東だけになったあと、`next_order` が None を返し続け、
南東へ出撃しなかった。ボットは調整チャートの要求と暫定出撃だけを繰り返していた。

## 実測（g1163、2026-11-09 03:0x JST）

- `hanjuku_bot.json`: `orders` の 4-B1/4-B2 が `launched` のまま、`sorties` は空。
  調整チャートの注文（A:ce1dfe1b:J1/J2）は撃破済みの北東・北西を向いていた。
- 行軍記録が失効すると `launched` のまま何も束縛できず、overdue の再出撃も
  `sorties` の記録を前提にするため、そのstepは二度と走らない。
- 同じメモリで `orders['4-B2']` を `pending` に戻すと `next_order` は
  `4-B2 ジェラート アルカ→四季王城(南東)` を返した。

## 修正（v529 `hanjuku-chart-v529-site-step-rearm`）

`next_order` の先頭で `_rearm_phantom_site_steps` を実行する。

- 対象は第4話の四季王stepで、`launched` かつ行軍記録なし・戦闘中でないもの。
- Yビューが撃破済みと実測した地点は開き直さない（`_site_king_down`）。
- `order_start_tick` から900観測以上経過したものだけ。同じstepの再実行は
  300観測のクールダウンで制限する。
- `site_step_rearmed` を判断ログへ残す。

## 確認

- `tests/test_hanjuku_site_step_rearm.py` 6件: 記録なし→開き直して `next_order` が返す /
  行軍中・戦闘中・新しいstep・撃破済み地点は開き直さない / クールダウン。
- 関連 `completed_season_sites` / `season_king_sites` / `boss_sortie_overdue` /
  `late_site_entry` / `spent_sortie_expiry` / `plan_failed_step_rearm` ほか 110件 passed。
- 本番反映後: `site_step_rearmed` の直後に `order_start`（南東）が出ることを実測する。
