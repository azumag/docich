# NetHack controlled canary (P5g)

Issue #490 P5 の controlled-canary 段階。P5d/P5e/P5f までは candidate が本番 NetHack を操作していないため、攻略性能そのものは評価できない。P5g では **本番と完全分離した arena** で baseline と candidate に実際に NetHack を操作させ、結果を比較する。

## 安全境界

P5g は production NetHack の設定や save を使わない。

```text
production
  /var/games/nethack/save
  /var/games/nethack/xlogfile
  /var/games/nethack/dumps

controlled canary
  <state_dir>/nethack/canary/<experiment>/
    baseline/episode-000/playground/...
    candidate/episode-000/playground/...
```

canary root と production path が一致・包含関係になる場合は起動しない。

さらに各 episode の直前/直後で production の save/xlog/dump fingerprint を比較する。変化を検出した場合は、candidate/worker が原因か production が同時に動いたかを区別せず、安全側に倒して experiment を即時 abort する。

production canonical state が `ready + active.game=nethack`、または current run が `active` の場合も起動しない。

## Candidate safety gate

P5g candidate は、同じ candidate/version/suite について P5d offline report が成功していなければ実行できない。

最低限:

- suite content hash valid
- candidate fingerprint 一致
- command SHA-256 一致
- baseline contract pass
- candidate safety contract pass
- behavior review eligible
- promotion review は false のまま
- automatic promotion=false
- policy_effect=none

を P5e と同じ gate で確認する。

## Canary plan

例:

```json
{
  "schema_version": 1,
  "experiment_id": "candidate-a-v1-20260916",
  "candidate_manifest": "/srv/docich/candidates/candidate-a-v1.json",
  "worker_command": ["/usr/local/bin/docich-nethack-canary-worker"],
  "episodes_per_arm": 8,
  "min_completed_per_arm": 6,
  "episode_timeout_s": 900,
  "max_turns": 20000,
  "seed_base": 1000,
  "require_seed_control": true,
  "required_isolation_mode": "container"
}
```

`worker_command` 自体は最終 report へ保存しない。SHA-256 のみ保存する。

## Worker protocol

worker は stdin に 1 episode 分の JSON request を受け取り、stdout に 1 JSON object だけ返す。

request には:

- experiment/episode/arm
- episode 専用 arena path
- unique player name
- max turns
- optional seed
- controller
  - baseline: `baseline_p3b`
  - candidate: `candidate_strategist` + candidate manifest identity
- required isolation mode
- wizard/explore 禁止
- production state を触らないこと

が含まれる。

worker は **実際の NetHack process/agent lifecycle を arena 内だけで完結**させる責任を持つ。P5g orchestrator は host 上の通常 NetHack binary を production playground のまま直接起動しない。

### 必須 result

正常終了時の worker result:

```json
{
  "schema_version": 1,
  "worker_status": "completed",
  "episode_id": "000",
  "arm": "candidate",
  "isolation_mode": "container",
  "arena": {
    "episode_root": "/.../candidate/episode-000",
    "playground_dir": "/.../candidate/episode-000/playground",
    "save_dir": "/.../candidate/episode-000/playground/save",
    "xlogfile": "/.../candidate/episode-000/playground/xlogfile",
    "dump_dir": "/.../candidate/episode-000/playground/dumps"
  },
  "player_name": "canary_c_...",
  "seed": 1000,
  "seed_applied": true,
  "controller_kind": "candidate_strategist",
  "terminal_status": "dead",
  "score": 1234,
  "turns": 4567,
  "max_depth": 6,
  "death_reason": "killed by ...",
  "got_amulet": false,
  "exit_reason": "terminal",
  "candidate_action_source": "candidate_strategist",
  "production_state_touched": false
}
```

candidate arm で `candidate_action_source != candidate_strategist` の場合、その episode は canary evidence として認めない。

baseline は `baseline_p3b` でなければならない。

## Isolation mode

P5g orchestrator が受け入れる isolation mode:

- `container`
- `vm`
- `namespace`

plan に書かれた mode と worker attestation が一致しなければ fail。

これは attestation だけに依存しない。orchestrator 側でも arena path containment と production fingerprint を検証する。

## Seed control

`seed_base` がある場合、episode N は baseline/candidate 両方に同じ `seed_base + N` を渡す。

worker がその seed を本当に適用できた場合のみ `seed_applied=true` とする。

`require_seed_control=true` の experiment では 1 episode でも seed 未適用なら integrity fail。

NetHack build が deterministic seed を安全に提供できない場合は `require_seed_control=false` で randomized controlled comparison として扱う。paired deterministic result とは混同しない。

## Arm order

時間帯バイアスを少しでも減らすため、episode ごとに順番を反転する。

```text
episode 0: baseline -> candidate
episode 1: candidate -> baseline
episode 2: baseline -> candidate
...
```

## Metrics

各 arm について:

- attempted episodes
- worker completed
- terminal completed
- dead / ascended / ended
- timeout / error
- Amulet obtained
- death rate
- median score
- median turns
- median max depth
- death signature distribution
- seed applied count

を保存する。

差分は descriptively:

- candidate - baseline death rate
- candidate - baseline median score
- candidate - baseline median turns
- candidate - baseline median max depth

を出す。

## 解釈上の制限

P5g は P5f より一段強い「candidate 自身が操作した結果」を得るが、少数 canary だけで自動昇格してはいけない。

report は常に:

```text
comparison_is_descriptive_only = true
statistical_significance_assessed = false
eligible_for_promotion_review = false
automatic_promotion = false
policy_effect = none
production_config_changed = false
```

を持つ。

十分な sample size、事前定義 metric、再現性確認、rollback plan は次段階で扱う。

## CLI

```bash
bin/docich --config config/docich.soren-live.toml \
  nethack-canary \
  --plan /path/to/canary-plan.json
```

出力 report:

```text
<state_dir>/nethack/canary/<experiment_id>/report.json
```

## CI contract

P5g test は real NetHack を起動しない。fake worker を使って orchestrator safety contract を検証する。

- paired baseline/candidate comparison
- arm order reversal
- production xlog/save/dump mutation -> immediate abort
- production NetHack active -> worker call 0
- candidate action source mismatch -> integrity fail
- required seed not applied -> integrity fail
- worker timeout は safety violation ではないが performance evidence にしない
- strict plan schema / unknown auto-promote field reject
- report に raw candidate/worker command を保存しない

## 次段階

P5h では VM 上に actual isolated worker を実装する。

候補例:

1. NetHack canary 専用 OCI/container image
2. episode ごとの private playground mount
3. baseline P3b controller と candidate strategist controller
4. xlog/dump から worker result を生成
5. CPU/memory/walltime limits
6. network egress 制限（candidate model endpoint が必要な場合だけ明示許可）

P5h の worker が実機検証できた後に、P5g controlled canary を本当に回せるようになる。
