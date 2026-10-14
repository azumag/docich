# NetHack strategist boundary (P3d)

P3cで定義した `StrategicRequest` / `StrategicProposal` を、将来のLLMや外部agentへ渡すためのbounded command境界。

P3dでも **proposalをNetHack keyへ直接変換しない**。

## Dispatch

`CommandStrategist` はprovider固有SDKを持たず、外部commandに:

```text
stdin  = StrategicRequest JSON
stdout = StrategicProposal JSON
```

を渡す。

これにより opencode / claude / local LLM / 独自gateway等を、ゲームpolicyから分離できる。

### fail-closed条件

- timeout
- process launch failure
- non-zero exit
- stdoutがtextでない
- request size超過
- response size超過
- proposal JSON/schema不正

はいずれも `status=error` で終了し、game actionは生成しない。

既定の境界値:

- timeout: 20s
- request: 32 KiB
- response: 16 KiB

上限はconstructorで明示変更できるが、timeoutは120s以下に制限する。

## Proposal evaluator

model出力がschema-validでも、そのまま信用しない。`evaluate_proposal()` は **fresh visible state** と照合する。

### intent allowlist

例:

- `prompt_decision` → inspect / answer_prompt（質問中に `rest` は許可しない）
- survival/status/food emergency → rest / inspect / consume / equip / use
- `stairs_decision` → rest / inspect / ascend / descend
- contact / exploration blocked → rest / inspect

元intentに無関係なproposal kindはrejectする。

### inventory letter drift

item系proposalはrequest時点のinventory snapshotと、実行直前に再取得したvisible inventoryを比較する。

同じletterでも:

- description
- quantity
- B/U/C表示
- equipped状態
- unpaid
- category hint

のどれかが変わった場合はrejectする。

これは `a` がrequest後に別itemを指すようになった状態で、古いLLM proposalを誤実行する事故を防ぐため。

### prompt drift

`answer_prompt` はfresh promptを再確認する。

- yes/no → `y` / `n`
- direction → vi方向キー等の短い方向入力
- selection → 1文字
- text → 32文字以下

request後にpromptが消えたり種類が変わった場合はrejectする。

## Execution gate

P3dの `execution_plan()` は、approved proposalのうち **`rest` だけ**を実行可能扱いする。

`rest` はaction 1件の明示的な `.` キーであり、action 0件の判断保留ではない。

以下はproposal evaluatorでapprovedになり得ても、executor未実装のため `allowed=false`:

- consume
- equip
- use
- answer_prompt
- ascend / descend
- inspect
- move_to_stairs

つまりP3dをマージしても、自動操作surfaceはP3bから増えない。

## 本番影響

- `config/games/nethack.toml` は引き続き `agent.enabled=false / brain=random`
- `CommandStrategist` はagent loopへ未接続
- command/model設定も標準configへ追加しない
- model障害でgame loopを停止・rollbackしない

## 次

P3e:

- strategist config（command / timeout / fallback chain）
- agent loopへの**advisory-only** dispatch
- proposal/evaluation/narration logging
- rate/budget gate

その後executorは種類ごとに別スライスで追加する。最初からconsume/equip/prompt/階段を一括許可しない。

P4:

- NLE/structured observation比較
- hidden-information leak test
- TTYとstructured sourceのnormalized adapter
