# ADR 0006: opencode session ファイルストアの age-based pruning（設計のみ・apply なし）

- Status: **Proposed**（design-only。本ADRは production / VM を変更しない）
- Issue: [azumag/docich#3140](https://github.com/azumag/docich/issues/3140)（#389 follow-up）
- Precedents: [ADR 0002](./0002-opencode-db-retention.md)（opencode.db bounded retention、writer exclusion gate 付き）、#1906（`opencode.log` logrotate・`tool-output` 除外、完了）
- Related lanes: #3109 / #3120 / #3121（`github-vm-ops` state 側の retention。本ADRの対象外）

## 0. 前提とスコープ

- **本ADRは設計・レビューのみ**。session 削除の apply（データ削除領域）は含まず、設計確定後に owner 確認を 1 回取ってから別途実施する（#3140 の条件どおり）。
- prompt・AI入出力・credential・ファイル本文は取得・表示しない。メタデータ（件数・サイズ・mtime・行数）のみ。
- DSH 追加条件 3 件を本ADRの必須構成要素とする:
  1. サブパス単位のベースライン実測
  2. monitor で観測可能な成功指標（`opencode_default_total` の減少＋増加停止）
  3. 既存 retention timer との整合説明

## 1. As-Is ベースライン（VM 実測・DSH条件1）

### 1.1 対象ストアと monitor 指標の対応

- 対象: 本番 VM の `~/.local/share/opencode/`（以降「opencode ストア」）。`~/.config/opencode` や worker ツリー（`soren/tmp/state/xdg_data/opencode/`）は別ツリーとして扱う。
- monitor 指標: `opencode_default_total` = `_storage_tree_usage(default_db.parent)`（`ops/vm_actions/collect_diagnostics.py`）が `allocated_bytes`（`st_blocks*512`、du と同値）と `count`（エントリ数）を返す。`summarize_storage.py` の `TREE_CATEGORIES` に含まれ、monitor から時系列観測可能。
- DB 部分は `opencode_default` family（db/wal/shm）として個別に観測される。

### 1.2 実測ベースライン（VM diagnostics スナップショットより）

| 時刻 (UTC) | `opencode_default_total` | 備考 |
|---|---|---|
| 2026-10-30 08:59Z | **2,342,293,504 B / 97,627 files** | DSH production 実測（正本） |
| 2026-10-30 21:17Z | **2,463,748,096 B** | 同。増加率 **+0.4GiB/日** |

- **実測の読み**: `~/.local/share/opencode/` ツリー全体は **約 2.3GiB・約 9.8万ファイル**で、**+0.4GiB/日**の増加傾斜にある。opencode.db 本体は既存 timer（hourly、`retention_days=1`）で ~60–107MiB に bounded 化済みのため、増加分のほぼ全量は **非 DB の session ファイル群**（`snapshot/` の session ごとの git object ストアと `storage/` の session_diff 等）である。
- 補助スナップショット（10-27、ローカル退避の diagnostics 抜粋）: ~1.0GiB / ~7–10k ent。DB prune 適用前で DB が支配していた時期。file entries はそこから 97k へ増えており、**エントリ数ベースでも増加が継続**している。
- worker ツリー `soren/tmp/state/xdg_data/opencode/` = 20.9MiB / 7 ent（10-31 時点の抜粋値）。小さいが同構造のため対称扱いを検討対象とする。
- 注: 本ADR初稿で参照したローカル退避 diagnostics には上位桁がマスクされた値が含まれており、production 実測と読み違いが生じた。ベースラインは上記 DSH production 系列を正本とする。

### 1.3 「2.29GiB」報告値との突き合わせ（VM 実測で確定）

- ops 報告の「session ファイルストア 2.29GiB・+0.3GiB/日」は、DSH の production 系列（10-30 08:59Z 2,342,293,504 B / 97,627 files、10-30 21:17Z 2,463,748,096 B、+0.4GiB/日）と同一ストア、つまり **`~/.local/share/opencode/` ツリー全体である**と確定した。
- `gateway_state`（`~/.local/state/github-vm-ops`）は同時期に約 2.26–2.46GiB を観測されるが、**サイズが偶然近いだけの別系統**であり、本ストアではない。`github-vm-ops` 側の retention は #3109・#3120/#3121 の keep=2→1 レーンで、**本ADRの対象外**とする。
- 初稿ではマスク済みの抜粋値を読み違え「gateway_state が最も合致」と記したが、DSH の指摘により上記のとおり訂正する。

### 1.4 サブパス列挙（対象ツリーの構造）

`~/.local/share/opencode/` 配下（レイアウトは opencode 1.19 系で確認）:

| サブパス | 内容 | 現行 retention | 本設計での扱い |
|---|---|---|---|
| `opencode.db` (+`-wal`,`-shm`) | session/event/part/message SQLite | **有**: `docich-opencode-retention.timer`（hourly、retention_days=1、transactional prune + sparse reclaim） | **対象外**（既存 timer がカバー） |
| `snapshot/` | session ごとの git object ストア（作業ツリーの snapshot） | なし | **prune 対象** |
| `storage/` | `session_diff/*.json`、migration マーカ等の session ファイル | なし | **prune 対象** |
| `tool-output/` | tool 実行出力 | #1906 で意図的除外（reader 未特定） | **除外**（方針維持） |
| `log/` | opencode.log | #1906 logrotate（size 50M・rotate 3・copytruncate） | **除外**（logrotate がカバー） |
| `repos/` `shell/` `auth.json` `*.log` 他 | repo キャッシュ・shell・資格情報 | — | **除外**（auth.json は資格情報のため触らない） |

- 現在の diagnostics は `opencode_default_total`（ツリー合計）のみを出し、**サブパス別の内訳は未観測**。DSH条件(1)のため、§4.1 の測定機構追加が先行必須。

## 2. 問題

- `snapshot/` と `storage/` には保持期間が存在せず、`opencode run` のたびに session あたり数十〜数百ファイル（snapshot git objects）を追記する。VM の producer（radio_main / radio_prepass / comment / improvement / soren91、attribution で 24h 700 sessions 観測）は過去 session を再利用しない（ADR 0002 §1.3 と同じ前提: `--session`/`--continue`/export 参照なし）ため、**cutoff を超えた session のファイル群は参照されない**。
- 既存 `docich-opencode-retention.timer` は opencode.db のみを対象とし、ファイル群は無制限のまま → `opencode_default_total` の count は増加し続ける（実測どおり）。
- ADR 0002 の gate（`flock` 共有/排他）が既に producer 側に配線済みで、ファイル prune にもそのまま再利用できる。

## 3. 要件

- **R1 (exclusion)**: prune の mutation window 中、新規 `opencode run` が対象ツリーに書き込まない。既存 `OPENCODE_ROTATION_GATE`（`flock`）を再利用する。
- **R2 (no live victim)**: cutoff 未満・実行中 session のファイルは消さない。mtime による cutoff と gate の二重防御。
- **R3 (fail-closed)**: gate 取得失敗・想定外レイアウト・測定失敗時は変更せず中断。
- **R4 (no new privilege)**: ubuntu（非 root）・既存 reviewed-helper 経路のみ。`auth.json` には触れない。
- **R5 (observability)**: prune の結果（削除件数・回収バイト・サブパス別残量）を diagnostics に記録し monitor で追跡できる。
- **R6 (idempotent)**: 中断・再実行しても安全（unlink ベース、途中停止で partial でも不整合を残さない）。

## 4. 設計

### 4.1 サブパスベースライン測定（DSH条件1の具体化・先行必須）

- `collect_diagnostics.py` に opencode ツリー用の固定トップレベル分類を追加する（`_classify_sys_tmp_top_level` と同型の fixed-subpath 分類: `snapshot` / `storage` / `tool-output` / `log` / `repos` / `shell` / `db-family` / `other` × `{count, allocated_bytes}`）。
- `summarize_storage.py` の固定キーにも対応するサブパス指標を追加し、monitor が増加先のサブパスを識別できるようにする。
- **これが最初の成果物**: apply 前に一度デプロイし、サブパス別の正確な実測を baseline として記録する（§1.3 の gateway_state 対応の最終確認もここで行う）。

### 4.2 prune ルール

- 対象: `snapshot/<hash>/` ディレクトリ、`storage/` 配下の `session_diff/*.json` 等の session 由来ファイル。
- 条件: **サブパス内の全エントリの mtime が `now − cutoff` より古い**こと。cutoff の既定は `retention_days=1`（24h、既存 DB cutoff と揃える。延長案は §6 の owner 決定待ち）。
- 除外: `tool-output/`・`log/`・`auth.json`・`repos/`・`shell/`・`migration` マーカ・cutoff 未満のもの。symlink は辿らず対象外扱い。
- 実施: 既存の排他 rotation gate（`flock -x`）を保持したうえで unlink。取得できない場合は変更せず中断（R3）。削除は件数・バイト数を記録するだけで内容は読まない。

### 4.3 既存 retention timer との整合（DSH条件3）

- 既存 `docich-opencode-retention.timer`（hourly oneshot、`opencode_db_retention_now.sh --scheduled`、deadline 780s）は `opencode.db` と worker DB のみを対象とし、本ファイル prune と**対象が非重複**。
- **推奨**: ファイル prune を同一の reviewed helper 起動内・同一の排他 gate 保持の下で DB pass の直後に実行する（timer unit は維持、helper の対象範囲を拡張）。1 つの mutation window・1 つのスケジュールに集約し、timer 競合を構造的に発生させない。
- 代替案: 別 timer（`docich-opencode-file-retention.timer`）を追加する方式も可能だが、同一 gate を共有する限り実害は小さい。同一 window 集約の方が「writer exclusion の証明責任」が 1 箇所で済むため推奨側に置く。
- gate の互換性: producer（soviet_now 側 `opencode run` 全呼出点）は既に共有ロック保持済み（ADR 0002 配線済み）のため、ファイル prune も自動的に exclusion される。追加の producer 変更は不要。

### 4.4 成功指標（DSH条件2・monitor 観測可能）

- `opencode_default_total`: prune 適用後に `allocated_bytes` が減少し、その後の増加傾斜（slope）が ~0 に収束することを monitor で確認する。現行は +250MB/日級の傾斜を観測（Oct 27 実測）。
- 追加するサブパス指標（§4.1）で増加先が `snapshot`/`storage` に限定され bounded 化されたことを示す。
- `opencode_retention` diagnostics に DB pass と同形状の結果（`deleted_entries`, `reclaimed_bytes`, `skipped`, `stage`, `reason`）を残す。

## 5. 失敗モードとテスト計画

- **T1** gate 取得 timeout → 変更なしで中断、diagnostics に `reason=gate_timeout` を記録。
- **T2** cutoff 未満のファイルを含む → 当該サブパス/ディレクトリはスキップ（session 単位で判断）。
- **T3** 想定外レイアウト（未知のサブパス・symlink）→ fail-closed でスキップし `reason=unexpected_layout`。
- **T4** 中断・再実行 → unlink は冪等、partial 削除は次回 pass で回収。
- **T5** `tool-output/`・`auth.json` 等の除外パスに一切触れないことを wiring test で固定（#1906 の `test_tool_output_is_never_reclaimed` 先例と同型）。
- **T6** dry-run（count-only）モードで削除件数・バイト見積を記録し apply と差分がないこと。

## 6. owner 決定待ち

1. **cutoff**: DB cutoff と同じ `retention_days=1`（24h）でよいか、より長い保持（例 7d）が必要か。
2. **timer 集約**: 同一 helper/window 集約（推奨）か、別 timer か。
3. **worker ツリー**（`soren/tmp/state/xdg_data/opencode/`、現状 20.9MiB）への対称適用の要否。
4. **`tool-output/` の恒久除外確認**（#1906 の決定を維持するか）。
5. 設計確定後、session 削除の **apply 実施可否**（本ADRでは実施しない）。

## 7. ロールアウト

1. **docich PR（本PR）**: 本ADR + §4.1 のサブパス測定（`collect_diagnostics`/`summarize_storage` の固定キー追加）とテスト。
2. **baseline 取得**: 測定のみ先行デプロイ → monitor でサブパス別実測を記録。
3. **soren/soviet_now PR**: ファイル prune helper（`lib/` の reviewed helper、DB retention と同じ gate 契約・同じ結果フォーマット）+ failure-path テスト。
4. **deploy → dry-run → apply（owner 承認後）**: 既存 timer の同一 window で実行。

## 8. セキュリティ・安全性

- session ファイル・prompt・tool 出力・`auth.json` の内容は一切取得・表示しない（件数・バイト・mtime のみ）。
- 任意 exec なし。fixed reviewed helper 経路のみ。非 root（ubuntu）。
- apply は本ADRのスコープ外。owner 承認待ちゲートを設計内に分離済み。
