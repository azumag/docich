# 半熟英雄: 場面で野営帰還を見送った記録に stage/goal/target を残す（v800、2026-11-14）

## 症状

場面（月初など）の割り込みで野営帰還を繰り返し中断し、`camp_recall_scene_abandoned`
でその野営を見送る際、記録には `resumes` しかありませんでした。どの段階
（dest など）で・どの帰還先（goal）・どの城を狙って見送ったのかを事後に
確認できず、場面割り込みの再現条件を特定できませんでした。

## 修正（v800 `hanjuku-chart-v800-scene-abandon-record`）

`camp_recall_scene_abandoned` の `observed_metric` に `stage` / `goal` /
`target` を追加します。挙動は変更しません。

## 検証

- 既存の `test_a_recall_cut_three_times_is_skipped_instead_of_looping` を拡張し、
  記録の stage（dest）/goal（アルマムーン）/target（`[175,84]`）を確認。
- 帰還・中断・field recall 関連 **80件 passed**。
- 修正前の `orchestrator.py` ではテストが失敗することを確認。

## 本番反映後に実測すること

- 見送り記録で、stage/goal/target が確認できること。
- どの段階で場面に切られやすいかを特定し、次の改善（dest の短縮・再開）に
  つなげること。
- 第9話の進行と12話ボス撃破は引き続き実測で確認する。
