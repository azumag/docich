# 半熟英雄スピードランのログを Cloudflare R2 へ退避する手順

- 対象: オーナー（R2 バケットと API トークンを作る人）
- 実装: `src/docich/hanjuku_speedrun/rta/archive.py`（送信）と `sigv4.py`（署名）
- 前提: 記録そのものは PR4a の spool が行う。送信は試走が終わった後だけ。
- この文書に実際の資格情報を書かない。リポジトリ・ログ・Issue・Actions にも置かない（`AGENTS.md` §3）。

## 1. 全体像

```
試走中の BOT
  └─ attempts/<attempt_id>/spool/ に判断ログ・観測トレース・画像を貯める
       （spool の上限は DOCICH_SPEEDRUN_SPOOL_MAX_BYTES、既定 1 GiB）

試走終了（game_switch の片付け）
  └─ finalize_logs が manifest.json / segments.json を作る
       └─ 切り離したプロセスで archive upload --pending を起動

切り離した archive プロセス
  ├─ spool を実ファイルとして再走査（manifest の一覧は信用しない）
  ├─ 1 ファイルずつ SigV4 署名付き PUT
  ├─ HEAD でサイズを確認
  ├─ 一致したファイルだけローカルから削除
  ├─ manifest.json / attempts.jsonl に archived を記録
  └─ PB・区間ベストの試走は keep/ にもコピー（best-effort）
```

ゲーム・配信・次のコーナーは送信を待たない。R2 が未設定なら何も送らず、試走とタイマーは普通に動く。

## 2. バケットを作る

Cloudflare ダッシュボード → R2 → **Create bucket**。

- 名前の例: `docich-hanjuku-speedrun`
- ロケーション: 既定（自動）でよい
- 公開アクセス: 不要（有効にしない）

このバケットはスピードランのログ専用にする。他の用途のバケットと混ぜない。

> ダッシュボードの項目名・配置は変わりうる。この文書の文言と画面が違うときは、
> 画面の現在の表示を確認して読み替える（この手順書は項目名を固定しない）。

## 3. バケット限定の API トークンを作る

R2 → **Manage R2 API Tokens** → **Create API Token**。

- Permissions: **Object Read & Write**
- Specify bucket: 手順 2 で作ったバケット**だけ**を選ぶ
- TTL: 必要なら設定する（期限切れ時は作り直して `r2.env` を更新）
- 作成直後にだけ表示される **Access Key ID** と **Secret Access Key** を控える。
  画面を閉じると Secret は再表示されない。

アカウント全体のトークンや、他のバケットも読めるトークンは使わない。
作成画面の項目名も変わりうるので、実際の画面で Permissions とバケット限定を確認する。

## 4. VM に資格情報を置く

VM の `/home/ubuntu` で、オーナーだけが読めるファイルを作る。

```sh
install -d -m 700 ~/.config/docich
install -m 600 /dev/null ~/.config/docich/r2.env
${EDITOR:-vi} ~/.config/docich/r2.env
```

書式（`<...>` はプレースホルダ。実値は書かない）:

```sh
R2_ACCOUNT_ID=<Cloudflare のアカウント ID>
R2_ACCESS_KEY_ID=<手順 3 の Access Key ID>
R2_SECRET_ACCESS_KEY=<手順 3 の Secret Access Key>
R2_BUCKET=docich-hanjuku-speedrun
```

- ファイルは `KEY=VALUE` の行だけにする。`export` を付けない。値の後ろに
  `# コメント` を書かない（行全体が `#` で始まる行は読み飛ばされるが、行末コメントは
  値の一部として扱われる）。
- `R2_ENDPOINT` は通常は不要。未設定なら
  `https://<R2_ACCOUNT_ID>.r2.cloudflarestorage.com` を使う。
- EU など別の管轄・独自ドメインを使う場合は、`R2_ENDPOINT=https://...` を
  **通常の KEY=VALUE 行として**追加する。テスト用に `http://` を許すのは、
  127.0.0.1 / localhost を明示したときだけ。それ以外の平文 HTTP は拒否される。
- パーサは `KEY=VALUE` 行だけを読む。シェルとして評価しない。値の前後の引用符は外すが、
  変数展開・コマンド置換はしない。
- ファイルのパスは `DOCICH_R2_ENV_FILE` で上書きできる（テストや別配置用）。
- **権限が 600 より緩い場合（グループ・その他に読み取り/書き込みがある場合）は使わずに拒否する。**
  ログには `insecure_permissions` とだけ出る。`chmod 600 ~/.config/docich/r2.env` で直す。
- ファイルが無い・必要な値が欠けている場合は「未設定」として扱い、何も送らない（正常終了）。

値は絶対に次へ書かない: リポジトリ、`handoff.md`、Issue / PR、Actions のログ、配信画面、
`manifest.json`、`archive_state.json`、シェルの履歴。

## 5. 疎通確認

リポジトリのルートで（`bin/docich` から起動する場合を除き、`src` を import パスに入れる）:

```sh
PYTHONPATH=src python3 -m docich.hanjuku_speedrun.rta.archive status
```

試走データの場所が既定と違うときは、トップレベルの `--data-dir` で指定する
（`status` の後ろではなく、サブコマンドの前）:

```sh
PYTHONPATH=src python3 -m docich.hanjuku_speedrun.rta.archive \
  --data-dir /path/to/run-soren-live/speedrun/hanjuku-speedrun status
```

`--data-dir` を省略した場合は、`DOCICH_SPEEDRUN_DATA_DIR`（データディレクトリ全体）、
次に `DOCICH_STATE_DIR`（`run-soren-live` など。`/speedrun/hanjuku-speedrun` を足す）、
最後に checkout の `run-soren-live` / `run` の順で探す。runtime ディレクトリ
（`.../runtimes/<id>`）を渡しても同じデータディレクトリに正規化される。

JSON が 1 行で出る。キーは固定:

```json
{"archived":0,"config":"ok","configured":true,"failed":0,"locked":false,"pending":0,"status":"ok"}
```

- `configured`: 資格情報が揃っていれば `true`
- `config` / `status`: `ok` / `unconfigured` / `insecure_permissions` /
  `invalid_env` / `invalid_config` / `invalid_endpoint` / `insecure_endpoint`
  （ファイルが無い・読めない場合は `unconfigured` として正常終了する）
- `pending`: まだ送れていない終了済みの試走数
- `archived`: 送信済みの試走数
- `failed`: `archive_state.json` にエラーが残っている試走数
- `locked`: 別の archive プロセスが動いていれば `true`

`status` は送信しない。設定が `ok` なら、次の試走終了時に自動で送られる。
送信内容だけを確認したいときは `upload --attempt <id> --dry-run` を使う。計画 JSON を
標準出力へ出すだけで、ファイルもロックも削除・変更しない（`archive_plan.json` は作らない）。
手で送り直すときは（データの場所が既定と違うときは `status` と同じく
サブコマンドの前に `--data-dir` を付ける）:

```sh
PYTHONPATH=src python3 -m docich.hanjuku_speedrun.rta.archive \
  --data-dir /path/to/run-soren-live/speedrun/hanjuku-speedrun upload --pending
PYTHONPATH=src python3 -m docich.hanjuku_speedrun.rta.archive \
  --data-dir /path/to/run-soren-live/speedrun/hanjuku-speedrun \
  upload --attempt a0001-g1-abcdef
```

終了コードは固定: `0` 正常（未設定・ロック中を含む）/ `1` 失敗あり / `2` 引数不正。
出力は固定コードと数値だけ。

## 6. ライフサイクル規則と保持

R2 のバケット → **Settings** → **Object lifecycle rules** で次を作る。

1. `hanjuku-speedrun/attempts/` を **90 日**で削除する。
   - プレフィックス: `hanjuku-speedrun/attempts/`
   - アクション: Delete objects
   - 日数: 90
2. `keep/` は消さない。`keep/` を対象にする削除規則を作らない。

**R2 側の「直近 20 試走だけ残す」整理は実装していない。** R2 に残る `attempts/` の
保持を決めるのは上のライフサイクル規則（90 日）だけである。`keep/` は PB・区間ベストの
コピー置き場として無期限に残す（ライフサイクル規則の対象にしない）。VM 側の
「直近 3 試走の manifest / segments だけ残す」整理は別物で、R2 の削除には影響しない。

VM に残るのは `records.json`、`attempts.jsonl`、直近 3 試走の `manifest.json` と
`segments.json` だけ。送信済みの古い試走の manifest / segments はローカルから消える
（R2 には残る）。spool の中身は送信・検証後に消える。ただし **まだ `archived` でない
古い試走**は、再送に必要な manifest / segments / spool をこの整理では消さない。

試走全体の合計上限は `DOCICH_SPEEDRUN_ATTEMPTS_MAX_BYTES`（既定 4 GiB）。
超えた場合、**まだ送れていない古い試走**の spool から順に削除する。manifest と
segments は残す。削除量は各試走の `archive_state.json` と `manifest.json` の
`dropped_for_cap` に記録する。現在走っている試走（`timer.json` が `running`）は消さない。
1 試走あたりの spool 上限は既存の `DOCICH_SPEEDRUN_SPOOL_MAX_BYTES`（既定 1 GiB）で、
そちらは PR4a の `enforce_cap` が担当する。

## 7. 料金の確認先

Cloudflare R2 の料金は公式ページで確認する（2026 年時点の目安: 保存 10 GB/月まで無料、
Class A / Class B オペレーションに無料枠。最新の条件は必ず公式を見る）。

- https://developers.cloudflare.com/r2/pricing/

1 試走（約 1 週間）で数百 MB 以内を目安にする。`attempts/` を 90 日で消し、
`keep/` だけを残す前提でも、無料枠を超えそうなら `status` とダッシュボードの使用量を見る。

## 8. トラブルシュート（固定コード）

`status` の `config`、`archive_state.json` の `last_error`、CLI の出力に出るコード:

| コード | 意味 | 対処 |
|---|---|---|
| `ok` | 正常 | なし |
| `unconfigured` | `r2.env` が無い、または必須値が欠けている | 手順 4 をやり直す。何も送っていない |
| `insecure_permissions` | `r2.env` の権限が 600 より緩い | `chmod 600 ~/.config/docich/r2.env` |
| `invalid_env` | `KEY=VALUE` 以外の行、大きすぎるファイル | 書式を直す |
| `invalid_config` | バケット名などが不正 | 値を見直す |
| `invalid_endpoint` / `insecure_endpoint` | エンドポイントが不正、またはテスト以外の平文 HTTP | 既定の https に戻す |
| `gzip_pending` | 圧縮中のファイルが残っている | 切り離し送信は最大10分待ってから次回に回す。失敗回数・バックオフには数えない。何もしなくてよい |
| `put_failed` | アップロード失敗（通信・HTTP エラー） | 次回に自動で再試行。続くならトークン・バケット・ネットワークを確認 |
| `head_failed` / `head_missing` | 送信後のサイズ確認に失敗 | ローカルは消していない。次回再試行 |
| `size_mismatch` | 送信サイズと HEAD のサイズが違う | ローカルは消していない。次回再試行 |
| `local_changed` | 送信中にローカルファイルが変わった | ローカルは消していない。次回再試行 |
| `frames_tar_failed` | 画像 tar の作成失敗 | 次回再試行。ディスク容量を確認 |
| `unsafe_directory` | 試走ディレクトリがシンボリックリンク | 実体のディレクトリに戻す。自動では追従しない |
| `not_ended` | まだ終了していない試走を指定した | 終了後に送る |
| `missing_attempt` | 試走が見つからない | `attempts.jsonl` と `attempts/` を確認 |
| `internal_error` | 想定外の失敗 | `status` と `archive_state.json` を見る。原因調査は follow-up |
| `locked` | 別の archive が動作中 | 待つ。すぐ終了する |
| `no_data` | データディレクトリがまだ無い | 試走が始まれば作られる |
| `partial_failure` | 複数試走のうち一部だけ失敗 | 成功分は消えている。失敗分は次回再試行 |

再試行は指数バックオフ（初回 60 秒、最大 24 時間）。`upload --attempt` で明示的に
指定した場合はバックオフを待たずに試す。同じキーへの PUT は上書きなので、再送しても
重複しない。

## 9. 漏えいが疑われるとき

1. Cloudflare ダッシュボードで該当 API トークンを **Roll / Delete** する。
2. 新しいトークンを作り、VM の `r2.env` を更新する（600 を維持）。
3. `archive status` が `ok` になることを確認する。
4. 漏えいした可能性のある値を、このリポジトリ・Issue・ログへ書かない。
   どの値がどこに出たかを調べる場合も、値そのものを貼らない。
