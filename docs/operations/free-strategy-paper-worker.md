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
- ホスト側のquotaはこの新worker unitだけに適用します。初期値は `CPUQuota=100%`、
  `MemoryMax=1G`、`TasksMax=128` で、既存PAPER workerのunit・PID・cgroupは変更しません。
- unitはsystem scopeの `User=ubuntu` / `SupplementaryGroups=docker` で実行します。
  `ubuntu` 自身へdocker groupを恒久追加せず、このworkerプロセスだけにDocker socket権限を渡します。
- workerは起動直後、実効cgroupの有限な `cpu.max` / `memory.max` / `pids.max` を確認します。
  いずれかが無い、`max`、または有限値でない場合はgateway・cycleの前に停止します。

## 1. VM適合確認

workerをenableする前に、production VMで次を確認します。

1. Docker daemonがLinux modeで利用可能。
2. Docker runtimeに `runsc` が登録済み。
3. memory / PID / CPU quota controlが有効。
4. production checkoutがcleanで、既存PAPER runtimeが正常。

Dockerの導入時も、PAPER sandboxからポートを公開しません。`docker run` / `docker create`
に `-p`、`--publish`、host networkを指定せず、既存FWへ手動ルールを追加しません。
Docker daemonのFW設定を変更する場合は、bridge通信を使わないことを確認したうえで
`iptables=false` / `ip6tables=false` / `ip-forward=false` を明示し、導入前後のFW差分を確認します。

この確認が終わるまではunitをenableしません。

## 2. Docker / gVisorの単体probe

レビュー済みのホスト provisioning は `docs/operations/container-host-provisioning.md` に定義し、`ops/container_host/install_container_host.sh` で導入・`ops/container_host/verify_container_host.sh` で検証する（末尾 `result=ok` を確認する）。
VM上では既定の `systrap` を使い、KVM設定は追加しません。導入後、まず候補guestとは別の
使い捨てprobeを1回だけ実行します。`runsc`登録、Linux daemon、memory/PID/CPU capabilityの
いずれかが確認できなければ停止します。

```sh
docker info --format \
  '{"OSType":{{json .OSType}},"Runtimes":{{json .Runtimes}},"MemoryLimit":{{json .MemoryLimit}},"PidsLimit":{{json .PidsLimit}},"CPUCfsQuota":{{json .CPUCfsQuota}}}'
runsc --version
docker run --rm --runtime=runsc --network=none --read-only \
  --cap-drop=ALL --security-opt=no-new-privileges:true \
  --cpus=1 --memory=512m --memory-swap=512m --pids-limit=32 \
  --entrypoint=/usr/local/bin/python \
  'python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea' \
  -c 'print("gvisor-probe-ok")'
```

probeは候補戦略・PAPER台帳・既存PAPER workerを読み書きしません。`docker info` の全出力を
ログやhealthへ保存せず、判定は固定されたcapability/error codeだけにします。

## 3. guest imageをVM上でbuild

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

## 4. local envを作る

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

## 5. unitをinstallする

```sh
sudo install -d -m 0755 /etc/systemd/system
DOCICH_ROOT="$(pwd)"
sed "s|__DOCICH_ROOT__|${DOCICH_ROOT}|g" \
  scripts/systemd/docich-free-strategy-worker.service \
  | sudo tee /etc/systemd/system/docich-free-strategy-worker.service >/dev/null
sudo systemctl daemon-reload

# これはenable/startではない。値が有限であることを確認してから次へ進む。
sudo systemctl show docich-free-strategy-worker.service \
  -p CPUQuotaPerSecUSec -p MemoryMax -p TasksMax -p FragmentPath
```

期待値は `CPUQuotaPerSecUSec=1s`（100%）、`MemoryMax=1073741824`、
`TasksMax=128` です。値が `infinity`、`max`、空、またはunitの実ファイルと異なる場合は
enableしません。起動後はworker自身の実効cgroup probeも再度この条件を確認します。
ここまでではworkerは起動しません。

## 6. PAPER workerを明示起動する

VM適合確認、image build、env確認が全部終わってからだけ実行します。

```sh
sudo systemctl enable --now docich-free-strategy-worker.service
```

状態確認:

```sh
sudo systemctl status docich-free-strategy-worker.service
bin/docich free-strategy --trading-dir "$DOCICH_FREE_STRATEGY_TRADING_DIR" status
cat "$DOCICH_FREE_STRATEGY_TRADING_DIR/free-strategies/health.json"
```

dashboardには「AI戦略研究 / 独立PAPER」として表示されます。

## 7. 停止

```sh
sudo systemctl disable --now docich-free-strategy-worker.service
```

停止しても既存のPAPER workerやPAPER台帳を変更しません。

## 本運用について

このworkerから実注文へ切り替える設定はありません。`live` / `promote` コマンドもありません。
本運用を作る場合は別設計・別レビュー・別承認が必要です。
