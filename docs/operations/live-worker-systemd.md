# LIVE ワーカーの systemd user unit(テンプレート)

LIVE ワーカーを常駐させるための **user unit のテンプレート**。このリポジトリはユニットを
インストールしない。**ユニットの配置・`enable`・起動はオーナーの作業**で、ここに書いた
コマンドもオーナーが自分の環境で実行する。秘密情報はユニットにもこの文書にも書かない。

- エントリポイント: `python -m docich.trading.live_worker run --state-dir <絶対パス>`
  (`src/docich/trading/live_worker/__main__.py`)。`--state-dir` は絶対パスで、親ディレクトリが
  存在すること。相対パス・親が無いパスは何も作らず終了コード `2` で拒否する。
- `--interval` は 30〜3600 秒(既定 60)。サイクルの間隔で、停止フラグは最大 1 秒の
  スリープ片の間で見る。範囲外は引数検査で何も作らず終了コード `2` で拒否する。
- 既定は読み取り専用。`--spot-order-sender` を付けたときだけ、同じ1口座の固定キーから
  GET 専用 reader と spot 注文 sender の対を作る(下記「発注可能な spot session」)。
- 状態ファイルは `<state>/trading-live/live_status.json`(0600、原子的に置き換え)。
  残高・金額・注文 ID・キーは入らない。

## CI のスモークテスト

GitHub-hosted の `trading-regressions` ジョブは、この文書の fenced `ini` テンプレートを
`tests/helpers/live_worker_systemd_smoke.py` で読み、ランナーの一時的な user manager に
一意な名前の unit を置いて、キー無しの起動経路を検証する。終了コード `2` /
`phase: exited` / `reason: config` と、`NRestarts=0`・`RestartPreventExitStatus=2` に
よる再起動抑止を確認する。`enable`・`loginctl enable-linger`・キー・外部接続は使わない。

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

既定の読み取り専用キーは `bitbank_live/credentials.py` が
`<state>/trading-live/owner/bitbank_readonly_key.json` から読む(0600、owner ディレクトリは
0700、symlink は追わない)。キーが無い・検査に通らない場合は値やパスを出さず、理由コードだけの
`ReaderUnavailable(kind="config")` になり、ワーカーは終了コード `2` で止まる。
**キーの値をユニット・環境変数・この文書・Issue に置かない。**
環境変数 `DOCICH_BITBANK_RO_API_KEY` / `..._SECRET` は `source="env"` を明示した読み方だけが
使い、このエントリポイントは使わない。

## 発注可能な spot session(将来の F、既定では使わない)

`run --spot-order-sender` は、1回のプロセス起動時にサービス役割を選ぶための明示オプションで、
実行中の売買スイッチではない。付けた場合だけ、固定の
`<state>/trading-live/owner/bitbank_spot_order_key.json` を読み、**同じ1口座の
`Credentials` 1個**から GET 専用 reader と spot 注文 sender を作る。reader と sender に
別々の口座を組み合わせる経路は無い。

- ファイルは `kind: "docich-bitbank-spot-order-key"`、`schema_version: 1`、`api_key`、
  `api_secret`、任意の `note`。0600 の通常ファイル、owner ディレクトリと LIVE root は
  0700、同一 uid、symlink 不可。環境変数へのフォールバックは無い。ファイル名は
  「発注可能なプロセス用に用意された」ことだけを示し、取引所側の権限の証明ではない。
- API キーの権限は、**残高・注文・約定の参照と、現物の注文作成・取消**に限る。
  **出金権限は付けない。**
- 既定の起動は、このファイルが置かれていても reader だけを使い、sender を作らない。
  発注には従来どおり `live_control.json` の `on` + `trade` が必要で、これはサイクルごとに
  読まれる。オプションを付けたセッションが壊れている場合は reader 専用へフォールバックせず、
  config/auth/clock の既存の終了コードで fail-closed になる。
- これは将来の F(常駐の実運用)のプロセス選択肢。実注文 E はオーナーの別ハンドオフで、
  1注文ごとの明示承認を含む E の手順はここでは扱わない。**このリポジトリのユニット
  テンプレート・デプロイ・自動化にこのフラグを設定しない。**

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

全銘柄の公開データ欠損が `CycleConfig.market_gap_exit_s`(既定 1800 秒 = 30 分)を
超えた場合も `8` で終わり、`Restart=on-failure` と `RestartSec=30` で再起動する。
この再起動間隔は約 30 分 + 30 秒なので、`StartLimitIntervalSec=300`(5 分)の窓には
1 回しか入らず、`StartLimitBurst=5` に達しない。データ障害が続く限り 30 分ごとに
再起動を繰り返すため、`NRestarts` と `live_status.json` の `market_gap_scope=all` /
`market_all_stale_seconds` を監視し、必要ならオーナーが
`systemctl --user stop docich-live-worker.service` で止める。ユニットの設定は変更しない。

## 既定のユニットができないこと(安全側の固定)

- 既定の `runner.py` は `sender=None` で executor を組み立てる。`--spot-order-sender` を
  明示した別プロセスだけが、許可リストの `bitbank_spot` sender を既存のゲート付き executor に
  渡す。取引所層の読み取り用 transport は GET だけで、`submit_order`/`cancel_order` を持たない。
- 最初の有効な方針セットの有効化は**オーナーの手動の作業**。WebUI の LIVE タブ
  「初期方針のセットアップ」で export → preview → activate する(通常の経路)。任意の
  スナップショットを CLI で記録する場合は `python -m docich.trading.live_owner
  --state-dir <state> activate ...` を使う。方針セットが無いワーカーは inert
  (`phase: inert`, `reason: no_active_policy_set`)で終了コード `0`。
- ON/OFF と `shadow`/`trade` は制御ファイル
  `<state>/trading-live/owner/live_control.json`。WebUI の LIVE タブ、または
  `python -m docich.trading.live_owner --state-dir <state> control ...` で書く。ワーカーは
  毎サイクル読むので、切り替えに再起動は要らない。
- ユニットのインストール・`enable`・`start`、`loginctl enable-linger`(ログインセッションが
  無くても動かす場合)はオーナーの判断と作業。このリポジトリのデプロイはユニットを置かない。
