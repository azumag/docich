# LIVE ワーカーの systemd user unit(テンプレート)

LIVE ワーカーを常駐させるための **user unit のテンプレート**。このリポジトリはユニットを
インストールしない。**ユニットの配置・`enable`・起動はオーナーの作業**で、ここに書いた
コマンドもオーナーが自分の環境で実行する。秘密情報はユニットにもこの文書にも書かない。

- エントリポイント: `python -m docich.trading.live_worker run --state-dir <絶対パス>`
  (`src/docich/trading/live_worker/__main__.py`)。`--state-dir` は絶対パスで、親ディレクトリが
  存在すること。相対パス・親が無いパスは何も作らず終了コード `2` で拒否する。
- `--interval` は 30〜3600 秒(既定 60)。サイクルの間隔で、停止フラグは最大 1 秒の
  スリープ片の間で見る。範囲外は引数検査で何も作らず終了コード `2` で拒否する。
- 状態ファイルは `<state>/trading-live/live_status.json`(0600、原子的に置き換え)。
  残高・金額・注文 ID・キーは入らない。

## ユニット(テンプレート)

`~/.config/systemd/user/docich-live-worker.service` などに置く。`<...>` はオーナーが埋める。

```ini
[Unit]
Description=docich LIVE worker (read-only exchange access, sender=None)
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
# docich は src/ 配下。`python -m` はカレントディレクトリを sys.path に入れる。
WorkingDirectory=<リポジトリの checkout>/src
ExecStart=<venv の python> -m docich.trading.live_worker run --state-dir <state の絶対パス>
Restart=on-failure
RestartSec=30
RestartPreventExitStatus=2 3 5
TimeoutStopSec=90
KillSignal=SIGTERM
# キーはユニットの環境変数で渡さない(下記「キー」)。

[Install]
WantedBy=default.target
```

- `WorkingDirectory` は `src`(その下に `docich/` がある)。`python -m` はカレント
  ディレクトリを `sys.path` に入れるので `docich` が見つかる。`ExecStart` の python は
  `requirements-trading.txt`(ccxt)を入れた venv のもの。ccxt が無い場合、読み取り層の
  構築が失敗して終了コード `2`(config)になる。
- `RestartPreventExitStatus=2 3 5` は config・auth・lease-held で再起動を繰り返さないため。
  **8(retryable)は再起動する**。**4(clock)も再起動する**。7(lease lost)も再起動する。
  `StartLimitIntervalSec` / `StartLimitBurst` が、時計のずれなどで速い失敗ループに入ったときの
  上限になる。
- `KillSignal=SIGTERM`。ワーカーの SIGTERM/SIGINT ハンドラはフラグを立てるだけで、
  実行中のサイクルのあと、最大 1 秒のスリープ片の間で止まり、終了コード `0` で終わる。
  `TimeoutStopSec=90` はサイクル中の停止に余裕を持たせるため。
- **キー・秘密をユニットの環境変数に書かない。** journal にも出さない。

## キー

`bitbank_live/credentials.py` が、`<state>/trading-live/owner/bitbank_readonly_key.json` を
読む(0600、owner ディレクトリは 0700、symlink は追わない)。キーが無い・検査に通らない場合は
値やパスを出さず、理由コードだけの `ReaderUnavailable(kind="config")` になり、ワーカーは
終了コード `2` で止まる。**キーの値をユニット・環境変数・この文書・Issue に置かない。**
環境変数 `DOCICH_BITBANK_RO_API_KEY` / `..._SECRET` は `source="env"` を明示した読み方だけが
使い、このエントリポイントは使わない。

## 終了コード

| code | 意味 | systemd |
|---|---|---|
| `0` | 正常終了(停止要求)、または有効な方針セットが無く inert | 再起動しない(成功) |
| `2` | config: レイアウト・台帳・読み取り層の設定・方針セット・引数の失敗 | 再起動しない(`RestartPreventExitStatus`) |
| `3` | auth: 取引所がキーを拒否した | 再起動しない(同上) |
| `4` | clock: ローカル時計が取引所の許容範囲外 | 再起動する(直れば回復) |
| `5` | lease held: 別のワーカーが書き込み権を持っている(状態ファイルは書かない) | 再起動しない(同上) |
| `6` | 未使用(旧: コード revision の変更) | — |
| `7` | lease lost: 起動中・サイクル中に書き込み権を失った(状態ファイルは書かない) | 再起動する |
| `8` | retryable: 一時的・分類できない起動失敗。安全に再起動できる | 再起動する |

`4` の再起動が続く場合は `StartLimitBurst` で止まる。時計を直してから
`systemctl --user reset-failed` → 起動し直す(オーナーの作業)。

## このワーカーができないこと(安全側の固定)

- `runner.py` は `OrderExecutor(..., sender=None, ...)` を組み立て、送信できる種別は
  `SENDER_KINDS_ALLOWED = {"mock"}` のまま。**有効化しても発注経路は無い**。取引所層は
  読み取り専用(`submit_order`/`cancel_order` を持たない)。
- 最初の有効な方針セットの有効化は**オーナーが `python -m docich.trading.live_owner
  --state-dir <state> activate ...` で行う手動の作業**。方針セットが無いワーカーは inert
  (`phase: inert`, `reason: no_active_policy_set`)で終了コード `0`。
- ON/OFF と `shadow`/`trade` は制御ファイル
  `<state>/trading-live/owner/live_control.json`。WebUI の LIVE タブ、または
  `python -m docich.trading.live_owner --state-dir <state> control ...` で書く。ワーカーは
  毎サイクル読むので、切り替えに再起動は要らない。
- ユニットのインストール・`enable`・`start`、`loginctl enable-linger`(ログインセッションが
  無くても動かす場合)はオーナーの判断と作業。このリポジトリのデプロイはユニットを置かない。
