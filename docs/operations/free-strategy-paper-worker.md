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

## 定期改善と昇格判定（2026-11-27 オーナー決定）

自由戦略も通常のPAPERと同じく、毎日改善する。`docich free-strategy improve`
（[`improve.py`](../../src/docich/trading/free_strategy/improve.py)）を
`docich-free-strategy-improve.timer`（毎日 06:40 JST、`Persistent=true`）が1回実行する。

1. `free-strategies/improve.disabled` があれば何もしない（停止スイッチ）。
2. 損失ストップ（`drawdown_limit`）で止まった候補は、期限を今に縮めて次のcycleで清算させ、観測枠を空ける。
3. 全候補の昇格判定を `free-strategies/promotion.json`（0600）へ書く（下記）。
4. 観測枠（最大2）に空きがあれば、過去の候補の成績（費用込みの損益・最大ドローダウン・約定数・停止理由、8件まで）を
   プロンプトに入れて新しい候補を1件だけ作る。プロバイダーへの依頼は従来どおりUTCの1日1回まで。新しい候補の期間は7日。

**昇格判定（ルール `free-v1`）**：候補のPAPER評価額の日次リターン（日本時間の日ごとに、その日の最後の評価額を前日と比べる。
作成日と当日は使わない）を、通常PAPERのルールv3と同じ e-process に通す（E ≥ 5、日ブロック5以上、1日0.2%の帰無）。
隔離（quarantined）・評価が古い候補は対象外。LIVEで自由戦略が動いているときは、同じ日でそのLIVEの日次リターンを上回ることも要る。
判定は報告を書くだけで、LIVEへの反映はLIVE側が行う。

**AIの呼び出し**：既定は従来どおり、道具を使わない文章専用のHTTPS窓口だけを使う（コーディングエージェントは使わない）。
窓口の設定は `~/.config/docich/free-strategy-ai.env`（0600、Gitに入れない。例は `scripts/systemd/free-strategy-ai.env.example`）。
プロンプト本文は [`prompts/generate_strategy.md`](../../src/docich/trading/free_strategy/prompts/generate_strategy.md) を正本とし、先頭のHTMLコメントを除いた本文へ、従来と同じ有界データを追記する。
候補に渡す銘柄は `free-strategy-worker.env` の `DOCICH_FREE_STRATEGY_SYMBOLS`（JPY建て。空でなく重複なし。固定の銘柄数上限はない）。
銘柄数を増やすとcontextが大きくなるため、gVisorへ渡す入力が `MAX_INPUT`（1MiB）を超えるcycleは `context_input_limit` でゲスト起動前に失敗する。

**DSHで生成する場合（任意・既定OFF）**：`DOCICH_FREE_STRATEGY_GENERATOR=dsh` と `DOCICH_FREE_STRATEGY_DSH_ENABLED=1` の両方が揃い、
かつ `DOCICH_FREE_STRATEGY_DSH_HOME` が有効な専用ディレクトリを指すときだけ、
`npx --yes @deepseek-ai/dsh@latest --profile headless --approval-policy never --permission-mode read-only --json -` を空の一時ディレクトリで実行し、標準入力へプロンプトを渡す。
`DOCICH_FREE_STRATEGY_DSH_HOME` は絶対パスの既存ディレクトリで、repo root と同一でもその内側でもなく、親processの `HOME` と同一でもないこと（mode 0700）。
DSHのprofile/資格情報ストアとnpm cacheだけを置く専用ディレクトリとし、`~/.config/docich/*.env` を置くディレクトリを指定しない。
未設定・不正な場合はHTTPS窓口未設定と同じ `text_provider_not_configured` で、プロセスを起動しない（生成予算も消費しない）。
子processの `HOME` は常にこの専用ディレクトリへ上書きし、親の `HOME` は渡さない。`DSH_HOME` は継承値が無ければ `<DSH_HOME>/.dsh`、
`npm_config_cache` は継承値が無ければ `<DSH_HOME>/.npm` を既定にする。
その他の環境変数は許可リスト（`PATH`/`LANG`等、プロキシ、`DSH_HOME`、`npm_config_cache`、`OPENCODE_API_KEY`）だけを渡し、`DOCICH_*`・LIVE・取引所の変数は渡さない。
`--permission-mode read-only` は `--dump-config` で確認した既存preset。DSHは道具とネットワークを持つエージェントなので、`research_brief` 経由のプロンプト注入と npm `@latest` のサプライチェーンは残存リスク。
生成物はHTTPS経路と同じ検証だけを通し、ホストではimport/execせず、gVisor内でのみ実行する。タイムアウトは900秒、stdout 2MiB・stderr 64KiBで有界。失敗はすべて `text_provider_failed`。
有効化の前提として、専用の `DSH_HOME` を用意し、unitの `PATH` 上で `npx`/`node` が起動できることを確認する（VMでの動作は未検証）。

**DSH側の定期実行（任意）**：DSH には定期呼び出し（オートメーション）機能があるため、docich の timer を使わず DSH 側で同じプロンプトファイルを定期実行し、生成物を `docich free-strategy register` で登録する運用も選べる（その場合 docich 側のフックは無効のまま）。DSH 側オートメーション設定は本タスクでは未検証。

**docich側のDSHフック（任意・既定OFF）**：`scripts/systemd/docich-free-strategy-dsh.service` と `.timer` は初期状態ではインストールもenableもしない（deployスクリプトは触らない）。
有効化する場合だけ、`free-strategy-dsh.env.example` から `~/.config/docich/free-strategy-dsh.env` を作って `DOCICH_FREE_STRATEGY_DSH_ENABLED=1`、
`DOCICH_FREE_STRATEGY_DSH_HOME`（専用ディレクトリ、0700）と `OPENCODE_API_KEY` を設定し、
`docich-free-strategy-improve.timer` の**代わりに** `docich-free-strategy-dsh.timer` をenableする（両方を同時に動かさない。生成予算は共有）。

導入（workerの導入後、VM上で）:

```sh
sed "s|__DOCICH_ROOT__|$(pwd)|g" scripts/systemd/docich-free-strategy-improve.service \
  | sudo tee /etc/systemd/system/docich-free-strategy-improve.service >/dev/null
sudo cp scripts/systemd/docich-free-strategy-improve.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now docich-free-strategy-improve.timer
```

DSHフックを使う場合のinstall例（improve.timerを使わない）:

```sh
sed "s|__DOCICH_ROOT__|$(pwd)|g" scripts/systemd/docich-free-strategy-dsh.service \
  | sudo tee /etc/systemd/system/docich-free-strategy-dsh.service >/dev/null
sudo cp scripts/systemd/docich-free-strategy-dsh.timer /etc/systemd/system/
sudo systemctl daemon-reload
# 既にimprove.timerが動いているなら先に止める（両方を同時に動かさない）。
sudo systemctl disable --now docich-free-strategy-improve.timer
sudo systemctl enable --now docich-free-strategy-dsh.timer
```
