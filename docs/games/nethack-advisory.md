# NetHack advisory strategist (P3e)

P3dで作った `StrategicRequest -> external command -> StrategicProposal` 境界を、P3eでNetHack brainへ **advisory-only** 接続する。

## 重要な境界

P3eのstrategist出力はゲームActionにならない。

```text
TTY observation
  -> normalized public state
  -> deterministic P3b policy
      -> reviewed gameplay Actions (More / safe h j k l only)
  -> optional P3e advisory sidecar
      -> StrategicRequest
      -> external strategist
      -> proposal validation/evaluation
      -> JSONL log
      -> optional narration
      X no gameplay Action
```

strategist、ログ、音声のどれが失敗しても、P3b policyが決めたActionを抑制・変更・追加しない。

## 標準設定

`config/games/nethack.toml` は次のように無効のまま。

```toml
[nethack.strategist]
enabled = false
command = []
timeout_s = 20.0
cooldown_s = 30.0
max_calls = 24
narration_enabled = false
narration_cooldown_s = 20.0
speaker = ""

[agent]
enabled = false
brain = "random"
```

したがってコードをmergeしただけではmodel呼び出しもNetHack agentも開始しない。

## 明示opt-in例

将来検証環境で使う場合のみ、外部commandを設定する。

```toml
[nethack.strategist]
enabled = true
command = ["python3", "brains/nethack/strategist.py"]
timeout_s = 20.0
cooldown_s = 30.0
max_calls = 24
narration_enabled = false
```

外部commandの契約はP3dと同じ。

```text
stdin  = StrategicRequest JSON
stdout = StrategicProposal JSON
```

provider固有SDKやAPI keyはNetHack policyへ持ち込まない。

## Dispatch条件

model dispatchするのは `PolicyDecision.requires_llm == true` のときだけ。

通常の `explore_step` や `advance_message` ではmodelを呼ばない。

同じintentを短時間に繰り返す場合は `cooldown_s` で抑止する。1 agent processで `max_calls` に達したら、それ以後のmodel callは行わない。

この上限はprovider障害時の連打や、同じprompt/emergencyを毎agent tickで問い合わせ続けることを防ぐ。

## Advisory log

modelを呼んだ判断は以下へJSONLで保存する。

```text
<state_dir>/nethack/strategist/advisory.jsonl
```

記録対象:

- timestamp
- intent / reason
- public request
- proposal
- evaluation result
- dispatch error
- narration実施有無
- `execution = advisory_only`

ログ書込み失敗もgameplayには影響させない。

## Narration

`narration_enabled=true` の場合だけ、既存Soren audio queueへ `context=nethack` で送る。

優先する文面:

1. strategist proposalの `narration`
2. proposalの `rationale`
3. Docich側の短いfallback文

`should_narrate()` と `narration_cooldown_s` で、毎歩・同じ危険状態の連続読み上げは避ける。

音声queue障害でもgameplay Actionは変えない。

## P3eでまだ実行しないもの

strategistが提案しても、次はまだkeyへ変換しない。

- consume / eat / quaff
- equip / wield / wear
- use / zap / apply
- yes/noやinventory letter回答
- ascend / descend
- door操作
- combat判断

P3d evaluatorはproposal整合性を確認できるが、P3e brainはその結果をAction pathへ接続しない。

## 次

P4ではNetHack 5.0 runtimeを維持したまま、structured observationをshadow sourceとして比較する。

NLE系は現状3.6.x系との世代差があるため、Docich本番runtimeをNLEへ置換せず、まず同一normalized schemaへ変換するadapterとhidden-information leak testを作る。
