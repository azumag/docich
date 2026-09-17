# NetHack canary promotion gate (P6d)

P6d は canary 候補を **人間 approve ではなく機械契約** で昇格判定します。`src/docich/nethack_promotion_gate.py` が判定ロジック（純粋関数）で、episode を実行する runner とは分離してあります。

## 判定

`evaluate_promotion(inputs, config)` が `promote` / `reject` と理由コードを返します。

入力:

```text
candidate_id / baseline_id
baseline:  tuple[EpisodeOutcome]   # arm=baseline
candidate: tuple[EpisodeOutcome]   # arm=candidate
trace_unverified: int              # P6c 検証で verified にならなかった件数
regression_green: bool             # 既存 P5c suite
smoke_ok: bool                     # production fingerprint 不変 / cleanup / provenance / isolation
```

判定順（1つでも該当すれば reject。理由は併記）:

1. `smoke_gate_failed`
2. `regression_failed`（`require_regression_green`）
3. `trace_unverified`（`require_trace_verified`）
4. `candidate_isolation_violation`（候補 episode の `production_state_touched` / fingerprint / cleanup）
5. `insufficient_episodes`（arm あたり `min_episodes_per_arm` 未満）
6. `unpaired_seeds`（baseline と candidate の seed 集合不一致）
7. `fitness_regression` / `degenerate_fitness`

## fitness（退化しない）

`EpisodeOutcome.fitness()` は **辞書式 `(max_depth, score, turns)`**。深さを最優先にすることで、`turns` だけを最大化する **endless rest** のような退化行動を昇格させません（P5k で導入した `rest` の副作用）。

- `max_depth_non_regression`: いずれかの seed で候補の depth が baseline を下回れば reject。
- `required_non_regression`: 非退行 seed の割合（既定 1.0）。
- `degenerate_fitness`: terminal 到達が両arm 無く候補の depth が全て 0 の場合は reject。

## rollback / known-good

`KnownGood(candidate_id)` を持ち、`apply_decision(known_good, decision)` は

- promote → 候補を known-good に更新、
- reject → known-good を据え置き（＝作り直し）。

## 単発 canary は使わない

実測で 1 run のばらつきが非常に大きい（P5j 746 / P5k 1005 / locked-door 11 / low-HP 493 turns）ため、`min_episodes_per_arm` と seed 固定のペア比較を必須にしています。

## 未実装（次）

- **behavioral な候補**: 現状 catalog は記述的で policy を駆動しない。候補を実際に挙動へ反映するには catalog を policy へ配線する（P6a-complete）。
- **runner**: seed 固定で baseline/candidate を実行し `EpisodeOutcome` と trace を集める層（P6e）。
- **capability の自動生成**: 失敗 episode → LLM が action spec / handler を提案 → canary 検証 → 本 gate、のループ。

Relates to #630, #631, #586, #490.
