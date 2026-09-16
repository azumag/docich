# NetHack retrospective / lessons memory (P5a)

P5aは、終了したNetHack遠征について **事実ベースのpostmortem** を作り、次の改善段階で使うcandidate lessonを永続化する。

この段階ではlessonを攻略policyへ自動適用しない。

## Source of truth

終了事実の正本は既存のrun history / xlogfile。

```text
state_dir/nethack/runs/<run_id>.json
  status
  death_reason
  score
  turns
  max_depth
  role/race/alignment
  terminal (xlogfile evidence)
  dump_file
```

P5aはdeath reasonを推測しない。

`ended_unknown` の場合は「terminal evidence不足」というcandidateだけを作り、架空の死因や戦略原因を生成しない。

## Additional evidence

### Strategist advisory

P3eの:

```text
state_dir/nethack/strategist/advisory.jsonl
```

から、その遠征の開始〜終了時刻内にあるeventだけを読み取る。

保存するsummary:

- event count
- intent count
- proposal kind count
- fresh-state evaluator reject数
- 直近20 event

遠征外の古い/新しいeventは混ぜない。

### Dumplog

runに `dump_file` が記録されている場合だけ、configured dump directory内のbasenameを読む。

- 最大128MiBのfileまで
- 最後の32KiBだけ読む
- 最後の非空行を最大12行保存
- tail SHA-256を保存

P5aではdump内容から未知のゲーム内部状態を復元しない。

## Death signature

繰り返し死因検出のため、xlogfile由来文字列を保守的に正規化する。

例:

```text
killed by a water elemental
  -> killed_by:water elemental

starved to death
  -> starvation
```

このsignatureは同種事故の集計用であり、因果推論ではない。

## Candidate lessons

現在生成するcategory:

- `repeated_death`
  - 同じdeath signatureが複数遠征で発生
- `survival_signal`
  - 死亡前advisoryに `survival_emergency` が存在
- `food_survival`
  - `food_emergency` を記録した遠征がstarvationで終了
- `proposal_drift`
  - strategist proposalがfresh-state evaluatorでrejectされた
- `evidence_gap`
  - 死亡runにadvisory履歴がない
- `terminal_evidence`
  - terminal reasonをxlogfileから確定できない

すべて:

```json
{
  "status": "candidate",
  "source": "p5a_retrospective",
  "policy_effect": "none"
}
```

で保存する。

「AがあったからBで死んだ」のような未検証の因果関係はlesson textにしない。

## Run write-back

解析対象runへ:

```text
retrospective
lessons[]
```

を書き戻す。

P5aが過去に生成したlessonは再解析時に置換し、他sourceのlessonは保持する。

## Global lessons memory

全runを読み直して:

```text
state_dir/nethack/lessons.json
```

を毎回再構築する。

同じrunを何回解析しても `evidence_count` が二重加算されない。

lesson memoryには:

- lesson_key
- category / text
- evidence_count
- first / last expedition
- evidence run ids
- status = candidate
- policy_effect = none

を持つ。

## Locking

既存 `NethackRunStore` と同じ:

```text
state_dir/nethack/.lock
```

を使用する。

run終了処理とretrospectiveが同時にrun JSONを書き換えないようにする。

## CLI

最新terminal run:

```bash
bin/docich --config config/docich.soren-live.toml \
  nethack-retrospective
```

特定run:

```bash
bin/docich --config config/docich.soren-live.toml \
  nethack-retrospective --run-id <uuid>
```

stdoutにはretrospective JSONを1件出す。

## P5aで行わないこと

- candidate lessonをpolicyへ自動反映
- strategist promptへlessonを自動注入
- executor allowlistの変更
- death reasonのLLM推測
- run evidenceがない教訓の生成
- 既存NetHack save/xlogfileの変更

## 次

P5bではterminal run確定後にretrospectiveを自動起動し、番組終了時に「今回の死因と次回の改善候補」を短く読み上げられるようにする。

P5cではcandidate lessonを回帰fixtureへ変換し、評価で改善が確認できたlessonだけをversioned policy候補へ昇格する。昇格/rollbackは別の明示操作とする。
