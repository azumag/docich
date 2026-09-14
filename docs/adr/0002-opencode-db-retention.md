# ADR 0002: opencode セッションDBの bounded retention（writer exclusion gate 付き）

- Status: **Proposed**（design-only。本ADRは production / VM を変更しない）
- Issue: [azumag/docich#389](https://github.com/azumag/docich/issues/389)
- Supersedes: #402 / #403（live mutation が危険との理由で #404 により revert）
- 関連: `ops/vm_actions/storage_reclaim.sh`, `.github/workflows/vm-operations.yml`, `azumag/soviet_now` の opencode 起動経路

## 0. 前提と調査方法

- production VM（`soren-prod-vnic`）へ **read-only** で SSH。`du/find/stat/lsof/ps` と SQLite `mode=ro` のメタデータ（`sqlite_master` / `dbstat` / 行数）のみ。
- prompt・AI入出力・credential・DB本文は取得・表示していない。値は一切読んでいない。
- 未確認事項は都度「未確認」と明記する。

## 1. As-Is（実測）

### 1.1 DB と肥大の内訳
- `~/.local/share/opencode/opencode.db` = 約 2.49 GB（実ページ 2.32 GiB）。
- dbstat 実測（MiB）: `event` 1,263.8 ＋ 索引 279.4、`part` 685.9 ＋ 索引 123.3、`message`/`session` は計 ~25。**event と part でほぼ全量**。
- `freelist_count=0`（VACUUM 以外の回収余地なし）。
- 期間 2026-08-20〜09-14（約25日）で 2.3 GiB → **≈90 MiB/日**。

### 1.2 書き込み元
- `opencode run` プロセス（例: `timeout --kill-after=10s 360 /snap/bin/opencode run --agent soren-lite --model vercel/... <prompt>`）。**常駐サーバは無い**。
- opencode は snap `1.18.27`（rev 215, held）。
- 起動経路は複数:
  - `lib/ai_generate.sh:_ai_call_opencode_unqueued`（817-853行）が `"$opencode_bin" run ...` を実行。**XDG_DATA_HOME を設定しない**ため、呼び出し側が設定しない限り既定 `~/.local/share/opencode` を使う。
  - `broadcast/radio_engine.sh:55,154` の直接 `opencode run` は `XDG_DATA_HOME="$(_opencode_xdg_data_home)"` を設定 → worker DB `$ELOOP_LIB_DIR/tmp/state/xdg_data/opencode/opencode.db`（実測 111.9 MB）。
  - `broadcast/radio_corners.sh:1752` の直接 `opencode run`。
- 結果として **2つの opencode DB が併存**し、大きいのは既定パス側。どちらの経路が dominant かは本調査では未確定（prompt 本文を追わないため）。

### 1.3 履歴の再利用
- パイプラインは過去セッションを再利用していない: `--session` / `--continue` の使用なし、`opencode session list/export/stats` の参照なし（`lib` `broadcast` `workers` `tools` を grep）。
- `auth.json` は別ファイル。`account`/`credential`/`control_account` テーブルは空。→ DB を縮めてもログイン情報は不変。

### 1.4 既存の producer 側ロック
- `lib/ai_generate.sh` に `_opencode_run_lock_enter/leave`（`mkdired` ベースの per-agent ロック）。
  - 既定 `tmp/state/.opencode_run_locks/<scope>`、`OPENCODE_RUN_LOCK_DIR` で上書き可。
  - `OPENCODE_RUN_LOCK_ENABLED`（既定1）、`OPENCODE_RUN_LOCK_WAIT_SEC`（2）、`OPENCODE_RUN_LOCK_STALE_SEC`（1800）、`OPENCODE_RUN_LOCK_MAX_WAIT_SEC`（0）。
- `_ai_generation_queue_run` 経由で「同時実行数」を制御するが、**「新規 writer を一定期間止める」契約は存在しない**。

### 1.5 稼働状況
- 3分間・5秒間隔サンプリングで `opencode` プロセスの **idle 窓 0**。ほぼ常時稼働。

## 2. 問題

- opencode セッションDBは単調増加し、削除・保持期間の設定が opencode 側に無い（`opencode session delete` は1件単位、`opencode db` は任意SQLのみ）。
- #402/#403 で「古いセッション削除＋VACUUM」を実装したが #404 で revert。理由（妥当）:
  - 単発 `pgrep` は瞬間しか見ておらず、DELETE/VACUUM 中に**新規 writer が開始し得る**。
  - 削除が autocommit のため途中失敗で **partial prune** が残り得る。
- 正しい実装には **mutation window 全体で新規 writer を防ぐ producer-side の exclusion contract**、**transactional な削除**、**failure-path テスト**が必要。gate 方式の選択は設計判断であり storage script 内で発明しない。

## 3. 要件

- **R1 (exclusion)**: rotation の mutation window 中、新規 `opencode run` を開始させない。単発のプロセス観測では不可。
- **R2 (drain)**: 起動中の run は完了（または timeout）を待ってから mutation。
- **R3 (atomicity)**: 削除は単一トランザクション。失敗時は rollback し partial を残さない。
- **R4 (fail-closed)**: gate を取得できない場合は**変更せず**中断する。
- **R5 (testability)**: gate 排他・部分失敗・writer 復帰のパスを自動テストで検証できる。
- **R6 (no new privilege)**: 既存の権限境界（owner-only control plane、非root/unprivileged）を広げない。

## 4. gate 方式の選択肢

| 方式 | 概要 | 長所 | 短所 |
|---|---|---|---|
| **A. flock 共有/排他 gate（推奨）** | producer は `flock -s` を run の間だけ保持。rotation は `flock -x`。 | 新規 writer を確実に阻止、drain も flock が自然に待つ。追加権限不要。 | 全 `opencode run` 呼び出し点に 1 行のフック追加が必要。 |
| B. 明示 maintenance window | ラジオ生成 worker を数分 quiesce してから prune+VACUUM。 | 実装が単純。 | 配信を止める運用判断が必要、自動化しにくい。 |
| C. 既存 per-agent ロックの空き待ちのみ | `_opencode_run_locks` が空になるのを待つ。 | 追加機構なし。 | 新規 writer を止められず R1 を満たさない（#404 の指摘そのもの）。 |
| D. DBファイル差し替えで全リセット | `opencode.db*` を rename し新規作成。 | VACUUM不要で全回収。 | WAL の rename は新規接続と競合し破損リスク。R3 を満たしにくい。 |

**推奨: A。**

## 5. 推奨設計（Option A）

### 5.1 gate の共有規約
- gate ファイル: `${ELOOP_LIB_DIR:-/home/ubuntu/soren}/tmp/state/.opencode_rotation_gate.lock`
- producer は **共有ロック（`flock -s`）**、rotation は **排他ロック（`flock -x`）**。
- 環境変数: `OPENCODE_ROTATION_GATE`（path 上書き）、`OPENCODE_ROTATION_GATE_ENABLED`（既定 1）、`OPENCODE_ROTATION_GATE_WAIT_SEC`（共有取得の最大待ち、既定 120）。

### 5.2 producer 側（`azumag/soviet_now`）
- 新規 `_opencode_rotation_gate_run CMD...`:
  ```
  exec {fd}>"$gate" || return 1
  flock -s -w "$wait" "$fd" || { exec {fd}>&-; return 124; }
  "$@"; rc=$?
  exec {fd}>&-
  return $rc
  ```
- フック点: `lib/ai_generate.sh` の `opencode run` 実行（850/853行 と guard 経路）、`broadcast/radio_engine.sh:57,156`、`broadcast/radio_corners.sh:1752`。
- 後方互換: gate ファイルが無ければ flock は即取得できるため挙動不変。rotation 未使用時は無影響。
- 共有ロックが timeout した場合は **fail-closed**（当該 run を実行しない）。rotation は数秒で終わる想定なので 120s は十分。

### 5.3 rotation 側（docich control plane）
- 固定 reviewed helper（`ops/vm_actions/` 配下、main から送る）として実装し、既存 `reclaim` operation の opt-in ではなく **専用の明示 operation** にする（mutation window が長く危険度が高いため）。
- フロー:
  1. `flock -x -w "$GATE_WAIT"` で gate 取得（取得できない場合は **変更せず**中断＝R4）。
  2. exclusive 保持中に SQLite を開き、`BEGIN IMMEDIATE` → 対象 session と子（`event`,`event_sequence`,`message`→`part` cascade, 付随テーブル）を削除 → `COMMIT`（R3）。
  3. `VACUUM`（削除確定後）。VACUUM 失敗時は削除結果を保持し deferred として記録。
  4. fd close で gate 解放。
  5. 前後の DB バイト数と削除 session 数を返す（sanitized）。
- VACUUM は削除後なので rebuild 対象は小さく、排他保持は短時間。

### 5.4 削除 SQL（案）
```
BEGIN IMMEDIATE;
CREATE TEMP TABLE old_sessions AS SELECT id FROM session WHERE time_created < :cutoff;
DELETE FROM todo/session_share/session_message/session_input/session_context_epoch
  WHERE session_id IN (SELECT id FROM old_sessions);
DELETE FROM event          WHERE aggregate_id IN (SELECT id FROM old_sessions);
DELETE FROM event_sequence  WHERE aggregate_id IN (SELECT id FROM old_sessions);
DELETE FROM message        WHERE session_id IN (SELECT id FROM old_sessions);  -- part は cascade
DELETE FROM session        WHERE id IN (SELECT id FROM old_sessions);
COMMIT;
VACUUM;
```

## 6. 実装・ロールアウト範囲

1. **soviet_now PR**: producer gate helper ＋フック（上記 4 箇所）＋ テスト（gate 排他 / timeout / 無効化時挙動）。
2. **docich PR**: 本ADR、rotation operation（authorize/workflow/helper）＋ failure-path テスト。
3. **deploy**: soviet_now を main へ → docich の `games/soviet_now` gitlink を bump → docich main。gate は無効化状態でも後方互換なので、producer を先に配備してから rotation を有効化する。
4. **実行**: owner-only control plane から dry-run（対象 session 数のみ）→ apply。

## 7. 失敗モードとテスト計画

- **T1** rotation が exclusive を取得できない（producer がロック保持）→ 待機後中断、DB 無変更。
- **T2** 削除中の例外 → トランザクション rollback、行数・サイズ不変。
- **T3** VACUUM 失敗 → 削除は確定、partial 無し、deferred 記録。
- **T4** producer 側: rotation が exclusive 保持中は新規 `opencode run` が待つ。解放後に再開。
- **T5** gate 無効時（`ENABLED=0`）は従来どおり即実行。
- **T6** 世代・schema 変化（テーブル欠如）で fail-closed。

## 8. セキュリティ・安全性

- 秘密情報・prompt・入出力は取得/表示しない（メタデータと件数のみ）。
- 任意 exec は public repo では無効のまま。fixed reviewed helper を main から送る経路のみ。
- 権限は ubuntu（非root）。DB は ubuntu 所有で sudo 不要。

## 9. 決定が必要な点（owner）

1. **gate 方式**: A（flock 共有/排他）で確定してよいか。B を選ぶ場合は maintenance window の運用設計が必要。
2. **保持期間**: 既定 3 日でよいか（#389 の初期案）。
3. **対象 DB**: 既定 `~/.local/share/opencode/opencode.db` のみか、worker DB（`tmp/state/xdg_data/...`）も対象にするか。あるいは producer 側の XDG 不整合を別途修正して DB を一本化するか。
4. **実行契機**: 手動 dispatch のみか、定期（週次等）を許容するか。
