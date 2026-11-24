# 月次帰還タスクの同月再試行 v520（2026-11-08）

Base: `f9b4645b`。g1153 第4話 8-11 の実測。

## 確認した原因

月次の帰還タスクは、目的地ピッカーの途中で防衛・攻撃イベントが割り込むと
`camp_recheck._defer` により recall state を失う。`post_month_status` の
`wait_recall` は recall state が消えた時点で `recall_finished_or_deferred`
としてタスクを完了にし、次に処理するのは翌月の月次走査だった。

実測（2026-11-08）:

- 16:57 8-11 の月次走査が `complete: true` になり、帰還タスク
  ポワソン / アンディーブ / リースリング / キール を作成。
- 17:00:16 ポワソン: 野営 `[140,128]` で「きかん」を選択、17:00:29 に
  帰還先マラコビアを選択。17:00:31 に防衛イベントが割り込み
  `retreat_recheck_unconfirmed`。17:00:32 に `post_month_task_result recall`。
- 17:04:51 リースリング: 野営 `[136,112]` で「きかん」を選択。17:05:04 に
  攻撃イベントが割り込み、17:05:06 にタスク完了。
- 帰還Aの送信記録（`recall_verification.input_sent`）は無く、将軍は野営の
  まま `retreat_rechecks` に残った。再処理は翌月まで無い。

## 変更

- `wait_recall` で recall state が消えたとき、`recall_verification` の
  `input_sent` と `requested_tick`（当月の `started_tick` 以降）を確認する。
- 未送信なら同じ帰還タスクをタスク列の末尾へ戻し、同月内に再試行する。
  上限は `RECALL_TASK_RETRIES = 2`（初回を含め最大3回）。
- 送信済み（`input_sent: true`）と前月の古い送信記録では再試行しない。
- ログ `post_month_recall_requeued` に将軍・試行回数・上限を残す。

送信済みの帰還を再送しないため、二重指示は増えない。再試行も従来の
SELECT→野営→きかん→帰還先の実画面手順を通る。

## 検証

- `tests/test_hanjuku_post_month_status.py` に、中断時の再キュー、送信済みの
  非再キュー、前月記録の無視、上限の4ケースを追加。
- 同ファイル 80 件、関連する recall / camp テスト 90 件が成功。
- 実機の再発確認は配備後、次に帰還が割り込まれた月次で
  `post_month_recall_requeued` と後続の `camp_recall_input_sent` を確認する。
