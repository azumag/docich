# 半熟英雄: 章またぎマニフェストの `set` が操作AIの入力を落とし、コーナーが input_stalled で終わる（v523、2026-11-08）

## 症状

2026-11-08 19:26:51 JST を最後に入力が止まり、画面は「しゅつげき名簿」
（`general_list`）のまま約10分間進まなかった。19:36:51 JST に
`hanjuku_run` の `input_stalled` が確定し、コーナーは sorengame へ戻って
終了した（g1153、第4話、月9-3）。

## 実測（g1153、2026-11-08、JST）

- `hanjuku_events.jsonl`: 最後の `input_sent` は 19:26:51.256。その後
  19:36:51.851 まで観測は 636 件届き、フレームも変化していた。
  19:36:51 の観測が `terminal_reason="input_stalled"`、
  `terminal_evidence="no_input_sent_while_observing"`。
- `hanjuku_decisions.jsonl`: 19:26:52 以降、毎回同じ計画
  `[{"buttons": ["b"], ...}]`（`dispatch_status="planned_not_yet_sent"`、理由は
  `carry_manifest_read` + `carry_arrived` 6件）を記録し続けた。実際の
  `input_sent` は無い。
- `hanjuku_bot.json` の mtime は 19:26 で、`step` は 60996 のまま。判断ログは
  `decision_id=...:60997` を記録し続けており、**状態の書き込みだけが毎回
  失敗していた**。
- 19:26 に `run/hanjuku_experience.json` が最後に更新され、以降も更新なし。

## 原因

1. v519（`78f7f429`）の章またぎマニフェストが、フロー状態へ
   `state['manifest_satisfied'] = set(listed)` を入れていた。
2. このフロー状態は操作AIの policy memory の一部で、`brains/hanjuku/bot.py` は
   それを `hanjuku_bot.json` として JSON 書き込みする。`set` は JSON 化できず、
   `atomic_write_json` が `TypeError: Object of type set is not JSON serializable`
   を送出した。
3. `bot.main()` はこの例外を「invalid observation」として `code=2` で終了。
   stdout には決定済みの actions が出力されていたが、`CommandBrain` は
   非ゼロ終了の stdout を破棄するため、入力は一度も送られなかった。
4. 状態が前進しないので、次の観測も同じ state から同じマニフェスト判定と
   同じ計画を再生成し、600 秒の `input_stalled` に到達した。

再現: 稼働中の `hanjuku_bot.json` と最後の画面で bot を実行すると、
`hanjuku-bot: invalid observation TypeError('Object of type set is not JSON
serializable') at game_switch.py:186:atomic_write_json < bot.py:639:main` で
`exit=2`、stdout には actions が残る。

## 変更（v523 `hanjuku-chart-v523-manifest-json-state`）

- `post_month_status._apply_manifest`: `manifest_satisfied` を list で保持する
  （参照側は `in` 判定のみ）。policy memory が JSON 化可能になる。
- `brains/hanjuku/bot.py`:
  - `_sortable` は dict のキー混在だけでなく、set などの非 JSON 値を
    `_json_safe` で正規化する（set は repr 順の list）。違反箇所は
    `non_json state /...` として stderr に出す。
  - 決定後の副作用（`persist` / `publish_adjust_request` / `ask_interim` /
    `hanjuku_bot.json` 書き込み）を個別に隔離し、失敗しても決定済み actions と
    `exit=0` を維持する。失敗は `persist_failed` / `adjust_request_failed` /
    `interim_failed` / `state_write_failed` として `_where` 付きで記録する。
- `ops/vm_actions/collect_diagnostics.py`: 現行版を allowlist へ追加。

## 変更しないこと

- マニフェストの到着・待ち判定そのもの（v519 の仕様）。
- 観測・判断・入力の順序、`input_stalled` の終端条件（600 秒 / 120 観測）。
- 非ゼロ終了の stdout を agent が採用する経路（不正な observation で
  推測入力を送らないため、bot 側で exit code を正す）。

## 検証

- `tests/test_hanjuku_bot_exit_diagnostic.py`: 新規3件。
  - set を含む state でも決定済み actions を保持し、list へ正規化して保存する。
  - bot state の書き込み失敗（TypeError）でも `exit=0` と actions を維持する。
  - 証跡書き込みの失敗（KeyError）でも `exit=0` と actions を維持する。
- `tests/test_hanjuku_post_month_status.py`: 新規1件。
  マニフェスト適用後の policy memory が `json.dumps` 可能で、
  `manifest_satisfied` が list であること。
- 関連: `test_hanjuku_chart_bot` / `test_hanjuku_map_discovery` /
  `test_hanjuku_hotload` / `test_hanjuku_brain` 513 passed、変更対象を直接
  参照する 20 ファイル 744 passed（いずれも 1 skipped）。
- 稼働中 runtime の state と最後の画面で再実行し、`exit=0`・`step` 前進・
  `manifest_satisfied` が list で保存されることを確認。

## 未確認・残件

- 実配信での再発防止は、次に章またぎマニフェストが発火した実ランで確認する。
- 稼働中プロセスの `hanjuku_run.json` の `bot_version` が v507 のままだった
  件（長寿命 supervisor が古い定数を保持）は本修正の対象外。判断ログ側は
  v522 で記録されており、入力の欠落とは独立。
