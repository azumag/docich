# docich#5708 恒久対応の実装記録

## 対象事象と根本原因

2026-11-26 23:38 JST の NetHack コーナーでは、試合が死亡で終了したあと、sorengame への復帰切替が `draining` のまま解除できず、rotation が `recovery_required` にラッチした。

恒久対応で修正した根本原因は次の3系統である。

1. 死亡画面が先頭行の追加により adapter 上は `unknown` prompt と分類されても、runtime 所有 process が 0 件と証明できる場合は cancel acknowledgement が prompt 分類へ依存しないようにする。
2. 運用者が詰まった tmux session を削除したあとの `session_missing` を、無条件の hard refusal ではなく、厳密な死亡証明ができた場合だけ dead runtime として扱えるようにする。
3. game-switch が dead active runtime を retire した `switch` / `failed` / `timeout` / `active_retired=true` receipt を、NetHack コーナーの failed rotation recovery が terminal evidence として受理できない問題を解消する。

なお `severity_triggers` の期限切れ in-flight game-switch アラートは、実装開始時点の main に既に `game_switch_in_flight_expired` として入っていたため、この作業では重複実装していない。既存実装が受入条件を満たすことを確認した。

## 変更内容

### 1. tmux session 喪失後の死亡証明

`src/docich/tmux.py`

- `Tmux.owned_processes_alive_after_session_loss()` を追加した。
- 対象 session が strict な probe で確実に存在しない場合だけ使用できる補助証明である。
- runtime id と generation が一致する env tag を持つ process を pidfd で再検証する。
- 生存している tmux server、現在残っている全 pane leader とその子孫、現在の祖先 process は保護対象から外さない。共有 tmux 基盤や別 runtime が tag を継承していても、死亡証明の対象へ誤認しない。
- ただし tmux server の子孫全体は無条件に保護しない。session 削除後も target pane leader が server の直接子として一時的に残ることがあり、それを隠すと生存 process を死亡と誤証明するためである。
- server 自体が無い場合は保護すべき pane が存在しないため、完全一致 tag を持つ orphan scan だけで証明する。
- session が存在する、probe が失敗する、process 表を完全に列挙できない、pidfd 判定ができない、生存者が1つでもある場合は `None` または survivor を返し、死亡とは扱わない。

### 2. NetHack cancel acknowledgement

`src/docich/adapters/nethack.py`

- `NethackCoordinatorAdapter._runtime_process_survivors()` を override した。
- session が存在する場合は従来どおり pane tree を含む generic proof を使う。
- session が存在しない場合だけ、上記の session-loss proof を使う。
- `cancel_round_boundary()` の `session_missing` 分岐を変更し、session-loss proof が空 tuple `()` を返した場合に限り dead runtime cancel として acknowledge する。
- acknowledge 前には従来どおり `nethack_boundary.json` への terminal boundary 記録を必須とする。記録できなければ refusal のままである。
- survivor がある場合や証明不能な場合は、従来どおり `session_missing` で拒否し、診断には `orphan_processes_present` を記録する。
- `unknown` prompt かつ runtime 所有 process 0 件の経路は #4693 の既存仕組みをそのまま使い、prompt を死亡 disclosure と分類できることを ack の条件にしない。

### 3. dead-active retire receipt の corner recovery

`src/docich/nethack_corner.py`

- `recover_failed_rotation()` の restore receipt 検証に、次の dead-active retire shape を追加した。
  - outer/result status がともに `failed`
  - `operation=switch`, `target=sorengame`
  - `from_game=nethack`, `to_game=sorengame`
  - `error_code=timeout`
  - `failure_phase=round_boundary`
  - `boundary_cancelled=true`
  - `retained_active=null`
  - `active_retired=true`
- この shape は rollback owner が残っていない terminal evidence なので、死亡 disclosure prompt の分類や replay には入らない。
- canonical が次を厳密に示す場合だけ corner state を `interrupted` に確定する。
  - `phase=idle`
  - `active/candidate/previous/operation/request_id/deadline_at` がすべて空
  - `last_result` が受理した receipt result と一致
  - `retiring` が list として読める
- `retiring` が残っている場合は、全 entry が NetHack / cli / `cleanup_role=failed_boundary_active` の runtime だけであることを確認し、通常の `coordinator.recover(..., expected_snapshot=canonical)` を1回実行する。cleanup 完了を canonical 再読で証明できた場合だけ terminal 化する。
- canonical が一致しない、foreign runtime が混ざる、cleanup が残る、または再検証できない場合は fail-closed のまま返す。
- corner state が `interrupted` になれば、既存の `corner-rotation recover` は terminal adapter observation から pending reservation を commit して解放する。この二段階は既存の rotation recovery 契約であり、新しい bypass は追加していない。

### 4. severity trigger

変更なし。実装開始時点の `ops/vm_actions/collect_diagnostics.py` に次が既に存在した。

- fixed enum: `game_switch_in_flight_expired`
- 対象 phase: `draining`, `quiescing`, `stopping`
- 条件: `deadline_expired=true` かつ deadline 超過が grace 以上

既存テスト `ops/vm_actions/tests/test_severity_trigger_summary.py::test_expired_waiting_game_switch_names_trigger` も `draining` / `quiescing` / `stopping` を固定値で検証しているため、受入3は現行 main で満たされている。

## 追加・更新した回帰テスト

### `tests/test_nethack_boundary_diag.py`

- `test_unknown_death_prompt_with_zero_processes_is_acknowledged`
  - 先頭に `You die...` があるため strict な disclosure classifier が `unknown` を返す pane で、survivor 0 件なら cancel を acknowledge することを固定する。
- `test_missing_session_with_zero_owned_processes_is_acknowledged`
  - `session_missing` でも session-loss proof が survivor 0 件を証明できれば acknowledge することを固定する。
- `test_missing_session_with_a_survivor_or_no_proof_still_refuses`
  - survivor または証明不能なら従来どおり hard refusal であることを固定する。

### `tests/test_tmux_orphan_sweep.py`

- session-loss proof の zero survivor、生存 orphan、server 不在、保護対象 pane、session 生存、pidfd 判定不能の各分岐を追加した。

### `tests/test_nethack_failed_rotation_recovery.py`

- `test_dead_active_retire_receipt_terminalizes_without_prompt_classification`
  - dead-active retire receipt が prompt classifier なしで受理され、corner state が `interrupted` になることを固定する。
- `test_dead_active_retire_cleans_exact_retiring_rows_before_terminalizing`
  - cleanup row が残っている場合は exact snapshot で coordinator cleanup を行い、完了後だけ terminal 化することを固定する。
- `test_dead_active_retire_refuses_when_canonical_still_has_an_active_game`
  - canonical が同じ idle outcome でない場合は terminal 化しないことを固定する。

## 変更ファイル

- `src/docich/tmux.py`
- `src/docich/adapters/nethack.py`
- `src/docich/nethack_corner.py`
- `tests/test_nethack_boundary_diag.py`
- `tests/test_tmux_orphan_sweep.py`
- `tests/test_nethack_failed_rotation_recovery.py`
- `IMPLEMENT-5708.md`

`ops/vm_actions/collect_diagnostics.py` と `ops/vm_actions/tests/test_severity_trigger_summary.py` は、受入3の実装と回帰テストが既存だったため変更していない。

## バージョン採番

採番しない。

本変更は NetHack コーナー、game-switch adapter、tmux 所有権証明、rotation recovery、監視アラート確認というインフラ/配信運用基盤の修正であり、バージョン付き game artifact や半熟英雄本体の `BOT_VERSION` を変更しない。実装開始時点の main にある #5704fix の `v1568` は半熟英雄側の採番であり、本件はその対象外である。したがって新しいゲームバージョン番号は割り当てない。

## 検証状況

- 依頼どおり pytest は実行していない。CI で検証される。
- `git diff --check` は成功した。
- `python3 -m compileall` による構文確認を試みたが、この実行環境では python 実行の承認が拒否されたため、compileall は実行できなかった。構文確認未実施であることを明示する。
- 本番 VM 操作、push、PR 作成、issue/Slack 投稿は行っていない。
