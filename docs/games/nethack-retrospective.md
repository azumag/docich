# NetHack retrospective / daily strategy candidates (P5a + daily)

P5aは、終了したNetHack遠征について **事実ベースのpostmortem** を作り、次の改善段階で使うcandidate lessonを永続化する。

retrospective自体はローカルに保存する。日次処理は設定済みAI委任先へ、終了区分・score・turn/depth・HP比率・phase/intent/keyの集計だけを送り、canary用catalog候補を作る。生TTY、画面/map、run ID、死因の生文字列は送らない。

候補はseed-paired隔離canary評価待ちとして保存され、production policyへ自動適用しない。

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

### Progress trace

NetHack brainは、persistent runがactiveの間、送信キー・policy/resolved intent・HP・階層・状態異常・prompt種別と画面/mapのSHA-256だけをrun単位で記録する。raw TTYは保存しない。`state_dir/nethack/progress/<run_id>.jsonl` は2MiB/run、0600 file / 0700 directoryで上限を持ち、記録失敗はゲーム操作を止めない。

retrospectiveはtraceからturn/depth/HP・intent/key集計と同一画面への反復送信を要約する。daily provider requestは既知intent・ASCII key・数値集計だけを許し、run ID・timestamp・生画面を含めない。

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

日次候補生成はproduction profileで次を実行する。

```bash
bin/docich --config config/docich.soren-live.toml nethack-daily-improve
```

canonical user timer `docich-nethack-daily-improve.timer` は毎日05:10 JSTに実行する。完了済みの未処理runがない日はproviderを呼ばない。最大8 runを1日あたり処理し、結果は `state_dir/nethack/daily-improvements/YYYY-MM-DD.json`、変更catalogは同じディレクトリの `candidates/` に保存する。候補statusは `pending_canary_evaluation` で、productionへ反映するには既存の隔離評価と明示的な昇格が別途必要。

## P5aで行わないこと

- candidate lessonをpolicyへ自動反映
- strategist promptへlessonを自動注入
- executor allowlistの変更
- death reasonのLLM推測
- run evidenceがない教訓の生成
- 既存NetHack save/xlogfileの変更

日次処理が行うのは、結果・経過の分析と隔離canary候補の生成までである。ゲーム実行、TTY送信、production policy変更は行わない。
