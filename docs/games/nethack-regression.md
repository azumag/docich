# NetHack lessons regression suite (P5c)

P5cはP5aのcandidate lessonsを、**再現可能な回帰fixture**へ変換する。

目的は「死んだので即policyを書き換える」ことではなく、改善案を入れる前後で最低限同じ安全契約を再評価できるようにすること。

P5c自体はpolicy・executor・strategist設定を変更しない。

## Input

P5aが再構築する:

```text
<state_dir>/nethack/lessons.json
```

だけをcandidate lesson indexとして読む。

各lessonの `run_ids` から個別run JSONも読み、run側に残っているlesson evidenceをfixture metadataへ添付する。

P3e advisory JSONLに同じrun時間帯の公開 `StrategicRequest` が残っている場合は、後続のcandidate strategist replay用に `replay_request` として保持する。

## Output

### Suite

```text
<state_dir>/nethack/regression/suite.json
```

主要field:

```text
schema_version
suite_id              # casesのcanonical SHA-256
cases[]
policy_effect = none
automatic_promotion = false
```

caseごとにも `policy_effect=none` を固定する。

suiteを生成した後にfixtureやexpectationを手編集すると、`suite_id` のcontent hashが一致せずevaluate前に拒否される。

### Evaluation report

```text
<state_dir>/nethack/regression/latest_report.json
```

主要field:

```text
total_cases
scored_cases
passed_cases
failed_cases
informational_cases
baseline_contract_passed
ready_for_candidate_evaluation
policy_effect = none
automatic_promotion = false
```

`ready_for_candidate_evaluation=true` は「candidateを評価するためのfixtureが壊れていない」という意味だけで、candidateの採用・昇格を意味しない。

## Lesson → case mapping

### survival_signal

`policy_survival_emergency`

critical HPの公開TTY fixtureを現在の `NethackLayeredPolicy` に通し:

```text
layer = strategic
intent = survival_emergency
requires_llm = true
actions = []
```

を維持することを確認する。

元runにP3eの `survival_emergency` StrategicRequestが残っていれば `replay_request` に添付する。

### food_survival

`policy_food_emergency`

visible `Weak` statusのfixtureで:

```text
intent = food_emergency
actions = []
```

を確認する。

### proposal_drift

`proposal_freshness_gate`

request時のinventory letter `a` と実行前のletter `a` が別itemを指すfixtureをP3d evaluatorへ通し:

```text
evaluation = rejected
execution_allowed = false
```

を確認する。

### repeated_death

`repeated_death_memory`

個別run lesson evidenceに:

- death signature
- total >= 2

が実際に存在することを確認する。

「同じ死因」という集計自体が壊れた状態で改善評価へ進まないためのmemory contract。

### terminal_evidence

`terminal_unknown_guard`

死因不明fixtureでdeath signatureを作らず、`policy_effect=none`を維持する。

### evidence_gap

`evidence_gap_guard`

観測ログ不足というlessonが、policy自動変更へ変換されていないことを確認する。

### unknown future category

`manual_review` としてinformational扱い。

未知categoryを勝手に既存policy操作へ割り当てない。

## CLI

suite生成:

```bash
bin/docich --config config/docich.soren-live.toml \
  nethack-regression build
```

現行contract評価:

```bash
bin/docich --config config/docich.soren-live.toml \
  nethack-regression evaluate
```

別suiteを明示する場合:

```bash
bin/docich --config config/docich.soren-live.toml \
  nethack-regression evaluate --suite /path/to/suite.json
```

## P5cで行わないこと

- lessonをagent promptへ自動注入
- lessonからkeypress ruleを自動生成
- candidate policyの自動インストール
- current policyの書換え
- executor allowlistの拡大
- passing reportによる自動promotion
- rollback対象の変更

## 次

P5dではversioned **candidate policy/strategist** をshadow/replayで評価する。

P5c suiteに保存した `replay_request` とbuilt-in safety fixtureへcandidateを通し、baselineとの差分・安全contract違反・改善指標をレポートする。

candidateの昇格を実装する場合も、別の明示操作とrollback可能なversion管理を必須にする。
