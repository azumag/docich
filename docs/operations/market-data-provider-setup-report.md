# Market-data provider 初回セットアップ調査レポート（2026-09-16）

日本株 PAPER 用 Moomoo/OpenD と FX PAPER 用 OANDA Practice の
read-only market-data provider を production で安全に実データ取得できる状態にするための
初回セットアップ調査の記録。

> スコープ外（本調査でも未実施）: 実取引・live order・`stocks.enabled=true`・
> `fx.enabled=true`・stock selector 有効化・PAPER worker/corner/timer の有効化。

> 関連Issue: #482（日本株 live feed）, #132（FX/株 production readiness 残件）,
> #579（FX market data を OANDA REST から Windows MT5 bridge へ切替）。
> #579 により、本レポート §4 の FX 節（OANDA REST PAT 前提）は方針更新済み。FX の最新方針は #579 を正とする。

## 1. 結論（要約）

| 対象 | 結果 | ブロッカー |
|---|---|---|
| 日本株 (Moomoo/OpenD) | **実施不可** | production VM は aarch64（ARM64）。Moomoo 公式 Linux OpenD は x86-64 のみで ARM64 版が存在しない |
| FX (OANDA Practice) | **未実施** | `~/.config/docich/oanda-practice.env` が未作成（人間による Practice アカウント / PAT の準備が必要） |
| main / production 同期 | OK | 調査開始時点で production == main、以後も push deploy で追随 |

追加所見は §6・§7 を参照。production への変更は行っていない。

## 2. 前提確認（main / production）

- 調査開始時点の `main` HEAD: `9eb97238dc2788a3072e3acc08b536d685a6e976`（#561）
- production checkout `/home/ubuntu/docich` の HEAD: 同 SHA、tracked files clean
- owner-only gateway `status`（operation=status）: `status=configured`、`sha=9eb97238…`、drift なし
  → **最新 main は production へ反映済み。deploy 作業は不要**
- その後 main は #576 等で前進（main push の `VM operations` deploy により自動追随）
- 確認手段: owner-only `VM operations`（`status` / `diagnostics`）＋補助的な read-only SSH
  （メタデータ確認のみ）。production への書き込みは一切行っていない。

## 3. 日本株（Moomoo/OpenD）

### 3.1 現状（未導入）

| 項目 | 結果 |
|---|---|
| `~/.local/share/docich/moomoo-opend/` | ディレクトリ無し |
| `~/.config/docich/moomoo-opend/` | ディレクトリ無し |
| `OpenD` binary | 無し（`find` でも発見なし） |
| `Appdata.dat` | 無し |
| `OpenD.xml` | 無し |
| `.venv-trading` | 有り（`bin/python3` は `/usr/bin/python3` への symlink） |
| `moomoo` SDK import | 不可（`ModuleNotFoundError: moomoo`） |
| `docich-moomoo-opend.service` | not-found / inactive |
| `docich-market-data-stocks.service` | not-found / inactive |
| 既存 OpenD process | 無し |
| `127.0.0.1:11111` | 未 listen |

`ops/vm_actions/check_moomoo_opend_runtime.sh` は rc=31（binary 不在）で fail-closed。
A-2 以降（配置・対話ログイン・remembered login・provider 起動・readiness 実測）は未実施。

### 3.2 公式パッケージの調査

公式ドキュメント記載の正規ダウンロードのみを使用（非公式 mirror・推測 URL は不使用）。

- 公式ドキュメント: https://openapi.moomoo.com/moomoo-api-doc/jp/opend/opend-cmd.html
- 公式ダウンロードページ: https://www.moomoo.com/download/OpenAPI
- 取得した Linux 成果物（公式 CDN）:
  - `https://softwaredownload.futustatic.com/moomoo_OpenD_10.10.7008_Ubuntu18.04.tar.gz`
  - size: 466,932,458 bytes / sha256: `72eaa6e47b5cb8905306427b5e3679d591408243492e3e7acbc3a7d46f09a0aa`
  - 内容: `OpenD`（実行ファイル）, `OpenD.xml`, `AppData.dat`, 共有ライブラリ, `README.txt`
    （`OpenD.xml` は変更履歴どおり account/password 項目が削除済み。値は一切出力していない）
- 参考（Futu ブランド版、公式 `futunn.com` リンク経由）:
  - `https://softwaredownload.futunn.com/Futu_OpenD_10.10.7008_Ubuntu18.04.tar.gz`
  - sha256: `dbb8e5e73faacaad093d7bac6c3bb60efd6f29a28aa7541c494c015aaec13d8d`
  - 内容は `FutuOpenD` / `FutuOpenD.xml` / `AppData.dat`（命名が異なるだけで同じ x86-64 ビルド）

### 3.3 実行不可の根拠（プラットフォーム）

production VM は **aarch64（ARM64）**:

- `uname -m` = `aarch64`、`dpkg --print-architecture` = `arm64`
- CPU は ARM（Oracle Cloud Ampere、Neoverse-N1）
- `docs/oracle_arm_setup_guide.md` のとおり、production が ARM64 であることは既知

一方、Moomoo 公式 OpenD の Linux ビルドは **x86-64 のみ**:

- `readelf -h OpenD` → `Machine: Advanced Micro Devices X86-64`
- `/lib64/ld-linux-x86-64.so.2` が存在しない
- 実行するとカーネルが ENOEXEC を返し、シェル解釈にフォールバックして
  `./OpenD: 1: Syntax error: "(" unexpected`。`ldd` は `not a dynamic executable`
- 公式ダウンロードページ（日本語版・中国語版・英語版・GUI 版ドキュメント）の Linux 成果物は
  `..._Ubuntu18.04.tar.gz` と `..._Centos7.tar.gz` の 2 種のみで、`arm64` / `aarch64` /
  `armv8` の記載や ARM 用ダウンロード種別は存在しない
- macOS ビルドは Mach-O のため Linux では使用不可

**結論**: この production VM では OpenD を配置しても起動できず、対話ログインも実施できない。
PR #521 / #528 / #547 の provider 設計は、production のアーキテクチャ（ARM64）を
考慮していない。

### 3.4 影響

- 日本株 provider の初回セットアップは、アーキテクチャ方針が決まるまで保留。
- `stocks.enabled=false` / selector 無効を維持。実注文 capability は使用していない。

## 4. FX（OANDA Practice）

- `~/.config/docich/oanda-practice.env`: **不在**
  （`ops/vm_actions/check_oanda_practice_env.sh` は未実行。実行した場合は rc=30 相当）
- `docich-market-data-fx.service`: not-found / inactive（未インストール）
- OANDA pricing collector プロセス: 稼働なし
- 参考: `run-soren-live/market-data/market-fx-quotes.json` に、以前の file-feed 検証で
  seed された合成 test feed（`source=owner-approved-test-feed`）が残存。OANDA 由来ではない。

必要な人間操作（旧・OANDA REST 前提）: Practice アカウントで Personal Access Token を発行し、
VM 上で `~/.config/docich/oanda-practice.env`（mode 0600、`DOCICH_OANDA_ACCOUNT_ID` と
`DOCICH_OANDA_TOKEN` を各 1 つ）を作成する。値は GitHub / Issue / PR / ログ / チャットへ出さない。

> **方針更新（#579, 2026-09-16）**: FX の実市場データ経路は、OANDA Japan REST API / PAT ではなく
> **Windows 上の OANDA MT5 Demo + MetaTrader5 Python API を market-data host とする MT5 bridge 構成**
> へ切り替える。理由は OANDA Japan REST API の利用条件（read-only pricing/Practice でも対象）が重いこと。
> #525 / #528 / #549 の OANDA REST collector は main に入っているが、新しい本番 FX market-data path としては
> **採用しない（当面は無効のまま残す）**。したがって本節の credential 設定は現行の次アクションではない。
> MT5 bridge の境界・実装ステップ・完了条件は #579 を正とする。

## 5. diagnostics のカバレッジ

owner-only `VM operations` の `diagnostics`（`ops/vm_actions/collect_diagnostics.py`）は、
`market_paper.{stocks,fx}` で **PAPER worker unit**（`docich-market-worker@<market>.service`）と
worker health/report のみを返す。

- provider unit（`docich-market-data-stocks.service` / `docich-market-data-fx.service` /
  `docich-moomoo-opend.service`）の active/enabled や provider health / quote 鮮度は返さない。
- このため provider の状態確認は read-only SSH のファイルメタデータで代替した。
- 恒久的には provider unit の状態と provider-health / quote 鮮度を sanitized に含める拡張が有用
  （別 PR 候補）。

## 6. 追加所見

1. **PAPER worker unit が active/enabled**（`docich-market-worker@stocks.service` と `@fx.service`）。
   ただし `config/market-paper.toml` は `enabled=false` のため、`Runtime.tick()` は即
   `{"status": "disabled"}` を返し health を新規作成しない（無害な無操作）。
   「worker/PAPER はまだ disabled」という想定とはずれるが、本調査では変更していない。
2. **データファイル名の不一致**: 公式パッケージのデータファイルは `AppData.dat`（大文字 D）だが、
   repo の preflight `check_moomoo_opend_runtime.sh` と `docs/operations/moomoo-opend.md` は
   `Appdata.dat`（小文字 d）を要求する。x86 環境で動かす場合も名称の解消が必要。
3. `OpenD.xml` は変更履歴どおり account/password 設定が削除されたテンプレート（値は未出力）。

## 7. 安全確認（不変）

- `stocks.enabled=false` 確認: yes
- `stocks.selector` 無効（`enabled` 未記載 = false）: yes
- `fx.enabled=false` 確認: yes
- 実注文 capability の使用: no
- secret の logs / GitHub / チャットへの露出: no
- production 設定変更: なし。ダウンロードした OpenD パッケージと作業ディレクトリは削除済み
  （ディスク使用率は元のまま）。

## 8. 選択肢と次アクション

日本株 provider（いずれも要判断。production 構成を勝手に変更しない）:

1. 公式 ARM64 Linux OpenD を使う — 現時点で公式チャネルに存在しない。
2. x86-64 OpenD を `qemu-user` / `binfmt` でエミュレート実行 — 未レビューの新依存
   （性能・安定性・ライセンス/ToS）で要レビュー・要承認。
3. 専用の x86_64 ホストで OpenD を常駐させ docich VM から接続 — 現設計は
   「同一 VM・`127.0.0.1` のみ・loopback 完結」のため、セキュリティ境界の設計変更が必要。
4. 日本株 provider はこの VM では保留（`stocks.enabled=false` 維持）し、FX を先行。

FX provider:

- 方針更新（#579）: OANDA REST collector（#525/#528/#549）は新しい本番 FX path としては採用せず、
  当面は無効のまま残す。
- 次アクションは #579 の MT5 bridge 実装（Windows MT5 Demo host + Tailscale-only + read-only bridge →
  provider-neutral `market-fx-quotes.json` → 既存 FX PAPER worker）。`fx.enabled=true` は
  証跡が揃った後の別レビュー。

共通:

- `Appdata.dat` / `AppData.dat` 名称の設計修正（x86 で動かす場合に必須）。
- provider 状態を owner-only diagnostics に含める拡張。
