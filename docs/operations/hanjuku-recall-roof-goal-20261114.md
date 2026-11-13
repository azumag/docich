# 半熟英雄: 野営帰還の屋根指定でも帰還先を名付け、到着を読む（v807、2026-11-14）

## 症状

野営帰還の帰還先選択で、可視の自軍城の屋根を選んで確定する経路は
`_request_recall(..., None, ...)` と帰還先名を渡していませんでした。このため

- `camp_recall_requested` の `castle` が None になり、
- `roster_read_due`（到着後に帰還先の一覧を読む要求）が登録されず、
- 帰還した将軍が「未確認」のまま可視化されない

状態が続いていました（g1163 2026-11-13 15:5x-16:3x の4回の帰還でも着地が
読まれていない）。

## 修正（v807 `hanjuku-chart-v807-recall-roof-goal`）

屋根確定の経路でも `state['goal']` か `_recall_goal(mem)`（出発地/主人公から
最寄りの自軍城）で帰還先を名付け、`_request_recall` へ渡します。

- `camp_recall_requested` の `castle` が入る。
- `roster_read_due[帰還先]` が `RECALL_READ_DELAY` 後に登録され、到着後の
  一覧で帰還将軍が確認できる。

## 検証

- 新規 `test_a_roof_destination_is_named_so_the_arrival_is_read`:
  屋根確定で castle=タオ、roster_read_due[タオ] が設定される。
- 野営・帰還・月場面・field recall 関連の既存テストを実行。
- 修正前の `recall.py` では新テストが失敗することを確認。

## 本番反映後に実測すること

- `camp_recall_requested` の castle が入ること。
- 帰還後に帰還先の一覧が読まれ、将軍が `garrison` に現れること。
- 12話ボス撃破には未到達。目標は active のまま継続します。
