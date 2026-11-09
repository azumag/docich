# 半熟英雄: ボス探索の割当を章替わりで消す（v565、2026-11-09）

## 症状

g1163 第7話 2026-11-09 15:32 時点の policy に、**第6話のボス探索割当が40件**
（`boss_step_probe`: `A:bf8c3dee:M…` → index 0〜12）残っていました。
`boss_probe_rotation`（章キー）も同様に残存。

診断の `boss_probes` は現在の章のものとして表示するため、**第7話で第6話の探索候補が
出ている**ように見えていました。

## 修正（v565 `hanjuku-chart-v565-boss-probe-scope`）

`campaign.CHAPTER_SCOPE_KEYS` に `boss_step_probe` / `boss_probe_rotation` を追加
（`boss_step_rearm` は既に追加済み）。章が変われば探索割当もローテーションも破棄します。

- 各章の最初のボス波は、従来どおり推定セル（index 0）から始まります
  （ローテーションは章キーで管理されていたため実害は小さいが、状態と診断の整合を取る）。
- 進行中の章の割当は従来どおり保持。

## 確認

- `tests/test_hanjuku_chapter_scope_cleared.py` に1件追加: 章替わりで
  `boss_step_probe` / `boss_probe_rotation` / `boss_step_rearm` が消え、`chapter` は残る。
- 章スコープ・ファントムstep・ボスセル 30件 passed。
- 本番反映後: 第7話の `boss_probes` が第7話の step だけになることを診断で実測する。
