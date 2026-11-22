# 半熟英雄: 章をまたぐとステップ単位の記録を残さない（v541、2026-11-09）

## 症状

g1163 第5話 07:58 時点の policy に、**第4話のボスHP**が残っていました。

```
boss_hp_seen = {castle: 四季王城(南東), monster: スプリミョーネ, hp: 60, tick: 13738}
```

同じく `order_start_tick` / `site_step_rearm` / `sortie_relaunch` / `cards_capped` /
`source_mismatch_castle` / `y_jumps_total` など、**ステップID単位の記録**が
章替わり後も残り続け、12話まで進むと policy を肥大させます。

## 原因

`campaign.CHAPTER_SCOPE_KEYS`（章替わりで消すキー）に、これらステップ単位・
ボス単位のキーが入っていませんでした。

## 修正（v541 `hanjuku-chart-v541-chapter-scope-hygiene`）

`CHAPTER_SCOPE_KEYS` に以下を追加（すべて chapter/step 単位の記録）。

- `boss_hp_seen`（章のボスHP）
- `order_start_tick`, `site_step_rearm`, `sortie_relaunch`, `y_jumps_total`
- `season_sites_refused`, `cards_capped`, `equivalent_launch_miss`, `kit_spent`,
  `source_mismatch_castle`
- `sortie_held_source_defender_counts` / `_gates`、`source_defender_cancel_counts` /
  `sortie_held_source_defender_cancel_counts` / `sortie_source_defender_cancel_counts` /
  `sortie_source_defender_cancel_gates` / `source_defender_cancel_gates`

実行中の判断に使う値（`chapter` 自体、`garrison`、`sorties` など）は従来どおり保持します。

## 確認

- `tests/test_hanjuku_chapter_scope_cleared.py` に1件追加: 上記キーを持つ memory を
  `finish_chapter` に通すと全て消え、`chapter` は残る。
- 章・チャート・出撃 473件 passed。
- 本番反映後: 第6話へ進んだ時点で `boss_hp_seen` などが残っていないことを実測する。
