# IMPLEMENT-2479-P1 — released post-finish owner の退役経路（恒久対応）

対象: azumag/docich#2479（#5067・#5068 は同一根本原因で #2479 へ統合）
ブランチ: `fix/2479-corner-dropout`（base `origin/main` @ `1db7e64ef`）
調査レポート: `/home/hatch/work/docich-2479/INVESTIGATE-2479.md`

## 根本原因の要約（11-19 バリアント）

2026-11-19 本番: NetHack コーナーは**完走まで到達**したが、復帰 switch
`nethack -> sorengame` が canonical plane の `recovery_required` 駐機中に
admission で拒否された。`_finish_locked` は失敗前に**復帰側の** request id を
`switch_request_id` へ記録するため、post-finish 失敗は

- `rotation_request_id == <dispatch request>`（release の対象）
- `switch_request_id == <restore request>`（**dispatch と別**）
- `completed_at` / `finish_reason` あり
- 復帰 receipt は **不在**（admission 拒否は receipt を書かない）

という durable 形状になる。release までは既存経路で進む
（`_mark_released_request` / `released-unverified` / `excluded=...`）が、
`settle_released_failure` には prelaunch 退役エンジン
（`_terminalize_prelaunch_quiesce_failure`）しかなく、

- prelaunch 証明は `switch_request_id == expected_request_id` の
  対象 request 自身の terminal `failed` receipt を要求する
- post-finish ではその receipt が存在しない（別 request の receipt か無し）
  → 永久に `False` → owner が `failed` のまま恒久的に excluded

という gap が根本原因。さらに `last_error_code == rollback_failed` の場合は
`reconcile_failed_rotation_start` へ振り分けられるが、そちらも
`switch_request_id == request_id`（start 形状）を要求するため、post-finish の
rollback_failed も同じく詰まる。

## P1 実装内容（`src/docich/retro_corner.py`）

### `_terminalize_released_finish_failure`（新規 reviewed 退役エンジン）

`RetroCornerManager` に追加。証明は corner lock（`self._locked()`、呼び出し元が
保持）+ shared store lock（`self.store.lock(exclusive=False)`）の内部で確立する:

- `status == "failed"`
- `rotation_request_id == expected_request_id`（release が証明した exact request）
- `completed_at` が tz 付き ISO として解釈可能（**post-start 失敗の要件**）
- `switch_request_id` は「別の request id の文字列」か「不在（commit 済み restore
  で pop された正常形）」を許容。`switch_request_id == rotation_request_id` は
  start 失敗形状であり、必ず prelaunch 側へ残す（同エンジンは証明不足で
  fail-closed のまま）
- 記録された restore request の receipt が**存在する場合は terminal
  （`TERMINAL_RECEIPT_STATUSES`）を要求**し、non-terminal は `queued` で defer
  （実行中の restore を owner の下で退役させない）。**receipt 不在は許容** —
  admission 拒否は receipt を書かない本番形状そのもの
- canonical は `phase in {"idle","ready"}` かつ `operation`/`request_id`/
  `deadline_at`/`candidate`/`previous`/`retiring` が全て空
- **`active.game != target` は要求しない**: leftover の corner runtime は
  release が既に reclaim 可能と証明した正常ケースであり、次の switch が
  quiesce する
- `phase` が idle/ready 以外（`recovery_required`/`failed` 駐機・遷移中）、
  canonical 欠落以外の transient、precommit 失敗、store lock busy は全て
  `queued` を返す → caller が `released_settle_pending` pin を書いて
  `None`（deferred）を返し、次 tick で再試行

書き込みは成功時のみ:

- `status="interrupted"`、`end_reason="released-failure-after-corner-finished"`
- `completed_at` / `finish_reason` / request id 群は**保持**（実測の終了証跡）
- `last_error` / `last_error_code` をクリア、`switch_status` と
  `released_settle_pending` を pop
- precommit（`_clear_released_stop_request`）で stop request を先に除去してから
  書き込む（#2352 review と同じ順序）

### `settle_released_failure` の dispatch 更新

- `finish_shape = isinstance(switch_request_id, str) and
  switch_request_id != request_id` を分岐条件に追加。
- `rollback_failed and not finish_shape` → 従来どおり
  `reconcile_failed_rotation_start`（post-lock で実行するため
  `reconcile_rollback` フラグに置き換え。**finish_shape の rollback_failed が
  reconcile を誤起動して engine 結果を捨てるバグを修正済み**）。
- `finish_shape` → 直接 post-finish エンジンへ（prelaunch は同形状で必ず
  `None` になるためスキップしても等価）。
- それ以外 → 従来どおり prelaunch を先に試し、`None` なら post-finish エンジンへ
  フォールバック（`switch_request_id is None` の commit 済み restore 形状を
  カバー）。
- **prelaunch 経路の振る舞いは不変**（既存の start 失敗形状は全て既存分岐へ）。

## P3 実装内容（durable noop/拒否理由）

### `src/docich/corner_rotation.py`

- `AUTO_RESTORE_DETAILS`（fixed detail 語彙）を追加し、`_run_auto_restore_recovery`
  の durable record に `last_detail` を保存。語彙外の自由文は `unknown` に
  丸める（`request_id` や free text は durable に残さない）。初期 record にも
  `last_detail: None` を追加。語彙は `recover_failed_restore` の呼び出しチェーン
  （`_recover_restore_failed` / `_replay_restore_failed` /
  `_recover_terminal_hanjuku_restore` / `_drive_recorded_restore` /
  `_settle_adopted_restore`）が emit する固定リテラル全件を列挙済み。
  view 系 corner（yoso/weather/tsuitate/mystery）は文字列 status しか
  返さず `.detail` を持たないため対象外（`last_detail` は None のまま）。
- `RELEASED_SETTLE_OUTCOMES = {"retired","deferred","unretirable"}` を追加し、
  `_observe` の released 判定直後に `_record_released_settle_outcome` で
  corner ごとの最新 settle 回答を durable 化
  （`released-owner-unretired` では deferred と unretirable を区別できない
  問題の解消）。bounded: adapters ∪ known_corners の corner のみ保持。

### `ops/vm_actions/collect_diagnostics.py`（固定 enum 同期）

- `ROTATION_AUTO_RESTORE_DETAILS` / `ROTATION_RELEASED_SETTLE_OUTCOMES` を
  corner_rotation 側と同期して追加。
- `_rotation_auto_restore_projection` に `auto_restore_detail` を追加。
- `_rotation_released_settles_projection` を新設し `corner_rotation.released_settles`
  へ投影（request id は出さない）。
- `_project_corner_state` の `end_reason` allowlist に
  `released-failure-after-corner-finished` を追加。

### `src/docich/webui.py` / `src/docich/webui_resources/index.html`

- webui の `end_reason` allowlist に `released-failure-after-corner-finished` 追加。
- `CORNER_END_REASON_JA` に `"終了後の復帰失敗で中断"` を追加。

語彙同期が不要と判断した allowlist: `WEATHER_END_REASONS` / chart 系
end_reason / `soren_corner_stats.reasons`（settle 対象 adapter
`{"game","nethack","meriken"}` の state には書かれない）、
`retro_corner._END_CLAUSE` 系（完走済み corner の終了アナウンスは
`finish_reason` 側で済んでおり default fallback で十分）。

## P2 実装内容（自動復旧の浪費抑止・採用）

`_run_auto_restore_recovery` 冒頭で
`self._plane_phase(adapter) in AUTO_PLANE_RECOVERY_PHASES`（`failed` /
`recovery_required`）なら replay を呼ばず `None` を返す。

- 根拠: parked plane では replay（`recover_failed_restore`）は deterministic に
  noop/queued となり、本番では `restore-failure-unproven` × 3 で request の
  bounded budget を燃やし、誤った `restore-recovery-exhausted` を durable に
  残しうる。
- **budget semantics は不変**: per-request の attempt 数・backoff・
  exhaustion 記録は `auto_restore` record に従来どおり durable 保存。
  gate は budget 消費**前**に置くため、parked 中は attempt を一切消費せず、
  plane 復旧後に同じ budget で再開する。`_run_auto_plane_recovery` が
  parked phase の recovery を担当する既存設計も維持。

## テスト追加

### `tests/test_nethack_released_settle.py`（新クラス `TestNethackReleasedFinishSettle`）

本番形状 fixture（rotation_request_id ≠ switch_request_id・completed_at あり・
復帰 receipt 不在・canonical clean）を使い:

- 退役成功: `interrupted` + `released-failure-after-corner-finished` +
  `completed_at`/`finish_reason` 保持 + error/pending クリア + coordinator 非起動
- canonical active が既に restore 先（sorengame）でも受理（`active.game != target`
  を要求しない確認）
- 記録 restore の **terminal** receipt（`rolled_back` + `rollback_failed`
  エラーコード）でも受理 — rollback 分岐の dispatch 修正をカバー
- defer: canonical phase `recovery_required` 駐機 / in-flight 要素あり /
  **non-terminal restore receipt** → `None` + `released_settle_pending` pin
- defer: restore receipt が `StateCorruptError`（不正JSON・不正schema等）で読めない場合も `queued`。破損を「receipt 不在」とみなして退役を許可しない（回帰テスト `test_settle_defers_on_a_corrupt_restore_receipt`）
- refuse: start 失敗形状（`switch_request_id == request_id`）、
  `completed_at` なし、別 request、release record なし → `False`・state 不変
- 退役後に `run_rotation` が owner gate を通る回帰

### `tests/test_corner_rotation_failed_corner.py`

- parked plane では replay を一切呼ばず `auto_restore` record を作らない
  （budget 消費なし）+ plane 復旧後に同一 budget で再開（P2）
- `last_detail` が fixed 語彙で durable に残る / 自由文は `unknown`（P3）
- `released_settle_outcomes` に `deferred` → `unretirable` → `retired` が
  tick ごとに記録される（P3）

### `ops/vm_actions/tests/test_collect_diagnostics.py`

- `auto_restore_detail` / `released_settles` の投影が fixed enum に丸められ、
  request id / free text が出ないこと、行が無ければ None になること
- `end_reason` に `released-failure-after-corner-finished` が投影されること

## 検証結果

- **pytest: この環境では未実施**（実行コマンドが環境側で拒否された）。
  変更・新規テストともゲート側（Muse）で `pytest -q
  tests/test_nethack_released_settle.py
  tests/test_corner_rotation_failed_corner.py
  tests/test_retro_corner.py
  tests/test_game_corner_released_settle.py
  tests/test_soren91_released_settle.py
  ops/vm_actions/tests/test_collect_diagnostics.py` の実行が必要。
- 代替としてコード読み取り検証を実施した:
  - `settle_released_failure` の 3 分岐と `CornerResult` status →
    `True`/`None`/`False` の対応を既存 caller の構造に照合。
  - `finish_shape` 判定は caller 側 gate（`owner == request_id`）後なので、
    `rotation_request_id == request_id` は engine に達する時点で既に成立。
    legacy fallback owner（rotation id 無し）は `finish_shape` が False になり
    従来経路へ留まる。
  - post-finish エンジンの defer 経路は全て `queued` → pin 書き込み →
    caller が `None` を返し rotation が retry する既存契約と一致。
  - `CornerResult` dataclass、`store.lock(exclusive=False)`、
    `receipts.load` / `finish_request`、canonical `load() -> (dict, missing)`
    の各シグネチャを既存 fixture・実装と照合。
  - `_observe` での outcome 記録位置（settle 直後・`unretired` 判定前）と
    `busy`/`unretired`/`excluded` 処理との整合を確認。
  - P2 gate は `_auto_restore_budget` 呼び出し**前**なので record 未生成・
    attempt 非消費を確認。`_auto_release_latch` の fallthrough
    （`_slot_reusable` → `_run_auto_plane_recovery`）は不変。

## 実装前の裏取り（read-only diagnostics）2点 — 未実施

- (5) 復帰 `switch_request_id` の receipt が `store.receipts` に不在であること
- (4) canonical `active` が nethack のままか sorengame 復帰済みか

この環境では本番 state への read-only アクセスが利用できなかったため
**2点とも未実施。ゲート側で確認要**。実装は両方の形状を許容する
（receipt 不在 OR terminal、`active.game` 非比較）。

## issue #2479 への追記事項（11-19 post-finish バリアントの完了条件）

- rotation tick が `released_settle` で post-finish owner を `interrupted` +
  `released-failure-after-corner-finished` に terminal 化し、
  `excluded` から除去されること
- 自然抽選で nethack が再 dispatch され `started_at` が更新されること
- 監視が #5067・#5068 を自動 close すること

※ 本環境からの issue 編集は行っていない（投稿前スキャン経路が無いため）。
  ゲート側で追記をお願いしたい。

## 残課題 / ゲート側チェックリスト

- [ ] 上記関連テストの pytest 実行（この環境では未実施）
- [ ] diff 確認・秘密情報スキャン（合成値のみ使用、秘密情報未記入）
- [ ] 本番 read-only 裏取り 2 点（復帰 receipt 不在 / canonical active）
- [ ] issue #2479 への 11-19 完了条件追記
- [ ] deploy 後の受入: rotation tick → owner terminal 化 → excluded 解除 →
      nethack 再 dispatch（開始時刻更新）→ #5067/#5068 自動 close
- [ ] handoff.md 正本への記録（運用区切りの更新はゲート判断に従う）

## 対象外（非推奨のまま実施していない）

- owner 検査の緩和 — 未実施
- `recover-failed-rotation` の latch ゲートを released ledger へ拡大 — 未実施
