# Free-strategy PAPER worker 運用手順

この手順は自由Python戦略を **PAPER研究だけ** で動かすためのものです。
実注文、取引所private API、資格情報、live昇格は扱いません。

## 安全境界

- 既存PAPER workerとは別process・別DB (`free-strategies/lab.sqlite3`)・別healthです。
- `docich free-strategy-worker` は `--enabled` が無ければ完全no-opです。
- 最短周期は300秒です。
- runscが利用できない場合、候補コードをruncやhost Pythonへfallbackしません。
- paused戦略は候補コードを実行せず、既存PAPER保有の時価評価だけ継続します。
- dashboardはallowlistされた研究summaryだけを別枠表示し、既存PAPER資産へ合算しません。
- 観測対象はpausedを含め最大2実験です。

## 1. VM適合確認

workerをenableする前に、production VMで次を確認します。

1. Docker daemonがLinux modeで利用可能。
2. Docker runtimeに `runsc` が登録済み。
3. memory / PID / CPU quota controlが有効。
4. production checkoutがcleanで、既存PAPER runtimeが正常。

この確認が終わるまではunitをenableしません。

## 2. guest imageをVM上でbuild

production VMでレビュー済みcheckoutからbuildし、`--iidfile` でimmutable image IDを取得します。

```sh
cd /absolute/path/to/docich
mkdir -p run/free-strategy-setup
docker build \
  --file containers/paper-strategy/Dockerfile \
  --iidfile run/free-strategy-setup/image-id \
  .
cat run/free-strategy-setup/image-id
```

得られる値は `sha256:` + 64桁hexでなければ使用しません。tag名や `latest` は設定しません。

## 3. local envを作る

`free-strategy-worker.env` はリポジトリへcommitしません。

```sh
mkdir -p ~/.config/docich
cp scripts/systemd/free-strategy-worker.env.example \
  ~/.config/docich/free-strategy-worker.env
chmod 600 ~/.config/docich/free-strategy-worker.env
```

次を実VMの絶対path/image IDへ置換します。

```text
DOCICH_FREE_STRATEGY_TRADING_DIR=/absolute/path/to/docich/run-soren-live/trading
DOCICH_FREE_STRATEGY_IMAGE=sha256:<64 hex>
DOCICH_FREE_STRATEGY_INTERVAL=300
```

このファイルへAPI key、取引所key、wallet情報は入れません。

## 4. unitをinstallする

```sh
mkdir -p ~/.config/systemd/user
DOCICH_ROOT="$(pwd)"
sed "s|__DOCICH_ROOT__|${DOCICH_ROOT}|g" \
  scripts/systemd/docich-free-strategy-worker.service \
  > ~/.config/systemd/user/docich-free-strategy-worker.service
systemctl --user daemon-reload
```

ここまでではworkerは起動しません。

## 5. PAPER workerを明示起動する

VM適合確認、image build、env確認が全部終わってからだけ実行します。

```sh
systemctl --user enable --now docich-free-strategy-worker.service
```

状態確認:

```sh
systemctl --user status docich-free-strategy-worker.service
bin/docich free-strategy --trading-dir "$DOCICH_FREE_STRATEGY_TRADING_DIR" status
cat "$DOCICH_FREE_STRATEGY_TRADING_DIR/free-strategies/health.json"
```

dashboardには「AI戦略研究 / 独立PAPER」として表示されます。

## 6. 停止

```sh
systemctl --user disable --now docich-free-strategy-worker.service
```

停止しても既存のPAPER workerやPAPER台帳を変更しません。

## 本運用について

このworkerから実注文へ切り替える設定はありません。`live` / `promote` コマンドもありません。
本運用を作る場合は別設計・別レビュー・別承認が必要です。
