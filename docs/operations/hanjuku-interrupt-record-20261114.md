# 半熟英雄: 場面中断の記録に帰還先の状態を残す（v801、2026-11-14）

## 症状（実測 g1163 2026-11-14 01:13）

野営のメニューを開き「きかん」まで到達したあと、帰還先の選択（dest）が
場面（月初）で中断しました。

```
01:11:43 camp_recall_cursor きかん（here）
01:13:21 camp_recall_interrupted {"stage": "dest", "steps": 1}
```

記録には stage/steps しかなく、どの帰還先（goal）を・どの城（dest_target）を
狙って・どこまで進んでいたのかを確認できませんでした。

## 修正（v801 `hanjuku-chart-v801-interrupt-record`）

`camp_recall_interrupted` の `observed_metric` に `goal` / `target` /
`dest_target` を追加します。挙動（中断の扱い・再開）は変更しません。

## 検証

- `tests/test_hanjuku_recall_month_scene.py` の3ケース（to_camp/menu/dest）を
  新しい記録内容に更新。
- 帰還・中断・field recall 関連 **64件 passed**。
- 修正前の `orchestrator.py` では3ケースが失敗することを確認。

## 本番反映後に実測すること

- dest 中断の記録で、goal/target/dest_target が確認できること。
- どの帰還先・段階で場面に切られやすいかを特定し、dest の短縮・再開の
  改善につなげること。
- 第9話の進行と12話ボス撃破は引き続き実測で確認する。
