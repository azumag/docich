# NetHack candidate live shadow (P5e)

P5eは、P5dでoffline safety replayを通過したversioned candidate strategistを、実際のNetHack観測へ **shadow-only** 接続する。

candidate proposalは本番gameplay Actionには使わない。

## Data flow

```text
live NetHack TTY
  -> normalize_tty
  -> reviewed P3b policy
  -> P3b safety guard
  -> real gameplay Action確定
        |
        +-> P5e candidate shadow
              -> same public StrategicRequest
              -> candidate proposal
              -> P3d fresh-state evaluation
              -> diagnostic execution plan
              -> JSONL telemetry
              X gameplay Action

        +-> P3e normal advisory/narration
```

candidate shadowは **実際のActionが決まった後** に呼ばれる。

candidateがtimeout、例外、壊れたproposal、危険なproposalを返しても、real Actionは変更しない。

## P5d gate

live shadowを有効にするcandidateはmanifestに `expected_suite_id` が必須。

さらに次のP5d reportが存在し、manifestと完全一致する必要がある。

```text
<state_dir>/nethack/regression/candidates/
  <candidate_id>/<version>/<expected_suite_id>.json
```

必要条件:

- `status = completed`
- `baseline_contract_passed = true`
- `candidate_safety_contract_passed = true`
- `eligible_for_behavior_review = true`
- candidate fingerprint一致
- command SHA-256一致
- suite ID一致
- `eligible_for_promotion_review = false`
- `performance_improvement_assessed = false`
- `automatic_promotion = false`
- `policy_effect = none`

つまり、P5d offline replayを飛ばしてcandidateをlive shadowへ直接載せることはできない。

## Config

標準設定は無効。

```toml
[nethack.candidate_shadow]
enabled = false
manifest = ""
cooldown_s = 30.0
max_calls = 24
```

有効化例:

```toml
[nethack.candidate_shadow]
enabled = true
manifest = "run/nethack/candidates/candidate-a-v1.json"
cooldown_s = 30.0
max_calls = 24
```

manifest自体にcandidate commandが入る。標準`nethack.toml`へcandidate commandを埋め込まない。

## Invocation policy

P5eは `PolicyDecision.requires_llm = true` の意味のある戦略判断だけcandidateへ送る。

通常の安全探索 `h/j/k/l` や `--More--` 処理ではcandidateを呼ばない。

さらに:

- 同一intent cooldown
- 1 agent processあたりの `max_calls`
- manifest側のrequest/response size limit
- manifest側のtimeout

を維持する。

## Log

```text
<state_dir>/nethack/candidate_shadow/
  <candidate_id>/<version>.jsonl
```

各eventには:

- timestamp
- current run ID (取得できる場合)
- candidate ID/version/fingerprint
- suite ID
- baseline policy intent/reason/actions
- public StrategicRequest
- candidate proposal
- P3d evaluation
- unexpected execution action count
- `execution = shadow_only`
- `policy_effect = none`

を保存する。

candidate command argvそのものはログへ保存しない。

## Safety property

P5e controllerはActionを返さない。

`NethackPolicyBrain.decide()`が返すActionは引き続き:

```text
list(decision.actions)
```

だけ。

candidate shadow failureはcatchされ、gameplayを抑制・追加・変更しない。

## P5eで行わないこと

- candidate proposalをNetHackへ送る
- candidateを本番brainへ昇格する
- configを自動変更する
- executor allowlistを拡大する
- candidateの実成績改善を断定する
- passing telemetryから自動promotionする

## 次

P5fではlive candidate shadow JSONLをterminal run/xlog/retrospectiveと結合し、実runにおける:

- strategic decision coverage
- candidate dispatch error rate
- proposal reject rate
- baseline/candidate divergence
- death前のcandidate proposal系列
- survival emergencyでのcandidate behavior

を集計する。

その結果もperformance evidenceの一部にすぎず、policy promotionは別の明示操作とrollback設計を必要とする。
