# Code Modernization の段階導入

Anthropic の [code-modernization](https://github.com/anthropics/claude-plugins-official/blob/main/plugins/code-modernization/README.md) を、既存仕様を維持した改善のために利用する。最初は **Understand it first（理解・評価のみ）**。プラグインの導入を理由に全体を書き換えない。

## 導入範囲と実行の区別

- `.claude/settings.json` は、このリポジトリでのプラグイン有効化を宣言する。ファイルのマージだけでは各端末へのインストールやコマンドの実行は完了しない。
- `CODE_MODERNIZATION_TELEMETRY=0` で、このプラグインの利用統計送信を無効にする。Claude Code 全体の通信・課金・既存の OpenTelemetry 設定を変更するものではない。
- プラグインには通常セッションでも呼ばれるフックがある。有効化しても移行作業や live pane を自動起動する設定は追加しない。
- 既存の `AGENTS.md`、モデル利用・委任・予算・本番操作の承認を維持する。この導入は他モデル、大量の並列エージェント、依存更新、再起動、デプロイへの包括承認ではない。
- CI、定期実行、production VM では自動実行しない。試行は承認済みの開発環境で行う。

## Claude Code へのインストール

ローカルの開発用 docich checkout で実行する。

```sh
claude plugin install code-modernization@claude-plugins-official --scope project
claude plugin list
```

公式 marketplace が未登録の端末だけ、先に `claude plugin marketplace add anthropics/claude-plugins-official` を実行する。既存セッションへの反映は `/reload-plugins`、または新しい Claude Code セッションを使う。`claude --version` と実際に読み込んだプラグインの version / scope / status を記録する。

[公式の導入説明](https://code.claude.com/docs/en/discover-plugins)に従い、`/code-modernization:modernize` が利用可能なことを確認する。Claude Code のクラウドセッションや別のエージェントで、ローカル設定からプラグインが自動的に利用できるとは扱わない。

## 最初のパイロット: 半熟英雄の繰り返し停滞判定

| 項目 | 初回の範囲 |
| --- | --- |
| 名前 | `docich-hanjuku-repetition` |
| 実装 | `src/docich/hanjuku_repetition.py` |
| 専用テスト | `tests/test_hanjuku_repetition.py` |
| 目的 | 判定ルールとテストの対応、境界条件、改善の必要性を把握する |
| 初回の停止点 | preflight / assess の結果を確認。書き換えない |
| 対象外 | エミュレータ、ROM、画面取得、Bot の起動・終了、保存・復帰、配信、VM、外部API、他リポジトリ |

このモジュールは標準ライブラリだけを import し、専用テストで判定を単体確認できる。小さな対象で手順を試すための選定であり、設計不良や書き換えの必要性を事前に断定しない。`hanjuku_run.py` 等との実機統合はこのスライスでは検証できない。

### 既存仕様として確認する観点

以下はレビュー候補であり、人によるルール承認や網羅性の証明ではない。抽出時に実際のソース行と実行済みテストへ結び付ける。

- 画面ハッシュと実際に成功した入力の周期で判定する。予定入力や同じ phase だけで停滞としない。
- 2〜24段階、最低4周、既定300秒の条件を保持する。同一トークンへの滞在を追加の周回に数えない。
- 観測の中断・pause・時計の巻き戻り・進行で過去の周期時間を引き継がない。
- 不正な記録、古い証拠、改ざんした候補、入力上限超過から終了証拠を作らない。履歴は有界に保つ。

参照: [`hanjuku_repetition.py`](../src/docich/hanjuku_repetition.py)、[`test_hanjuku_repetition.py`](../tests/test_hanjuku_repetition.py)。

### ソースと出力を分離する

最初に checkout の `AGENTS.md` と関連 Issue / PR を確認する。以下は Bash 用。既存の開発用 Python / pytest を使い、パッケージや依存を自動更新しない。

```bash
set -euo pipefail
repo="$(git rev-parse --show-toplevel)"
base="$(git -C "$repo" rev-parse HEAD)"
work="$(mktemp -d "${TMPDIR:-/tmp}/docich-modernize.XXXXXX")"
mkdir -p "$work/source" "$work/workspace" "$work/evidence"

# 選んだコミットの2ファイルだけを取り出す。未コミット変更は含めない。
git -C "$repo" archive "$base" \
  src/docich/hanjuku_repetition.py tests/test_hanjuku_repetition.py \
  > "$work/source.tar"
tar -xf "$work/source.tar" -C "$work/source"
printf '%s\n' "$base" > "$work/evidence/source-commit.txt"
git -C "$repo" ls-tree -r "$base" -- \
  src/docich/hanjuku_repetition.py tests/test_hanjuku_repetition.py \
  > "$work/evidence/source-blobs.txt"

(
  cd "$work/source"
  PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    PYTHONPATH="$work/source/src" \
    python3 -m pytest --noconftest -c /dev/null -p no:cacheprovider \
    -q tests/test_hanjuku_repetition.py \
    --junitxml="$work/evidence/baseline.xml" \
    2>&1 | tee "$work/evidence/baseline.log"
)

# 別workspaceはdocichのproject設定を継承しないので明示設定する。
cd "$work/workspace"
export CODE_MODERNIZATION_TELEMETRY=0
export PYTHONDONTWRITEBYTECODE=1
claude plugin install code-modernization@claude-plugins-official --scope local
claude plugin list
printf 'Workspace: %s\nSource: ../source\n' "$work/workspace"
claude
```

これは独立した2ファイルの **単体テスト用スライス**。`--noconftest`、`-c /dev/null`、プラグイン自動ロード停止は、リポジトリ全体の fixture や追加プラグインを持ち込まないためであり、通常CIの代わりにはしない。将来この2ファイル以外への依存が増えたら、無条件に追加せずスライスの境界とテスト手順を見直す。テスト失敗・0件・skipを成功扱いしない。

`$work` は一時領域なので、必要な証拠は消失前に承認済みの非公開保存先へ残す。全checkoutやproductionディレクトリを `--source` に渡さず、出力のあるworkspace自身をソースにしない。

### 最初のセッション

`/code-modernization:modernize` を開き、次の意図を伝える。

> 名前は docich-hanjuku-repetition。ソースは ../source。システム全体ではなく、半熟英雄の繰り返し停滞判定と専用テストだけのスライスです。Understand it first を選びます。既存挙動を変えず、初回は preflight / assess の結果確認までに限定してください。統合動作は未検証です。記録済みの単体テスト結果と実行コマンドを確認し、INTENT.md / PREFLIGHT.md に範囲・制約・未確認事項を残してください。大量並列化や次段階への実行承認は推測せず、ソース・本番・依存・モデル設定を変更しないでください。

案内された順に実行する。明示的な入口と状態確認は次のとおり。

```text
/code-modernization:modernize-preflight docich-hanjuku-repetition --source ../source
/code-modernization:modernize-status docich-hanjuku-repetition
/code-modernization:modernize-assess docich-hanjuku-repetition
```

preflightの5項目は実際の回答を記録し、不明点や端末固有のビルド可否を勝手に「確認済み」にしない。後続コマンドは記録済みの意図を読む。`REPORT.html` は `analysis/docich-hanjuku-repetition/` に作られる。

## 分析から実装へ進むときのゲート

1. **評価を確認**: 変更不要も有効な結論。必要な場合だけ map → extract-rules → review → brief へ進む。`extract-rules` は大規模対象で多数のエージェントを起動するため、対象と見積もりを提示し、既存のモデル・予算承認を確認する。
2. **計画を人が承認**: `uplift` / `transform` / `reimagine`、対象範囲、互換性、ロールバックを確定する。初回のパイロット選定やこの導入PRを、MODERNIZATION_BRIEFの承認・署名へ転用しない。
3. **変更と検証を分離**: 実装は元ソースでなく作業用出力へ。verifyは可能なら新しいセッションで行い、cleanなテスト再実行、同一入力の旧新比較、未使用の境界入力、カナリア、重要ルールと実行テストの対応を確認する。許容差や正規化する項目は理由とともに明示する。
4. **証拠と未確認を区別**: `PROVEN` 等の判定はプラグインの実際の証拠計算結果だけを報告する。モデルの自己申告やテスト件数だけでは付与しない。旧システムが実行できない場合や差分未解決を、完全同等としない。意図的な挙動変更・署名・security patchの適用は人の判断を残す。
5. **既存PRフローへ戻す**: 必要なソース・テスト差分だけを作業ブランチへ移し、自己レビュー、独立レビューの有無、CI、マージ、本番反映を別々に記録する。source SHA、plugin version、テスト、入力が変わったら古い結果を流用しない。本番操作は `AGENTS.md` の正規経路と承認の範囲内だけ。

他リポジトリへの展開は、最初のパイロットの有用性と利用量を確認してから個別PRにする。この導入で横断的な自動変更を開始しない。

## 初回の基準確認（2026-10-15 JST）

Claude Codeプラグインを起動する前の、手動での隔離単体テスト確認。GitHubから取得した2ファイルをGit blob SHA一致で照合した。Python 3.13.5、上記と同じpytest隔離オプションで実行し、件数はJUnit XMLから確認した。

| 対象 | Git blob SHA |
| --- | --- |
| `src/docich/hanjuku_repetition.py` | `cd68460300cf429d8f0108334a60057d59c48965` |
| `tests/test_hanjuku_repetition.py` | `1ae03fd5d6e7b349c74ed92c4cbae38e5ca477a8` |

現行コードは **42 passed / 0 failed / 0 errors / 0 skipped**。別コピーだけで `MIN_CYCLES = 4` を `3` に変えたカナリアは **40 passed / 2 failed** となり、`test_long_cycles_need_four_whole_repetitions` と `test_terminal_proof_is_revalidated[cycles-3]` が失敗した。元の2ファイルのblob一致は実行後も確認した。カナリア変更をこのリポジトリへ含めない。

これは既存テストの基準と一部の感度確認にすぎない。プラグインのインストール・preflight・assess・verify、旧新比較、全体CI、実機統合、独立レビュー、本番反映はこの記録では未実施。`PROVEN` 判定や人による承認・署名は付与していない。

## 証拠の公開範囲

`legacy/`、`analysis/`、`modernized/` と個人の `.claude/settings.local.json` は誤追加防止のためrootでignoreする。通常は上記のようにcheckout外で作業する。レポート、テスト入力、ログ、セキュリティ指摘を一括で `git add` しない。秘密・内部運用情報・個人情報を確認したうえで、公開可能な結果・SHA・実行コマンド・制約だけをPRや `docs/` に選んで記録する。未加工の証拠は承認済みの非公開領域に置く。
