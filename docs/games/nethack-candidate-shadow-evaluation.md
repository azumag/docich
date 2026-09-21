# NetHack live candidate shadow evaluation (P5f)

P5fはP5eで実プレイ中に収集したcandidate shadow JSONLを **run単位でオフライン集計**する。

candidateはそのrunを操作していないため、死亡・score・深度をcandidateの成績として扱わない。

## Purpose

見たいのは次のような「candidateの振る舞い」である。

- strategic局面で何を提案したか
- proposalがP3d evaluatorに何回rejectされたか
- candidate dispatch error率
- critical HPや状態異常時のproposal分布
- production policyが停止していた局面でcandidateがnon-restを提案した頻度
- death signatureごとに、どんなproposalが観測されていたか

ここから因果関係は断定しない。

## Input

```text
<state_dir>/nethack/candidate-shadow/
  <candidate_id>/<version>/*.jsonl
```

P5eと同じmanifestを明示指定する。

評価前にP5e offline gateを再実行し、P5c suiteとP5d reportの整合性を確認する。

## Identity/integrity gate

各eventで以下がmanifest/evidenceと一致する必要がある。

- schema_version
- candidate_id
- candidate_version
- candidate_fingerprint
- command_sha256
- suite_id
- `candidate_action_sent=false`
- `execution=candidate_shadow_only`
- `policy_effect=none`

candidate identityが違うeventは集計対象から外す。

特に:

```text
candidate_action_sent != false
execution != candidate_shadow_only
policy_effect != none
```

はsafety violationとして数える。

P5eが本番Actionを送っていないという証拠自体も評価対象にする。

## Bounds

ログ処理はbounded。

```text
最大log file数  4096
総容量上限        128 MiB
1行上限           256 KiB
```

壊れたJSON行は`malformed_lines`としてcountする。

## Run correlation

UUIDのrun_idを持つeventは:

```text
<state_dir>/nethack/runs/<run_id>.json
```

と照合する。

取得するterminal evidence:

- expedition
- terminal status
- score
- turns
- max depth
- death reason
- Amulet flag
- P5a death signature
- same death count

run_idがないeventは`untracked`として別bucketに置く。

## Per-run metrics

各runについて:

- event count
- proposed / error
- approved / rejected
- reject rate
- proposal kind counts
- production current intent counts
- intent × candidate proposal matrix
- critical event count
- critical reject/error count
- critical tag counts
- production Actionが空なのにcandidateがnon-restを提案した件数/率（report field名は後方互換で `nonhold` のまま）
- unexpected would-execute Action event数
- terminal evidence

を出す。

## Baseline divergence proxy

P5f時点ではcandidateとproduction policyは異なるaction spaceを持つため、直接「同じActionか」を比較しない。

代わりに:

```text
production current_actions == []
かつ
candidate proposal != rest
```

を保守的なdivergence proxyとして数える。

これは「candidateが悪い」という意味ではなく、productionが止まった局面でcandidateが進行/操作案を出した、という観測事実だけを表す。

## Critical tags

P5eのvisible-state tagを集計する。

例:

```text
critical_hp
low_hp
food_risk
impaired
prompt:yes_no
intent:survival_emergency
```

critical tagを持つeventについてreject/errorを別集計する。

## Death signature correlations

P5a death signatureがあるterminal runはsignature別にもまとめる。

例:

```json
{
  "death_signature": "killed_by:grid bug",
  "runs": 3,
  "events": 15,
  "proposal_kind_counts": {
    "rest": 9,
    "inspect": 4,
    "descend": 2
  },
  "interpretation": "correlation_only"
}
```

これは相関であり、candidate proposalが死亡原因だった／死亡を防げたという意味ではない。

## Output

```text
<state_dir>/nethack/candidate-shadow/
  <candidate_id>/<version>/summary.json
```

主要field:

```text
candidate_shadow_integrity_passed
candidate_errors
proposal_reject_rate
proposal_kind_counts
intent_proposal_counts
critical_events
critical_rejected
critical_errors
candidate_nonhold_on_current_no_action_rate
tracked_runs
terminal_correlated_runs
runs[]
death_signature_correlations[]
```

常に:

```text
correlation_is_not_causation = true
performance_improvement_assessed = false
eligible_for_promotion_review = false
automatic_promotion = false
policy_effect = none
```

## CLI

```bash
bin/docich --config config/docich.soren-live.toml \
  nethack-candidate-shadow-evaluate \
  --manifest /path/to/candidate.json
```

## Integrity

`candidate_shadow_integrity_passed=true` には最低限:

- malformed line 0
- candidate identity mismatch 0
- safety violation 0
- diagnostic would-execute Action event 0

が必要。

これはcandidateの攻略性能判定ではない。

## P5fで行わないこと

- candidateが死因を改善したと判定する
- score/depthをcandidateの成績として帰属する
- correlationからcausationを推論する
- candidateをproduction brainへ昇格する
- configを書き換える
- executor allowlistを広げる

## Next

次段階では、十分なrun数が蓄積した後にproduction baselineとcandidate shadowの **事前定義したbehavior metric** を比較する。

ただしcandidateはまだゲームを操作していないため、実際の攻略性能を比較するには最終的に別環境/controlled canaryでcandidate自身が操作したrun evidenceが必要になる。

その段階も明示opt-in、versioned rollout、即時rollback可能な別PRとして扱う。
