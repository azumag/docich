# ADR 0005: strategy 永久アーカイブ（prune されない by_hash）の世代設計

- Status: **Proposed**（設計 ADR。production / VM は変更しない）
- Issue: [azumag/docich#392](https://github.com/azumag/docich/issues/392)
- 関連: `ops/vm_actions/collect_diagnostics.py` / `ops/vm_actions/summarize_storage.py`（可観測性）、`ops/vm_actions/storage_reclaim.sh`（gateway stdin 予算）、`azumag/soviet_now` の `core/config.sh`・`strategy/regression.sh`・`eloop.sh`・`eloop_improve.sh`・`strategy/improve.sh`
- 起票元: `docs/operations/vm-storage-issues.md` の Issue D

## 0. 前提と調査方法

- 本ADRの As-Is は `azumag/soviet_now` の **main（b7e39d0f）を checkout して read-only に grep・閲覧**した結果と、Issue #392 と同スレッドの owner コメント（2026-09-23 の production read-only 実測）の記載で構成する。
- 容量・更新頻度の数値は Issue 側の production 実測（2026-09-16 時点）を引用する。本ADRの作成では production へ一切アクセスしていない。
- 戦略ファイルの**本文は読んでいない**。読んでいるのはパス・関数名・分岐条件のみ。

## 1. As-Is（実測）

### 1.1 実容量

| 項目 | 値 |
|---|---|
| `strategy_versions_archive/by_hash` | 4,475 ファイル / 825,921,536 B（0.83 GiB）※Issue 調査時点。同コメントでは 4,488 ファイル |
| 9/16 以降の更新 | 11 ファイルのみ（＝99.75% は不活性） |
| 想定圧縮効果 | Python 平文の gzip で概算 -75% ≒ -620MB |

### 1.2 設定（`core/config.sh:863-880`）

```
STRATEGY_HASH_ARCHIVE_DIR="strategy_versions/by_hash"
# 永続アーカイブ: prune されないバックアップ。backfill のフォールバック元
STRATEGY_HASH_PERMANENT_ARCHIVE_DIR="strategy_versions_archive/by_hash"
HASH_ARCHIVE_KEEP_TOP=100（旧既定 10 からの引き上げ）
HASH_ARCHIVE_PRUNE_SAFETY_MIN_KEEP=20
HASH_ARCHIVE_PRUNE_SAFETY_MIN_RATIO_PCT=60
HASH_ARCHIVE_RESTORE_PERMANENT=0
HASH_ARCHIVE_PRUNE_BACKGROUND=1
```

`HASH_ARCHIVE_KEEP_TOP` と prune safety guard は **作業アーカイブ（`strategy_versions/by_hash`）だけに効く**。永久アーカイブには prune 経路が存在しない。

### 1.3 writer

`strategy/regression.sh:749` `_archive_strategy_snapshot_by_hash` が平文の `<hash>.py` を**両方の**ディレクトリへ `cp` する（同名が既にあればスキップ）。呼び出し元: `eloop.sh:103,252` / `eloop_improve.sh:63-64` / `strategy/ab_gate.sh:132-134,320` / `strategy/regression.sh:807-811,3024,4977`。

→ 永久アーカイブの件数は「今までに発生した distinct hash 数」。**上限を持たない**。

### 1.4 reader（全員が平文 `<hash>.py` を直接開く）

| 経路 | 何をするか |
|---|---|
| `eloop.sh:138` `_find_strategy_archive_for_hash` | 作業 → 永久 の順で `${expected_hash}.py` を開き `extract_decide_hash.py` で検証 |
| `strategy/regression.sh:780` `_find_rollback_candidate_file_for_hash` | 作業を優先、永久にあれば作業へ repair copy してから返す |
| `strategy/regression.sh:2801` `_merge_rolling_scores_on_normalize` | 永久の `<stale_hash>.py` から作業アーカイブを修復（スコアはマージしない） |
| `strategy/regression.sh:803` `_backfill_hash_archive_from_known_versions` | 永久 → 作業 の復元。`include_permanent=1`（`HASH_ARCHIVE_RESTORE_PERMANENT`、既定 0）のときだけ |
| `strategy/improve.sh:315-319` `_archive_restart_has_candidate` / `:481-484` `_escape_ai_seed_available`、`eloop_improve.sh:1886-1894,2300-2303` | 永久 dir を Python へ渡し `os.path.join(permanent, f"{h}.py")` を開いて本文を読む（`archive_is_runtime_stable` は先頭 200,000 文字を読む） |

`strategy/improve.sh:527-528` のコメントが要点を言い切っている: wildcard 起源の seed は**ほぼ全て永久アーカイブにいる**（作業 by_hash は約16件に prune されるため）。

→ 永久アーカイブは「ディスク上のバックアップ」ではなく、**archive_restart / escape_ai の実質的な seed プール**。

## 2. 問題

- 作業アーカイブは `HASH_ARCHIVE_KEEP_TOP` で有界、永久アーカイブだけが無界限。この系で残った最後の単調増加要因。
- **keep 上限をそのまま永久アーカイブに適用するのは挙動変更**。seed プールと作業アーカイブの repair 元を同時に削るため、改善ループの脱出経路（archive_restart / escape_ai）が枯渇し得る。Issue の受入基準は「世代数に上限**または**十分な圧縮率」だが、前者は情報 loss を伴う。
- **gzip を今そのまま適用するのは経路破壊**。§1.4 の reader は全員 `<hash>.py` を直接 open する。mtime だけでリネームすると rollback 候補探索・normalize repair・seed 探索が同時に壊れる。

## 3. 要件

- **R1 (bounded)**: 永久アーカイブの allocated bytes が単調増加しなくなる（世代上限 or 圧縮）。
- **R2 (lossless)**: 戦略内容を1つも失わない。作業アーカイブから prune された hash の唯一のコピーだから。
- **R3 (reader-safe)**: on-disk 形式を変える前に、全 reader が両形式を読めること。
- **R4 (fail-closed)**: reader が `.py.gz` を読めないと確認できない場合、圧縮は実行しない。
- **R5 (observable)**: 戦略内容を読まずに、有界性を read-only diagnostics で検証できる。
- **R6 (no new privilege)**: owner-only control plane のみ。任意 exec は public repo で無効のまま。dry-run 既定。
- **R7 (gateway budget)**: `ops/vm_actions/storage_reclaim.sh` は 15,803 B（gateway exec stdin 上限 16,384 B、警告 16,000 B、`test_helper_payload_fits_gateway_stdin_cap` で固定）。**この helper に圧縮処理を足してはならない**（同上限超過で production reclaim が `operation_rejected` になる実績がある: helper が 16,919 B まで肥大化した PR #1005 の初回 apply attempt / 2026-09-23、`ops/vm_actions/tests/test_storage_reclaim.py` の `test_helper_payload_fits_gateway_stdin_cap` に記録）。

## 4. 選択肢

| 方式 | 概要 | 長所 | 短所 |
|---|---|---|---|
| **A. keep 上限** | `HASH_ARCHIVE_KEEP_TOP` 相当を永久アーカイブにも適用 | 実装が小さい・即効 | seed プールと repair 元を削る＝**挙動変更**。R2 違反 |
| **B. gzip ＋ reader fallback（推奨）** | reader が `.py` → `.py.gz` を順に読むようにし、7日超の平文を圧縮 | 情報 loss ゼロ・reader 互換・概算 -75% | soviet_now 側の reader 改修が前提。2フェーズ必要 |
| C. 外部 cold storage | 永久アーカイブを object storage へ退避 | ディスクから完全に消える | 新経路・新資格情報・復旧手順が必要。R6 を広げる |
| D. writer 側で永久への複写を止める | `_archive_strategy_snapshot_by_hash` の永久複写を削る | 増加が止まる | fallback / seed プールを失う。R2 違反 |

**推奨: B。** 受入基準の「十分な圧縮率で増加が有界」を lossless に満たせる唯一の方式。

## 5. 推奨設計（Option B）

### 5.1 Phase A — reader fallback（`azumag/soviet_now`）

- パス解決を**1箇所**に集約する。shell 側は `_find_strategy_archive_for_hash`（`eloop.sh:138`）と `_find_rollback_candidate_file_for_hash`（`regression.sh:780`）が既に順次探索しているので、ここを `.py` → `.py.gz` の順に拡張する。
- Python 側（`improve.sh` / `eloop_improve.sh` のインライン Python と `find_archive_path`）は `os.path.join(dir, f"{h}.py")` の直打ちを、`.py` → `.py.gz` を順に試す共通 helper に置き換える。`.py.gz` は `gzip` で展開して読む。
- writer は**平文のまま書き続ける**（Phase B まで形式を変えない）。既存の 4,475 ファイルは Phase B で順次圧縮される。
- 後方互換: `.py` が在るときの挙動は不変。`.py.gz` のみでも読めることがテストで固定される。

### 5.2 Phase B — 圧縮 reclaim（docich control plane）

- **`storage_reclaim.sh` には追加しない**（R7）。`ops/vm_actions/` に**別の reviewed helper** を置き、必要なら専用 operation として配線する。
- フロー（各 step は dry-run 既定、`APPLY=1` でのみ変更）:
  1. 固定パス `$root/strategy_versions_archive/by_hash` のみ。glob・ネットワーク入力なし。
  2. age gate（既定 7日。最新 N 件は平文のまま残すかは owner 判断）で `*.py` を選定。
  3. `gzip -c` で `<hash>.py.gz` を**一時ファイルに**書き、`gzip -t` と展開後の SHA-256 一致を検証。
  4. 検証 OK のときだけ `<hash>.py` を削除（rename ではなく「作成→検証→削除」の順。クラッシュしても平文が残る）。
  5. **reader gate**: デプロイ済み soviet_now が `.py.gz` を読めることを確認できない場合は **何もせず中断**（R4）。ゲート判定は「deployed 側の探索経路に `.py.gz` が存在するか」の固定検査に限定し、任意 exec は使わない。
- 処理中も writer は動き続ける。`cp` 済み hash は追記されないので、圧縮対象の `<hash>.py` が同時に新規作成されることはない（既存 hash のみが対象）。

### 5.3 可観測性（本ADRで実装済み）

- `collect_diagnostics.py` の `strategy_archive` に加えて **`strategy_archive_formats`** を射出: 固定 suffix 語彙（`.py` = plain / `.py.gz` = compressed）ごとの **count と allocated_bytes のみ**。filename・path・内容は出さない。bounded scan・symlink 非追従・hardlink 単一回課金は既存 `_storage_tree_usage` と同じ規約。
- `summarize_storage.py` は `strategy_archive_{plain,compressed}_{count,bytes}` と `strategy_archive_compressed_share_pct` を公開 alert の context に追加する。`incomplete` は既存の局所的なフラグ（issue は作らない）として扱う。
- これにより「増加が有界か」は**戦略内容を読まずに**確認できる: plain_count が 0 へ収束し compressed_bytes が頭打ちになること。

## 6. 実装・ロールアウト範囲

1. **soviet_now PR（Phase A）**: reader fallback helper 化＋テスト（`.py` のみ / `.py.gz` のみ / 両方 / 壊れた `.py.gz` で fail-closed）。
2. **docich PR（本ADRで完了）**: ADR ＋ `strategy_archive_formats` の diagnostics / summary 投影 ＋ 回帰テスト。
3. **docich PR（follow-up、Phase A マージ後）**: 別 reviewed helper（圧縮）＋ operation 配線＋ failure-path テスト。`storage_reclaim.sh` は触らない。
4. **deploy**: soviet_now main → docich `games/soviet_now` gitlink bump → docich main → VM。**Phase B を有効にする前に VM 上で reader gate を実測**する。

## 7. 失敗モードとテスト計画

- **T1** reader が `.py.gz` を読めない状態で Phase B が動く → reader gate で中断、production 無変更。
- **T2** `gzip` 出力の検証失敗（展開不一致・`gzip -t` 失敗）→ 元の `<hash>.py` を残す。ディスクは減らないが内容は失われない。
- **T3** 圧縮途中の中断 → `.py.gz` の未検証一時ファイルだけが残り、平文は生存。次回は上書き再試行。
- **T4** `.py.gz` が壊れている（disk error）→ reader は fallback せず fail-closed（rollback 候補探索が「無し」と判定する側に倒す）。
- **T5** diagnostics の entry bound 到達 → `scan_complete=False` を射出し、有界性は「未検証」として扱う（silent な部分計上にしない）。
- **T6** symlink 化された `by_hash` → 追従せず `scan_complete=False`、形式別 count は 0。

## 8. セキュリティ・安全性

- 戦略内容・prompt・credential は取得・表示しない。出すのは固定 suffix 語彙ごとの count と allocated bytes のみ。
- 任意 exec は public repo では無効のまま。production 変更は owner-only control plane の fixed reviewed helper 経由に限定する。
- 権限は ubuntu（非root）。対象ディレクトリは ubuntu 所有で sudo 不要。

## 9. 決定が必要な点（owner）

1. **方式**: B（gzip + reader fallback）で確定してよいか。A（keep 上限）を選ぶ場合は seed プール枯渇の影響評価が必要。
2. **age gate**: 7日でよいか。直近 N 件を平文のまま残すか（残すと rollback の即時性は上がるが効果は下がる）。
3. **gateway stdin 予算**: 圧縮は `storage_reclaim.sh` への追加ではなく別 helper（+ 必要なら専用 operation）でよいか。それとも上限引き上げを検討するか。
4. **受入基準の言い換え**: 「世代数に上限、または十分な圧縮率」を「1ヶ月運用で `strategy_archive_plain_count` が 0 へ収束し `strategy_archive_compressed_bytes` が頭打ち」として diagnostics 実測で判定する形にしてよいか。

## 10. 本ADRの実施範囲（部分完了の明記）

このPRで実装したのは **§5.3 の可観測性のみ**。以下は未実施で、理由を明記する。

- **Phase A（soviet_now reader fallback）: 未実施。** reader は `azumag/soviet_now` 側にあり、本カードの実装契約は `azumag/docich`。reader を壊さずに圧縮へ進むには soviet_now PR が前提。
- **Phase B（圧縮 helper）: 未実施。** R7 の gateway stdin 予算と R4 の reader gate 設計が owner 判断事項（§9-3, §9-1）。helper 分割の要否が決まるまで実装しない。
- production / VM への変更、soviet_now gitlink bump、`storage_reclaim.sh` の変更: **一切行っていない**。
- 可観測性は diagnostics 投影と回帰テストで検証済み（`ops/vm_actions/tests/test_storage_breakdown_diagnostics.py`、14 tests green）。
