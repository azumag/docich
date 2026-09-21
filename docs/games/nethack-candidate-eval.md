# NetHack candidate strategist replay (P5d)

P5dはP5c regression suiteに対して、**明示指定したversioned candidate strategist** をオフラインreplayする。

candidateがどれだけ良さそうでも、この段階では本番policy・agent・executorへ昇格しない。

## 前提 gate

candidateを起動する前にP5c suiteを現行コードで再評価する。

```text
baseline_contract_passed != true
  -> candidate processを1回も起動しない
  -> status = blocked_baseline
```

壊れたbaselineの上でcandidate結果を比較しない。

## Candidate manifest

例:

```json
{
  "schema_version": 1,
  "candidate_id": "nethack-strategist-exp-a",
  "version": "v1",
  "command": ["python3", "brains/nethack_candidate.py"],
  "timeout_s": 20.0,
  "max_request_bytes": 32768,
  "max_response_bytes": 16384,
  "max_cases": 64,
  "min_replay_cases": 1,
  "expected_suite_id": null
}
```

manifestは明示pathで指定する。標準configへcandidate commandを追加しない。

`candidate_id` と `version` はreport保存先にも使うため、英数字・`.`・`_`・`-` の短いidentifierだけを許可する。

## Request boundary

P5c caseの `replay_request` がある場合も、そのままcandidateへ渡さない。

P5dで再度strict validationし、許可するのはP3e public StrategicRequestのfieldだけ:

```text
schema_version
intent
reason
observation
  message
  prompt
  player
  vitals
  conditions
  local_map
inventory
constraints
```

unknown fieldがあればrecorded requestを破棄する。

known case (`survival_signal` / `food_survival`) ではP5c synthetic TTY fixtureからpublic requestを再生成してfallbackできる。

したがって、壊れたadvisory JSONLに `hidden_map` 等が混ざってもcandidateへforwardしない。

## Replay flow

```text
P5c case
  -> recorded public request or reviewed synthetic request
  -> CommandStrategist (bounded subprocess)
  -> StrategicProposal schema validation
  -> P3d evaluate_proposal against replay public state
  -> P3d execution_plan (diagnostic only)
  -> candidate report
  X gameplay action
```

P3d execution gateは現在、`rest` の明示的な `.` 1キーだけを実行可能にする。

P5dはplanを診断用に計算するだけで、そのActionをagent loopへ返さない。

`rest` の `execution_action_count == 1` は許可済みの待機として扱う。それ以外の予期しない
state-changing actionが計画された場合だけcandidate safety contract failureになる。

## Candidate report

保存先:

```text
<state_dir>/nethack/regression/candidates/
  <candidate_id>/<version>/<suite_id>.json
```

reportにはcommand argv自体を保存しない。

代わりに:

```text
command_sha256
candidate_fingerprint
```

を保存する。

主要metrics:

- replayable_cases
- evaluated_cases
- coverage_complete
- dispatch_errors
- approved_proposals
- rejected_proposals
- proposal_kind_counts
- unexpected_execution_actions
- candidate_safety_contract_passed
- eligible_for_behavior_review

常に:

```text
eligible_for_promotion_review = false
performance_improvement_assessed = false
automatic_promotion = false
policy_effect = none
```

P5dは安全/互換性replayであり、実ゲーム成績が改善したとは判定しないため。

## Coverage

`max_cases` でreplayを途中打ち切った場合:

```text
coverage_complete = false
candidate_safety_contract_passed = false
```

となる。

一部の都合のよいcaseだけでpass扱いにしない。

`min_replay_cases` 未満でもpassしない。

## CLI

```bash
bin/docich --config config/docich.soren-live.toml \
  nethack-candidate-evaluate \
  --manifest /path/to/candidate.json
```

特定suite:

```bash
bin/docich --config config/docich.soren-live.toml \
  nethack-candidate-evaluate \
  --manifest /path/to/candidate.json \
  --suite /path/to/suite.json
```

`expected_suite_id` をmanifestに固定すると、別suiteへ誤ってreplayすることを防げる。

## P5dで行わないこと

- candidateを本番configへ書き込む
- agent brain切替
- candidate proposalのkeypress送信
- executor allowlist拡大
- passing reportで自動promotion
- candidateの実成績改善判定
- 現行policyの上書き

## 次

P5eではcandidateを**shadow-onlyの実ゲーム観測**へ接続し、本番Actionとは別に「candidateなら何を提案したか」を蓄積する。

その後、run成績・死亡率・危険局面・proposal reject率などをbaseline/candidateで比較できるようにする。

実昇格を作る場合は、versioned active pointer + explicit promote + explicit rollbackを別スライスで実装する。
