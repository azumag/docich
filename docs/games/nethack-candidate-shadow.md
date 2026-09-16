# NetHack candidate live shadow (P5e)

P5eでは、P5dでoffline safety replayを通したversioned candidate strategistを、実際のNetHack観測へ **shadow-only** 接続する。

candidateは本番Actionを生成・変更・遅延させない。

## Data flow

```text
live NetHack TTY
  -> normalize_tty
  -> reviewed P3b policy
  -> P3b safety guard
  -> production Actionを確定
  -> P3e advisory (optional)
  -> P5e candidate shadow queue
       -> daemon worker
       -> candidate strategist
       -> P3d proposal evaluator
       -> diagnostic execution plan
       -> JSONL
       X production Action
```

`NethackPolicyBrain.decide()` が返すActionは、candidate shadowを呼ぶ前に `production_actions` として確定する。

candidateがtimeout・異常終了・危険proposalを返しても、このActionは変更しない。

## Non-blocking

candidate callをagent loop内で同期実行しない。

P5e controllerはbounded queue + daemon workerを使う。

```text
agent thread                 candidate worker
-----------                  ----------------
policy decision
production action確定
queue.put_nowait  ---------> candidate dispatch
return production action     evaluate/log
```

workerが処理中でqueueが埋まっている場合は `busy` としてskipする。

candidateの応答を待つためにNetHack操作を止めない。

## Offline gate

`[nethack.candidate_shadow] enabled=true` だけでは起動しない。

以下を全て確認する。

1. manifestがP5d schemaでvalid
2. P5c `suite.json` のcontent hashがvalid
3. manifestの `expected_suite_id` が指定されていれば一致
4. 同じcandidate/version/suiteのP5d reportが存在
5. reportのcandidate fingerprint / command hashがmanifestと一致
6. `baseline_contract_passed=true`
7. `candidate_safety_contract_passed=true`
8. `eligible_for_behavior_review=true`
9. `eligible_for_promotion_review=false`
10. `automatic_promotion=false`
11. `policy_effect=none`

offline reportが欠けている、古い、またはsuiteがtamperされている場合はcandidate shadowを有効化できない。

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
manifest = "config/candidates/nethack-candidate-a.json"
cooldown_s = 30.0
max_calls = 24
```

manifestはP5dで使用したものと同一である必要がある。

## When candidate is called

P5eではP3 policyの `requires_llm=true` のstrategic局面だけcandidateへ渡す。

通常の安全探索1歩ごとにはmodelを呼ばない。

同一intentは `cooldown_s` で抑制し、agent processあたり `max_calls` を超えない。

## Public information only

candidateへ渡すrequestはP3cの `StrategicRequest`。

含まれるのは画面から見えている情報だけ:

- message
- prompt kind
- visible HP/Pw/AC/Exp/Dlvl/gold/turn
- visible conditions
- local visible map
- visible inventory description
- reviewed constraints

hidden map、monster内部ID、peaceful flag、未鑑定itemのtrue identity、RNG state等は渡さない。

## Runtime log

runが追跡できる場合:

```text
<state_dir>/nethack/candidate-shadow/
  <candidate_id>/<version>/<run_id>.jsonl
```

run外なら:

```text
.../untracked.jsonl
```

各eventには公開情報だけで以下を記録する。

- candidate id/version/fingerprint
- command SHA-256（argv本体は保存しない）
- P5c suite id
- run id / expedition / session index
- turn / dungeon level
- critical state tags
- production policy layer / intent / reason
- 実際に返したproduction Action summary
- public StrategicRequest
- candidate proposal
- proposal evaluation status/reason
- diagnostic `would_execute_action_count`
- `candidate_action_sent=false`
- `execution=candidate_shadow_only`
- `policy_effect=none`

file modeは0600、directoryは0700。

## Critical tags

visible stateから補助tagを付ける。

例:

```text
low_hp
critical_hp
food_risk
impaired
prompt:yes_no
intent:survival_emergency
```

これは分析用ラベルであり、candidateへ追加のhidden stateを与えるものではない。

## Run correlation

P1のatomically-written:

```text
nethack/current.json
nethack/runs/<run_id>.json
```

をbest-effortで読む。

lockを取得してagent loopを止めない。読めない場合は`untracked.jsonl`へ記録するだけでplayは続行する。

## P5eで行わないこと

- candidate proposalをkeypressへ変換する
- current brainをcandidateへ切り替える
- executor allowlistを広げる
- candidate failureでproduction Actionを止める
- candidate応答を待ってagent loopをblockする
- candidate resultから自動promotionする
- gameplay configを書き換える

## Next: P5f

P5fではrun単位のcandidate shadow JSONLとterminal retrospectiveを集計し、例えば:

- strategic局面数
- candidate dispatch error率
- proposal reject率
- candidateがholdを選んだ割合
- critical HP時のproposal分布
- repeated death signatureとの相関
- baseline current decisionとのdivergence

を比較する。

P5fでも観測相関は因果関係とは扱わない。

実runの成績改善まで十分なevidenceが揃った後にのみ、別PRでpromotion reviewを検討する。
