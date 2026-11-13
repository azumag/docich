# 半熟英雄: 野営の帰還先を城一覧で確認する（v772、2026-11-13）

## 症状（実測 g1163 2026-11-13 15:5x〜16:3x）

国の将軍数は32人でしたが、城一覧に見えるのは `オシロガ` の4人程度。
残りは野営（キャンプ）にいて、帰還を試みても確認できない状態でした。

```
15:54 camp_found → camp_enter → camp_recall_cursor ×4 → 16:00 camp_recall_interrupted
16:26 camp_found → camp_recall_interrupted
16:28 camp_found → 16:33 camp_enter → cursor ×4 → 16:34 interrupted
16:36 camp_found → destination picker → fallback → camp_recall_requested
16:37 camp_recall_input_sent → camp_recall_unconfirmed
```

帰還先の城を選び、送信まで行っていますが、**帰還先の城一覧を読む経路が
ありません**。そのため、帰還した将軍が実際にどの城に入ったのか確認できず、
`general_location_unknown` も空のまま、32人中大多数が「所在不明のまま
見えない」状態が続いていました。

## 原因

`roster_read_due` は他のフロー（出撃元の確認）で使われていますが、
野営帰還のフローは帰還先を `roster_read_due` に登録していませんでした。
また corridor の `_due_probe` は「移動を受け入れた城」だけを見ており、
`roster_read_due` の城を読みませんでした。

## 修正（v772 `hanjuku-chart-v772-recall-roster-due`）

- 野営帰還の `camp_recall_requested` で、帰還先 `goal` を
  `roster_read_due` に登録。
- `corridor._due_probe` の巡回対象に `roster_read_due` の城を追加
  （月の巡回予算とは別の到着確認。v760/v761 と同じ扱い）。
- 帰還先の一覧を1回読むことで、`_observe_garrison` が在城者を記録し、
  帰還した将軍が可視化される。

## 検証

- 新規 `test_a_recall_destination_is_read_without_an_incoming_move`:
  移動の incoming が無くても `roster_read_due` の城を読むプローブが出る。
- 第9話 corridor / probe / 前線 / capture guard 関連 **175件**、
  野営・帰還系 **243件 passed**。
- 修正前の `corridor.py` では新テストが失敗することを確認。

## 本番反映後に実測すること

- 野営帰還の後、帰還先の城一覧を読む `roster_probe_due` が出ること。
- 帰還した将軍が `garrison` に現れ、国の将軍数との差が縮むこと。
- 帰還が確認でき、前線へ送れる将軍が増えること。

## 残件

- 帰還先の一覧読みは帰還の移動時間を待たない（送信直後に読む）。空振りの
  場合は次の帰還要求で再登録される。
- 帰還そのものが中断される問題（#3978）は別途。
- 第9話の進行と12話ボス撃破は引き続き実測で確認する。
