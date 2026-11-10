# 半熟英雄: 進行中の記憶なら一意な城名から章を確定する（v644、2026-11-10）

## 症状（実測 22:1x）

新規ランで `chapter: None` のまま、`resume_chapter_pending` を立てても**章が確定しない**
ため、月次ループと保留が続き攻略が始まりませんでした。

## 原因

章の確定（一意な城名）は `resume_chapter_pending` が立っているときだけ働きますが、
フラグが無いラン（状態を初期化した再開・新規開始）では、一意な城名を見ても確定できません
でした。

## 修正（v644 `hanjuku-chart-v644-chapter-running-memory`）

**進行中の記憶**（`resume_chapter_pending` または `tick` が整数 または `journal` が辞書）なら、
一意な城名で章を確定できるようにしました。空のスクラッチ辞書（テスト等）は従来どおり
確定しません（既存テスト `test_unknown_resume_chapter_resolves_only_fresh_unique_castle` は不変）。

## 確認

- レジューム・章・チャート・出撃・診断 **683件 passed**。
- 本番反映後: 章が確定し、出撃（攻略）が始まることを実測する。
