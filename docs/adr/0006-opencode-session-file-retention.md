# ADR 0006: opencode session ファイルストアの age-based pruning（auto-apply 有効化済み・owner 承認 #3140 comment 6635080145）
- Status: **Accepted**（2026-11-02 DSH レビュー。設計確定・auto-apply 有効化済み（owner 承認 #3140 comment 6635080145））
- Issue: [azumag/docich#3140](https://github.com/azumag/docich/issues/3140)（#389 follow-up）
- Precedents: [ADR 0002](./0002-opencode-db-retention.md)（opencode.db bounded retention、writer exclusion gate 付き）、#1906（`opencode.log` logrotate・`tool-output` 除外、完了）
- Related lanes: #3109 / #3120 / #3121（`github-vm-ops` state 側の retention。本ADRの対象外）

## 0. 前提とスコープ

- 本ADRは設計と dry-run 配線までを対象とする。session 削除の apply（データ削除領域）は含まず、owner 確認を 1 回取ってから別途実施する（#3140 の条件どおり）。自動 apply も既定 OFF で、有効化は owner ゲート。
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
- worker ツリー `soren/tmp/state/xdg_data/opencode/` = 20.9MiB / 7 ent（10-31 時点の抜粋値）。小さいが同構造のため対称扱いを検討対象とする（DSH 判断: 対称適用は据え置き）。
- **サブパス実測（#3141 マージ後の production diagnostics、DSH 実測値 2026-10-31 06:02Z）**:
  - `snapshot/` = **1.98GiB / 97,352 files** — session ごとの git object ストアが増分の支配的要素
  - `tool-output/` = 50.6MiB（除外方針維持・対象外）
  - `log/` = 12.2MiB（logrotate 済み・対象外）
  - `storage/` = 不在（session_diff 等は未生成または空）
  - `opencode.db` = 55.7MB（既存 timer で bounded 維持）
  - ツリー全体 ≈ 2.3GiB のほぼ全量が `snapshot/` に集中していることを確認。
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

- diagnostics は `opencode_default_total`（ツリー合計）に加え、`opencode_default_{snapshot,storage,tool_output,log,other}` のサブパス別 allocated bytes / count を出す（#3141 と本改訂）。`other` は既知サブパスと db family の残差。

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
- 実装状況: `snapshot` / `storage` / `tool-output` / `log` は #3141、`other` は本改訂で追加済み。`other` は total −（既知 4 サブパス + db family）で、未知サブパスの検知用。

### 4.2 prune ルール

- 対象: `snapshot/<hash>/` ディレクトリ、`storage/` 配下の `session_diff/*.json` 等の session 由来ファイル。
- 条件: **サブパス内の全エントリの mtime が `now − cutoff` より古い**こと。cutoff の既定は **24h**（DB cutoff `retention_days=1` と整合。§6 で確定）。
- 除外: `tool-output/`・`log/`・`auth.json`・`repos/`・`shell/`・`migration` マーカ・cutoff 未満のもの。symlink は辿らず対象外扱い。
- 実施: 既存の排他 rotation gate（`flock -x`）を保持したうえで unlink。取得できない場合は変更せず中断（R3）。削除は件数・バイト数を記録するだけで内容は読まない。

### 4.3 既存 retention timer との整合（DSH条件3）

- 既存 `docich-opencode-retention.timer`（hourly oneshot、`opencode_db_retention_now.sh --scheduled`、deadline 780s）は `opencode.db` と worker DB のみを対象とし、本ファイル prune と**対象が非重複**。
- **確定（DSH 推奨 ②）**: ファイル prune を DB pass と同じ hourly スケジュール・同じ排他 gate（`OPENCODE_ROTATION_GATE`、`flock -x`）に集約し、別 timer は作らない。
- **配線方式（暫定・2026-11-02）**: soren 側 `lib/opencode_db_retention.{sh,py}` へ file pass を足す形が筋だが、soren レーンを本 PR に持ち込まないため、当面は `ops/vm_actions/opencode_db_retention_now.sh` が DB rotate の直後に `opencode_session_file_reclaim.py` を呼ぶ。**2 つの連続した mutation window**（同一 hourly スケジュール・同一 gate ファイル）であり、1 window 集約ではない点が暫定。将来 soren 側へ集約する。
- owner 承認（#3140 comment 6635080145）に基づき **auto-apply** を配線する（`--auto-apply` と `--manifest-file` を渡す）。DB pass と file pass は独立 stage として扱い、片方の失敗でもう片方を止めない。DB pass の 75（deferred）はそのまま維持し、file pass の `gate_timeout` も 75 として deferred に扱う。
- gate の互換性: producer（soviet_now 側 `opencode run` 全呼出点）は既に共有ロック保持済み（ADR 0002 配線済み）のため、ファイル prune も自動的に exclusion される。追加の producer 変更は不要。

### 4.4 成功指標（DSH条件2・monitor 観測可能）

- `opencode_default_total`: prune 適用後に `allocated_bytes` が減少し、その後の増加傾斜（slope）が ~0 に収束することを monitor で確認する。現行は +250MB/日級の傾斜を観測（Oct 27 実測）。
- 追加するサブパス指標（§4.1）で増加先が `snapshot`/`storage` に限定され bounded 化されたことを示す。
- `opencode_default_other`（total − 既知サブパス − db family）を追加し、opencode upgrade で未知のサブパスが静かに増えることを検知する。これは自動 apply 有効化の前提条件とする。
- `opencode_retention` diagnostics に DB pass と同形状の結果（`deleted_entries`, `reclaimed_bytes`, `skipped`, `stage`, `reason`）を残す。
- 削減効果の報告は diagnostics の **allocated bytes**（`summarize_storage` 経由）を正とする。helper の `bytes_removed` は `st_size`（見かけ）基準の参考値。

### 4.5 2-pass continuity（自動 apply・既定 OFF）

手動 apply の「レビュー済み plan の sha256 必須」は維持する。定期運用では plan が毎回変わるため、審査対象を「毎回の plan」から「レビュー済み policy（helper・固定対象・除外・cutoff・batch・gate）＋ 2 pass 連続性の機械検証」へ移す。

- 前回 pass の plan（path + 全 stamp + bytes）を mode 0600 の state file に保存する。**opencode ストアの外**に置き、scan 対象に混入させない。
- 自動 apply は「前回 plan ∩ 今回 plan ∩ stamp（dev/ino/size/mtime_ns/ctime_ns）完全一致 ∩ 今回も cutoff 超」のみ。stamp が変わったエントリは skip する。
- state file の欠落・symlink・非 regular・nlink>1・mode が 0600 以外・所有者不一致・不正 JSON・cutoff/ストア不一致は、**自動 apply 全体を中断**する（fail-closed）。
- 自動 apply した全エントリを VM-private の manifest（path / bytes / mtime_ns、0600）に追記する。
- 自動 apply は `--auto-apply` 指定時のみ。既定は dry-run で、state を温めるだけ。初回 backlog は (a) 人手 digest apply、定常は (b) 自動 apply の 2 段構えとする。

## 5. 失敗モードとテスト計画

- **T1** gate 取得 timeout → 変更なしで中断、diagnostics に `reason=gate_timeout` を記録。
- **T2** cutoff 未満のファイルを含む → 当該サブパス/ディレクトリはスキップ（session 単位で判断）。
- **T3** 想定外レイアウト（未知のサブパス・symlink）→ fail-closed でスキップし `reason=unexpected_layout`。
- **T4** 中断・再実行 → unlink は冪等、partial 削除は次回 pass で回収。
- **T5** `tool-output/`・`auth.json` 等の除外パスに一切触れないことを wiring test で固定（#1906 の `test_tool_output_is_never_reclaimed` 先例と同型）。
- **T6** dry-run（count-only）モードで削除件数・バイト見積を記録し apply と差分がないこと。
- **T7** 2-pass continuity: 前回 plan と stamp が一致するエントリだけ適用し、stamp 変化・前回 plan 非搭載は skip する。
- **T8** state file の欠落・不正 JSON・symlink・mode 0644 は、削除前に自動 apply 全体を中断する。
- **T9** hourly 配線は `--apply` / `--auto-apply` を渡さず、dry-run のままであることを wiring test で固定する。

## 6. 確定事項（2026-11-02 DSH レビュー）

当初の owner 確認事項は、2026-11-02 の DSH レビューで次のとおり確定した。

1. **cutoff**: **24h を既定**として承認。DB retention（`retention_days=1`）と一致し、24h 超のファイルは DB 行が消えた orphan なので保持を延ばす価値がない。30/60/90 日は不採用（定常 9〜36GiB は 97% 到達実績のある FS で成立しない）。
2. **72h は上限**: helper の `--cutoff-hours` で指定可能な上限としてのみ文書化する。scheduled 配線は 24h 固定で、72h を恒久設定にしない。owner が使う場合は都度明示指定する。
3. **timer 集約**: 同一 hourly スケジュール・同一 gate ファイル。soren 側 helper への集約が最終形で、docich wrapper からの 2 window 連続呼び出しは暫定（§4.3）。
4. **worker ツリー**: 対称適用は据え置き（既定ツリーのみ対象）。
5. **`tool-output/`**: #1906 方針どおり除外維持。
6. **apply ゲート**: 初回 backlog は人手 digest apply、定常は 2-pass continuity の自動 apply、いずれも owner 確認後に有効化する。本 ADR と配線 PR では apply を実行しない。
7. **不可逆性**: session ファイル削除の不可逆性は、apply 実施時の owner 確認 1 回で受容する（content バックアップは行わない。manifest のみ残す）。

## 7. ロールアウト

1. **docich PR #3141**: 本 ADR + §4.1 のサブパス測定（merged）。
2. **docich PR #3145**: bounded reclaim helper + テスト（merged）。
3. **docich 配線 PR（本改訂）**: hourly unit への **dry-run のみ**の配線、`opencode_default_other` 指標、README、本 ADR の Accepted 化と §6 反映、2-pass state 機構のテスト。
4. **baseline 取得**: dry-run 配線を deploy し、monitor でサブパス別実測と state の生成を確認する。
5. **初回 apply（owner 承認後・別作業）**: dry-run の `plan_sha256` を owner がレビューし、`--expect-plan` で人手 apply。
6. **自動 apply 有効化（owner 承認後・別作業）**: `opencode_default_other` の監視を前提に、`--auto-apply` を unit に追加する。

## 8. セキュリティ・安全性

- session ファイル・prompt・tool 出力・`auth.json` の内容は一切取得・表示しない（件数・バイト・mtime のみ）。
- 任意 exec なし。fixed reviewed helper 経路のみ。非 root（ubuntu）。
- apply の実行は owner ゲートで分離し、配線 PR は dry-run のみ。state / manifest は mode 0600 でストア外に置く。
