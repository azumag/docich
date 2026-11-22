# IMPLEMENT-5026 — [半熟英雄] 築城の試行台帳を監査の除外集合へ一致させる (v1370)

- Issue: azumag/docich#5026 / 判定 `chikujou_cancelled_with_resources` (ch4 8-8、VERIFY-5026.md)
- Base: origin/main 3a01ae71 (worktree、未コミット。commit/push/PR/fetch/rebase なし)
- バージョン: `hanjuku-chart-v1370-chikujou-tried-ledger`
- opencode の read-only 調査「#4989 (v1363) でも #4990 (v1365) でもカバーされない」を確認し、残存欠陥を実装修正。

## 原因

`chikujou_cancelled_with_resources` は `chikujou_declined`(終了系) または
`chikujou_leftover` の記録時に `_chikujou_snapshot` が「未試行候補あり+資金十分」
と再構成したとき発火する。この「未試行」の再構成が、フロー自身の除外集合より
狭かった:

1. `shop['chikujou_tried']` は `_finish_month_sub` で sub 終了時にしか
   `rows_tried`+`chosen` がマージされない。生存中の sub では各城の拒否
   (`chikujou_castle_unavailable`) と満級読み (`chikujou_max_level_seen`) が
   `month_sub['rows_tried']` にだけある。
2. 監査が走査する `records` は**現在の観測バッチのみ**。拒否は
   「城を選ぶ(A)」→「拒否文が出る」で各1観測を消費するため、2城以上の拒否が
   別観測にまたがると、以前の観測の拒否は監査から見えない。

結果、複数城が順に拒否・満級と実測されてからの正当な「未試行候補なし」終了が
「資源があるのにキャンセル」と誤発行される (ch4 は非ボス7城で複数拒否が現実的)。
さらに `month_sub_lost`/`month_sub_abandoned` は `rows_tried` を台帳へ
マージせず sub を破棄していたため、追跡喪失後の `chikujou_leftover` でも
同じ誤判定が起きた。

補助欠陥: 残存ちくじょう画面を B で閉じた際、`chikujou_refused` が無条件で
立ち、拒否行の無い「まだ選択可能な城一覧」まで翌2か月の再オープンを抑止していた。

## 修正

- `month_audit._chikujou_snapshot`: 同月 (`sub['key'] == shop['key']`) の
  生存中 chikujou `month_sub` の `rows_tried` と `chosen` を `tried` に合流。
- `flows/month._note_chikujou_tried` (新規): `month_sub_lost` と
  `month_sub_abandoned` で、同月 chikujou sub の `rows_tried`+`chosen` を
  `shop['chikujou_tried']` へ `_finish_month_sub` と同規則でマージしてから破棄。
  異なる月の stale sub は書き込まない。
- `chikujou_refused` は `これいじょう` / `しょうぐんがおりませなんだ` の
  実測がある場合のみセット (`month.chikujou_leftover` + orchestrator 早期B)。
  裸の城一覧を B で閉じても翌月を抑止しない (v1292 の意図どおり観測した拒否のみ抑止)。

## 変更ファイル

- `src/docich/hanjuku/month_audit.py`: live sub の `rows_tried`/`chosen` を `tried` に合流。
- `src/docich/hanjuku/flows/month.py`: `_note_chikujou_tried` 新規 + `month_sub_lost`/`month_sub_abandoned` で呼出 + `chikujou_leftover` の refused を拒否行実測時のみに限定。
- `src/docich/hanjuku/orchestrator.py`: 早期Bの `chikujou_refused` を拒否行実測時のみに限定。
- `src/docich/hanjuku/version.py`: v1370 へバンプ。
- `ops/vm_actions/collect_diagnostics.py`: 許可リストへ v1370 を 1 行追加。
- `ops/vm_actions/tests/test_collect_diagnostics.py`: 固定値リストへ v1370 を追加。
- `tests/test_hanjuku_month_audit.py`: 3件追加 —
  live sub `rows_tried` が全候補を覆うとき incident を出さない、
  未試行が残るとき incident を出す、他月の sub は除外に使わない。
- `tests/test_hanjuku_multi_chikujou.py`: 3件追加 —
  `month_sub_abandoned` / `month_sub_lost` で `chikujou_tried` へマージ、
  stale 月の sub は書き込まない。
- `tests/test_hanjuku_chikujou_orch_refused.py`: 1件追加 —
  拒否行の無い裸の城一覧は B で閉じるが `chikujou_refused` を立てない。

## テスト

- この環境では pytest 実行が承認されていないため、コード読みで検証。
  関連コマンド (別環境/CI で実施すること):
  `pytest tests/test_hanjuku_chikujou_orch_refused.py tests/test_hanjuku_multi_chikujou.py tests/test_hanjuku_chikujou_prompt_hold.py tests/test_hanjuku_month_audit.py tests/test_hanjuku_off_chart.py -x -q`
- 既存テストへの影響の目視確認:
  `test_orchestrator_chikujou_prompt_records_refused_and_skips_next_months` は
  `これいじょう…` を含む実測文を使うため refused 設定経路を維持。
  `test_an_untracked_chikujou_screen_is_closed_with_b` (off_chart) も同じ実測文。
- `month_sub` の破棄サイトは全て確認済み: egg 専用 (583)、月違い stale
  (1380、マージ不要)、`_finish_month_sub` (1436、既存マージ)、lost (1718)、
  abandoned (1842)。今回未完了の 2 サイトを台帳保存へ揃えた。

## 残課題

- 実機の監査行 `chikujou_event.decision` (`chikujou_declined` か
  `chikujou_leftover` か) と `tried`/`staffed_candidates` の照合は Issue 本文が
  必要。pytest/gh が使える環境で CI を回し、次回ランで同判定が消えることを確認する。
