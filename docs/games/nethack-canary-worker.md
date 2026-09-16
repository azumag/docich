# NetHack controlled-canary container worker (P5h)

P5g の controlled canary を、VM 上で実際に動かせる container worker にする。

## 目的

P5g は比較オーケストレータと worker protocol までを実装した。P5h はその protocol を実装する実workerを追加する。

```text
P5g orchestrator (host)
  |
  | JSON request
  v
python -m docich.nethack_canary_container
  |
  | podman/docker, network=none
  v
NetHack 5.0 canary container
  |
  +-- private /canary/episode/playground
  +-- baseline_p3b or candidate_strategist
  +-- tmux 80x24 TTY
  +-- xlog result
  |
  v
JSON worker result
```

production NetHack の runtime/save/xlog/dump は container に mount しない。

## なぜ専用ビルドが必要か

NetHack 5.0 Unix build は HACKDIR/SAVEDIR を通常の runtime config で安全に差し替える方式ではない。

P5h image は公式 NetHack 5.0.0 source archive を SHA-256 固定で取得し、build 時に:

```text
HACKDIR=/opt/nethack/playground
VAR_PLAYGROUND=/canary/episode/playground
```

を埋め込む。

immutable support data は `/opt/nethack/playground`、ゲームごとの可変データは bind-mounted `/canary/episode/playground` に分離する。

公式 source:

```text
https://nethack.org/download/5.0.0/nethack-500-src.tgz
sha256 2959b7886aac76185b90aea0c9f80d14343f604de0ae96b3dd2a760f7ab3bde9
```

## Image build

Docker:

```bash
docker build \
  -f containers/nethack-canary/Dockerfile \
  -t docich-nethack-canary:5.0.0-p5h \
  .
```

Podman:

```bash
podman build \
  -f containers/nethack-canary/Dockerfile \
  -t docich-nethack-canary:5.0.0-p5h \
  .
```

CI の `NetHack canary image CI` でも image を実buildし、NetHack binary と worker module importを確認する。

## System configuration

canary buildでは:

```text
WIZARDS=
EXPLORERS=
SHELLERS=
MAXPLAYERS=1
```

にする。

`-D` wizard、`-X` explore、shell escape をcanary controllerへ提供しない。

DUMPLOGFILEも `/canary/episode/playground/dumps/...` に固定する。

## Host launcher hardening

`docich.nethack_canary_container` は `podman` を優先し、なければ `docker` を使う。

明示指定:

```bash
export DOCICH_CANARY_RUNTIME=podman
export DOCICH_NETHACK_CANARY_IMAGE=docich-nethack-canary:5.0.0-p5h
```

containerには以下を固定する。

```text
--rm
--network=none
--read-only
--cap-drop=ALL
--security-opt=no-new-privileges
--pids-limit=256
--memory=1024m
--cpus=1.0
/tmp = tmpfs, nosuid,nodev,noexec
```

Podmanでは `--userns=keep-id`、Dockerではhost uid/gidを明示する。

### mount surface

baseline arm:

```text
host episode root -> /canary/episode (rw)
```

candidate arm:

```text
host episode root      -> /canary/episode (rw)
candidate manifest only -> /canary/candidate.json (ro)
```

repository全体、production playground、SSH key、home directory、Docker socket等はmountしない。

candidate command自体はimageに含まれる `/opt/docich/brains` 等から解決する前提。

## Network

P5h は `network=none` 固定。

したがって、この段階でcanary実行できるcandidateは **container内だけで完結するlocal command** に限る。

HTTP/API型LLM candidateのegress許可はP5hには入れない。必要なら、接続先allowlist・credential scope・request budgetを別設計で追加する。

## Worker lifecycle

container entrypoint:

```text
python3 -m docich.nethack_canary_worker
```

workerは:

1. internal requestをstrict validation
2. `/canary/episode/playground` を準備
3. tmux 80x24 TTYでNetHackを起動
4. character creation promptはworkerが通常モードで処理
5. TTYを `normalize_tty` へ渡す
6. baseline/candidate controllerを実行
7. xlogfileをterminal factの正本として読む
8. JSON resultを1件だけstdoutへ返す
9. tmux sessionを必ず終了

の順で動く。

## Baseline arm

baselineはproductionと同じ `NethackLayeredPolicy` + `assert_p3b_safe` を使う。

自動操作は現行P3bの:

- `--More--` のspace
- visible safe cardinal exploration `h/j/k/l`

だけ。

## Candidate arm

通常のP3b actionがある局面はbaselineと同じ。

`requires_llm=true` のstrategic局面だけcandidate manifestの `CommandStrategist` を呼ぶ。

```text
visible TTY
 -> P3 policy
 -> strategic stop
 -> optional visible inventory probe
 -> public StrategicRequest
 -> candidate
 -> P3d evaluate_proposal (fresh state)
 -> P5h canary-only executor
 -> canary keypress
```

production P3d `execution_plan()` は変更しない。productionでは引き続きhold以外を実行しない。

## Canary-only executor

P5hで実行を許すもの:

| proposal | visible condition | canary keys |
|---|---|---|
| hold | approved | none |
| inspect | approved | none |
| answer_prompt | fresh prompt compatible | answer |
| consume | category=food | `e` + letter |
| consume | category=potion | `q` + letter |
| equip | category=weapon | `w` + letter |
| equip | category=armor | `W` + letter |
| use | category=tool | `a` + letter |

拒否:

- unpaid item
- unknown category
- wand (`z`後のdirectionがproposal schemaにない)
- ring/amulet equip
- ascend/descend/move_to_stairs
- stale inventory letter
- fresh prompt mismatch

特に階段は、TTY上の`@`の下にあるterrainを公開観測だけで確定できないため推測しない。

## Inventory probe

strategic局面かつblocking promptがない場合、worker自身が `i` でvisible inventoryを表示し、公開情報だけを解析してEscapeで戻る。

candidateは未鑑定itemのtrue identityを受け取らない。

## Current capability limit

P5hは「完全攻略AI」ではない。

現行StrategicProposalにはattack/open-door/travel direction等がなく、P3bも隣接creatureで停止する。そのためcontrolled canaryが:

```text
terminal_status=timeout
exit_reason=policy_stall:...
```

になることは正常にあり得る。

この段階の目的は **candidateが実操作できる隔離実験基盤を安全に成立させること**。action schema/executorの拡張は別PRで、productionとは分離してレビューする。

## Seed

stock NetHack 5.0.0にP5hが依存できるreviewed public deterministic seed injectionは追加しない。

requestのseedは結果にechoするが:

```text
seed_applied=false
```

を返す。

したがってP5hで実際に回すP5g planは当面:

```json
{
  "seed_base": null,
  "require_seed_control": false
}
```

とする。

NetHack本体へのseed patchはゲーム挙動を変えるため、このPRには含めない。

## Wall-clock timeout

P5gの既定 `episode_timeout_s=900` に対して、host launcherはcontainer内に既定840秒を渡す。

```bash
export DOCICH_CANARY_INNER_TIMEOUT_S=840
```

内部workerが先に終了することで、外側timeoutがcontainer launcherを強制killするのを避ける。

P5g planで900秒より短いepisode timeoutを使う場合、inner timeoutも必ずそれより十分短く設定する。

例:

```bash
export DOCICH_CANARY_INNER_TIMEOUT_S=240
# plan episode_timeout_s=300
```

## P5g plan example

candidate manifestが `/srv/docich/candidates/cand-a-v1.json` にある場合:

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

実行:

```bash
bin/docich --config config/docich.soren-live.toml \
  nethack-canary --plan /path/to/plan.json
```

## Result source of truth

workerはterminal factsをcanary専用:

```text
/canary/episode/playground/xlogfile
```

から取得する。

score / turns / max depth / death reason / achievement bitsはxlog由来。画面から死因を推測しない。

## 次

P5i候補:

1. P5h image/workerをVMへ配置してsmoke canaryを実測
2. policy stall分布を収集
3. canary専用proposal/action schemaへ attack/open-door 等を追加
4. controlled canaryのsample数を増やす
5. 事前定義metricと統計判定を追加
6. その後にだけ、rollback可能なproduction promotion設計を検討
