# IMPLEMENT-4978 — 帰還ピッカー受理を同じ観測で「未確認」終端しない

Issue: azumag/docich#4978
Base: origin/main 30cec8457
Bot version: `hanjuku-chart-v1351-picker-dispatched`

## 原因

`src/docich/hanjuku/flows/recall.py` の `_recall_dispatch_step` は、
`picker_closed`（input_sent 確認済みで画面が `map` に戻った＝ゲームが帰還送信を受理した）
と `steps >= 6` のタイムアウトを同一分岐で終端し、どちらも
`status='arrival_unconfirmed'`・記録 `camp_recall_unconfirmed`・`mem['uncertain']=True`
として確定していた。受理済みの帰還が毎回「未確認」として月次オーディットの
issue 化対象になり（#4971, #4934 など）、地図アンカーも毎回捨てられていた。

## 変更内容

### `src/docich/hanjuku/flows/recall.py` (`_recall_dispatch_step`)

- `picker_closed` の場合は `status='dispatched'` とし、記録名を
  `camp_recall_dispatched`（`resulting_event='dispatched'`）に分離。
  `recall_verification` には `status='dispatched'` を保持する。
- `mem['uncertain']` は送信自体が未確認の `dispatch_unconfirmed` のときだけ立てる
  （`arrival_unconfirmed`＝送信済みだがピッカー閉鎖を未観測、でも立てない）。
- `recall_goal_failures`・`camp_recall_blocked` のカウントは従来どおり
  `arrival_unconfirmed`（タイムアウト）と `dispatch_unconfirmed` のみ。
  `dispatched` は構造上到達しない。

### `src/docich/hanjuku/flows/camp_recheck.py`

到着確認を `recall_verification → camp_recheck` に一本化するため、
新ステータス `dispatched` を「送信受理済み・到着未確認」として既存の
`arrival_unconfirmed` と同じ扱いで受け入れる。

- `observe_camp_status`: 同じ将軍に `dispatched` の検証が残る場合は
  新規 recheck をキューしない（従来の `arrival_unconfirmed` と同じ抑止）。
- `_arrival_request`: proof の `status` に `dispatched` を受理
  （`arrival_check_only` の名簿再読が受理済み帰還にも起動する）。
- `_step_flow` の named 終端: `dispatched` の検証は recheck 行を
  `arrival_unconfirmed` に写像（`_scope` の許可ステータス集合は不変）。
  `_defer` が無条件に立てる `mem['uncertain']` は、`dispatched` で
  事前値が偽なら除去し、受理済み送信で地図アンカーを捨てない。

### `src/docich/hanjuku/version.py`

- `BOT_VERSION = 'hanjuku-chart-v1351-picker-dispatched'`

### `ops/vm_actions/collect_diagnostics.py`

- バージョン許可リストへ `"hanjuku-chart-v1351-picker-dispatched",` を追加
  （v1348 エントリ直後、末尾カンマあり、既存エントリ保持）。
- `HANJUKU_CAMP_DECISIONS` に `'camp_recall_dispatched'` を追加し、
  診断テールで受理済み帰還を観測可能にする。

### テスト更新

- `tests/test_hanjuku_camp_receipts.py`:
  `test_actual_sender_receipts_bind_request_then_mark_dispatched` に改名し、
  `recall_verification['status'] == 'dispatched'`・記録列
  `['camp_recall_input_sent', 'camp_recall_dispatched']`・`not mem.get('uncertain')`
  を期待値にした。
- `tests/test_hanjuku_retreat_recheck.py`（2箇所）: picker_closed 終端後の
  `recall_verification['status']` を `'dispatched'` に更新。行側は
  `arrival_unconfirmed` のまま、`arrival_check_only` の到着再読が動くことを確認。

## 検証結果

- この実行環境では `python3` / `pytest` 系のコマンド実行がツール層で一貫して拒否され、
  pytest・`python3 -m compileall` のいずれも実行できなかった。
- `git diff --check` は clean。変更箇所は手動レビューで構文・インデント・
  下流整合を確認済み。
- 到着確認の整合: `dispatched` 検証は `_arrival_request` に受理され、
  named 終端では行ステータス `arrival_unconfirmed` へ写像されるため、
  `arrival_check_only` の名簿再読（城内観測 → `in_castle_observed`）経路は従来どおり動く。
- 監査: `month_audit` は `camp_recall_dispatched` を issue 化対象に含めない
  （`CAMP_TERMINAL`・kind 一覧とも未追加＝意図どおり「受理済み帰還は issue にならない」）。
  タイムアウト系の `camp_recall_unconfirmed`（`arrival_unconfirmed` /
  `dispatch_unconfirmed`）は従来どおり記録・監査される。

実行環境があれば以下のローカル確認を推奨:

```
python3 -m pytest -q tests/test_hanjuku_camp_receipts.py \
  tests/test_hanjuku_retreat_recheck.py tests/test_hanjuku_recall_interrupt.py \
  tests/test_hanjuku_recall_arrival_status.py tests/test_hanjuku_fort_camp.py \
  tests/test_hanjuku_recall_month_scene.py tests/test_hanjuku_month_audit.py \
  tests/test_hanjuku_recall_goal_retry.py tests/test_hanjuku_survey_cache.py \
  tests/test_hanjuku_camp_observation.py tests/test_hanjuku_post_month_status.py
```

## 残課題・留意点

- テスト未実行のため、CI での回帰確認が必須。
- `arrival_unconfirmed`（送信済み・ピッカー閉鎖未観測のタイムアウト）でも
  `mem['uncertain']` を立てない判断は issue の文言
  「uncertain は送信自体が未確認のときだけ立てる」に忠実化したもの。
  従来の保守的挙動へ戻したい場合は `dispatch_unconfirmed` の条件を
  `status != 'dispatched'` に戻すだけでよい。
- named recheck 終端では `_defer` 経由で `retreat_recheck_unconfirmed`
  （監査対象外の記録名）が出る。`dispatched` 時も同じ記録名になるのは
  既存セマンティクスの維持であり、issue の完了条件（受理済みが
  `camp_recall_unconfirmed` として記録されない）は満たす。
- commit・push・PR 作成・fetch・rebase は依頼により未実施。
  worktree に未コミットのまま残している。
