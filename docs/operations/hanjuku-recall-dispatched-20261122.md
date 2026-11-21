# 帰還ピッカーの受理を「未確認失敗」ではなく「発射済み」として記録する (v1350)

## 症状

野営からの帰還は「いどう」→ Yジャンプ→ 自城確定で送信される。送信 A の直後に
行き先ピッカーが閉じてフィールドマップへ戻ると、ゲームは帰還を受理している。
それでも旧実装は **同じ観測で** `camp_recall_unconfirmed`
（reason_code `camp_arrival_unconfirmed`）として終端し、月次オーディットが
「キャンプ帰還未確認」issue を受理のたびに 1 件起票していた。

さらにこの終端は `mem['uncertain'] = True` を立てるため、受理済みの帰還のあとに
地図アンカーを捨てて再アンカー観測を強いていた。

## 実測 (#4971 / g1190-ch1v4 ch11 46-10, 2026-11-21 16:59:41–45Z)

```
camp_enter           野営にカーソルを合わせて決定
camp_recall_cursor   実手カーソルを「いどう」へ
y_jump_open RECALL → アルマムーン
y_jump_confirm       カーソルを合わせて決定
camp_recall_requested
camp_recall_input_sent   16:59:45Z 送信を確認
camp_recall_unconfirmed  16:59:45Z ← 同じ秒。reason_code=camp_arrival_unconfirmed
```

## 原因

`flows/recall.py` `_recall_dispatch_step` が、`picker_closed`
（＝ピッカーが閉じてマップへ戻った＝受理）でも
`status = 'arrival_unconfirmed'` として同じ終端処理へ流していた。
コード内コメント自身が "picker_closed means the game accepted the dispatch" と
書いており、受理の証拠と未確認の扱いが矛盾していた。

## 変更

- `picker_closed` かつ `input_sent` かつ中断なし → `status='dispatched'` とし、
  記録を `camp_recall_dispatched`（`resulting_event='accepted_awaiting_arrival'`）に
  する。月次オーディットの incident 対象（`CAMP_TERMINAL`）ではないので
  偽の「キャンプ帰還未確認」は出ない。
- 受理済みでは `mem['uncertain']` を立てない。
- 到着確認は従来どおり `recall_verification` → `camp_recheck` の本人名簿読み
  （`arrival_request`）が担う。`_arrival_request` と在営観測の重複抑止は
  `arrival_unconfirmed` に加えて `dispatched` も受ける。
- 受理されなかった場合（`picker_closed` でない）は従来どおり
  `camp_recall_unconfirmed` + `uncertain`。

## 変えないこと

- 「到着した」とは断定しない（城内観測は `recall_in_castle_observed` のまま）。
- 同じ野営→同じ城の失敗カウント（`camp_recall_goal_failed`）の条件。
- ボス・砦のリトライ待ち（`_arm_recall_skip`）。

## 検証

- `tests/test_hanjuku_camp_receipts.py` を新しい契約へ更新
  （`camp_recall_input_sent` → `camp_recall_dispatched`、`status='dispatched'`、
  `uncertain` なし）。
- `test_hanjuku_recall_month_scene` / `test_hanjuku_recall_arrival_status` /
  `test_hanjuku_fort_camp` / `test_hanjuku_recall_interrupt` と合わせて **108 passed**。
- `test_hanjuku_month_audit` / `test_hanjuku_camp_recheck_exhaustion` /
  `test_hanjuku_retreat_recheck` / `test_hanjuku_recall_goal_retry` /
  `test_hanjuku_survey_cache` も実行する。

## 期待効果

- 受理済み帰還ごとの偽 issue と、不要な再アンカー観測がなくなる。
- 「帰還したのに未確認」と「本当に届いていない」を分けて数えられるようになり、
  3ヶ月野営放置（#4976 など）の切り分けができる。
