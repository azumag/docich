# NetHack controlled-canary container worker (P5h)

P5g の controlled canary を、production VM 上で本当に NetHack 5.0.0 を動かせる container worker にする。

## 境界

```text
P5g orchestrator (host)
  |
  | JSON request
  v
python -m docich.nethack_canary_container
  |
  | Docker + gVisor runsc only
  | network=none / read-only rootfs
  v
NetHack 5.0.0 canary container
  |
  +-- private /canary/episode/playground
  +-- baseline_p3b or candidate_strategist
  +-- tmux 80x24 TTY
  +-- xlog result
  |
  v
JSON worker result
```

production NetHack の runtime/save/xlog/dump、home、SSH key、Docker socket は container に mount しない。

P5h は `runc`、Podman、host Python への fallback を行わない。production VM でレビュー済みの Docker + gVisor `runsc` が使えなければ fail-closed する。

## NetHack source / build

image は公式 NetHack 5.0.0 source archive を固定する。

```text
https://nethack.org/download/5.0.0/nethack-500-src.tgz
sha256 2959b7886aac76185b90aea0c9f80d14343f604de0ae96b3dd2a760f7ab3bde9
```

base image も digest pin した `python:3.12-slim` を builder/runtime 両方に使う。

Unix版NetHackでは可変playgroundを安全に分離するため build 時に:

```text
HACKDIR=/opt/nethack/playground
VAR_PLAYGROUND=/canary/episode/playground
```

を設定する。Debian の `linux.500` が正式に持つ `WANT_SYSTEM_LUA=1` を使い、system Lua 5.4でbuildする。

canary buildでは:

```text
WIZARDS=
EXPLORERS=
SHELLERS=
MAXPLAYERS=1
```

とし、`-D` wizard / `-X` explore / shell escape をcanary controllerへ提供しない。DUMPLOGFILEも `/canary/episode/playground/dumps/...` 固定。

## Image attestation

runtime imageには以下を埋め込む。

```text
org.docich.nethack-canary.abi=1
org.docich.nethack.version=5.0.0
org.docich.nethack.source-sha256=2959b7886aac76185b90aea0c9f80d14343f604de0ae96b3dd2a760f7ab3bde9
```

host launcherは tag を受け付けない。`docker build --iidfile` で得た `sha256:<64 hex>` の image ID のみ利用する。

```bash
mkdir -p run/nethack-canary-setup
docker build \
  -f containers/nethack-canary/Dockerfile \
  --iidfile run/nethack-canary-setup/image-id \
  .
cat run/nethack-canary-setup/image-id
```

環境変数:

```bash
export DOCICH_NETHACK_CANARY_IMAGE="$(cat run/nethack-canary-setup/image-id)"
```

launcherは `docker image inspect` で image ID と3つのlabelを再照合する。imageが `VOLUME` を宣言している場合も拒否する。

## Docker/runsc preflight

launcherは `docker info` から必要な項目だけ読み、次を必須にする。

- OSType=linux
- runtime `runsc` 登録済み
- MemoryLimit=true
- PidsLimit=true
- CPUCfsQuota=true

実行containerには最低でも次を固定する。

```text
--runtime=runsc
--network=none
--read-only
--cap-drop=ALL
--security-opt=no-new-privileges:true
--cpus=1
--memory=1024m
--memory-swap=1024m
--pids-limit=256
--ipc=none
--ulimit nofile=128:128
--ulimit core=0:0
--tmpfs /tmp:rw,noexec,nosuid,nodev,size=256MiB
--log-driver=none
--restart=no
```

containerには一意な名前を付け、正常終了・timeout・launcher例外のいずれでも `docker rm --force` をbest-effort実行する。

## Mount surface

baseline arm:

```text
host episode root -> /canary/episode (rw)
```

candidate arm:

```text
host episode root       -> /canary/episode (rw)
candidate manifest only -> /canary/candidate.json (ro)
```

repository全体はmountしない。candidate commandが参照するコードはimage build時に `/opt/docich/brains` 等へ含まれている必要がある。candidateを更新した場合はimageも再buildし、新しいimmutable image IDで実験する。

## Network / credentials

`--network=none` 固定なので、P5hで使えるcandidateはcontainer内だけで完結するlocal commandに限る。

HTTP/API型LLM candidateのegress、API key、credential forwardingはP5hには入れない。必要なら接続先allowlist・credential scope・request budgetを別設計・別レビューで追加する。

## Worker lifecycle

container entrypoint:

```text
python3 -m docich.nethack_canary_worker
```

workerは:

1. internal requestをstrict validation
2. `/canary/episode/playground` を準備
3. tmux 80x24 TTYでNetHackを起動
4. character creationを通常モードで処理
5. TTYを `normalize_tty` へ渡す
6. baseline/candidate controllerを実行
7. canary専用xlogfileをterminal factの正本として読む
8. JSON resultを1件だけstdoutへ返す
9. tmux sessionを必ず終了

の順で動く。

## Baseline arm

baselineはproductionと同じ `NethackLayeredPolicy` + `assert_p3b_safe` を利用する。

自動操作は現行P3bの:

- `--More--` のspace
- visible safe cardinal exploration `h/j/k/l`

だけ。

## Candidate arm

通常のP3b actionがある局面はbaselineと同じ。`requires_llm=true` のstrategic局面だけcandidate manifestの `CommandStrategist` を呼ぶ。

```text
visible TTY
 -> P3 policy
 -> strategic stop
 -> optional visible inventory probe
 -> public StrategicRequest
 -> candidate
 -> P3d evaluate_proposal (fresh visible state)
 -> P5h canary-only executor
 -> canary keypress
```

production P3d `execution_plan()` は変更しない。productionでは引き続き、任意操作ではなく
明示的な `rest`（`.` 1キー）だけを実行する。

## Canary-only executor

現段階のallowlist:

| proposal | visible condition | canary keys |
|---|---|---|
| rest | approved, fresh complete gameplay frame without visible creature contact | `.` |
| inspect | approved | none |
| answer_prompt | fresh prompt compatible | answer |
| consume | category=food | `e` + letter |
| consume | category=potion | `q` + letter |
| equip | category=weapon | `w` + letter |
| equip | category=armor | `W` + letter |
| use | category=tool | `a` + letter |

拒否する例:

- unpaid item
- unknown category
- wand（directionがproposal schemaにない）
- ring/amulet equip
- stale inventory letter
- prompt mismatch
- ascend/descend/move_to_stairs

階段はTTY上の`@`の下にあるterrainを公開観測だけで確定できないため推測しない。

## Capability limit

P5h初版は実NetHackを操作できるが、まだ完全攻略AIではない。

現行StrategicProposalにはattack/open-door/travel direction等がなく、P3bも隣接creatureで停止する。そのため:

```text
terminal_status=timeout
exit_reason=policy_stall:...
```

は正常に起こり得る。この段階では実container経路・隔離・実xlog結果取得を成立させることを優先する。canary専用tactical action schemaは次段で拡張する。

## Seed

stock NetHack 5.0.0について、P5hが依存できるreviewed public deterministic seed injectionは使用しない。

requestのseedはechoしても:

```text
seed_applied=false
```

を返す。したがって現行workerで実行するplanは:

```json
{"seed_base": null, "require_seed_control": false}
```

とする。seed patch版を将来導入する場合は別image ABIとして扱う。

## Timeout

P5g既定 `episode_timeout_s=900` に対し、P5h launcherはcontainer workerへ既定840秒を渡し、外側より先に終了させる。

```bash
export DOCICH_CANARY_INNER_TIMEOUT_S=840
```

P5g planのepisode timeoutを短くする場合は、inner timeoutも十分短く設定する。例:

```bash
export DOCICH_CANARY_INNER_TIMEOUT_S=240
# plan episode_timeout_s=300
```

## P5g plan example

```json
{
  "schema_version": 1,
  "experiment_id": "cand-a-v1-001",
  "candidate_manifest": "/srv/docich/candidates/cand-a-v1.json",
  "worker_command": ["python3", "-m", "docich.nethack_canary_container"],
  "episodes_per_arm": 4,
  "min_completed_per_arm": 2,
  "episode_timeout_s": 900,
  "max_turns": 20000,
  "seed_base": null,
  "require_seed_control": false,
  "required_isolation_mode": "container"
}
```

実行前に `DOCICH_NETHACK_CANARY_IMAGE=sha256:...` を設定する。

```bash
bin/docich --config config/docich.soren-live.toml \
  nethack-canary --plan /path/to/plan.json
```

production VMの `ubuntu` ユーザーを恒久的にdocker groupへ追加しない。Docker socket権限が必要な実行経路は、既存container workerと同様にservice/owner-gated実行単位で `SupplementaryGroups=docker` を付与する。

## Result source of truth

terminal factsはcanary専用:

```text
/canary/episode/playground/xlogfile
```

から取得する。score / turns / max depth / death reason / achievement bitsはxlog由来で、画面から死因を推測しない。

## CI

通常CIはlauncher/executor/workerをmock/fakeで契約テストする。

`NetHack canary image CI` は実際に公式sourceからimageをbuildし、immutable image IDで:

- provenance labels
- NetHack binary `--version`
- worker module import

をsmokeする。GitHub-hosted runnerでの通常runc smokeは**image build検証だけ**であり、productionの隔離認証ではない。production P5hはlauncherのDocker/runsc preflightを通らない限り起動しない。

## 次

P5i候補:

1. P5h imageをproduction VMでbuildしimmutable image IDを記録
2. owner-gated / service-scoped Docker権限で1 episode smoke canaryを実測
3. policy stall分布を収集
4. canary専用proposal/action schemaへ attack/open-door 等を追加
5. sample数を増やし事前定義metric・統計判定を追加
6. その後にだけrollback可能なproduction promotion設計を検討
