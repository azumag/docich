# 半熟英雄: 野営帰還の帰還先は「歩く時間の後」に読む（v774、2026-11-13）

## 症状（実測 g1163 2026-11-13 16:5x〜17:1x）

v772 で野営帰還の帰還先を `roster_read_due` に登録し、v773 で TTL と
1要求1回の制限を入れました。しかし要求は**帰還を指示した tick**で登録され、
corridor はその直後に帰還先を読んでいました。帰還した将軍がまだ歩いている
途中なら、一覧は空で、貴重な1回の読取を空振りしていました。

## 修正（v774 `hanjuku-chart-v774-recall-read-delay`）

- `recall.RECALL_READ_DELAY = 180` tick（約4〜5分）を追加。
- 野営帰還の要求は `roster_read_due[帰還先] = tick + RECALL_READ_DELAY` と
  **due 時刻**で登録。
- `corridor._due_probe` は `roster_read_due` を due 時刻として扱う。
  - due 前（未来）は読まない。
  - due 後は TTL（900 tick）内で1回だけ読む。
  - TTL を過ぎた古い要求は state から削除する。

## 検証

- 新規/更新テスト
  - `test_a_future_recall_due_waits_for_the_walk`: 未来の due は due まで読まない。
  - `test_a_stale_recall_due_is_ignored`: 古い due は無視され、state から消える。
  - `test_stationary_picker_cursor_...`: 帰還要求の `roster_read_due` が
    `tick + RECALL_READ_DELAY` になる。
- 帰還・野営・off-chart・corridor 関連 **233件 passed**。
- 修正前の `recall.py` では帰還先 due の assertion が失敗することを確認。

## 本番反映後に実測すること

- 野営帰還の要求から約180 tick 後に `roster_probe_due` が出ること。
- 帰還先の一覧に帰還した将軍が現れ、`garrison` が更新されること。
- due 前の空振り読取がなくなること。

## 残件

- 帰還の移動時間はゲームの歩行速度に依存する。180 tick で足りない場合は
  実測で延長する。
- 第9話の進行と12話ボス撃破は引き続き実測で確認する。
