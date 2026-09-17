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

`EpisodeOutcome.fitness()` は **辞書式 `(max_depth, score, turns)`**。深さを最優先にすることで、`turns` だけを最大化する **endless rest** のような退化行動を昇格させません（P5k で導入した `rest` の副作用）。NetHack は seed を固定しても時刻依存コードで完全再現しないため、`turns` は `GateConfig.turn_tolerance_ratio`（既定 0.15）以内のドリフトを許容し、depth と score は厳密に比較します。

- `max_depth_non_regression`: いずれかの seed で候補の depth が baseline を下回れば reject。
- `required_non_regression`: 非退行 seed の割合（既定 1.0）。
- `degenerate_fitness`: terminal 到達が両arm 無く候補の depth が全て 0 の場合は reject。

## rollback / known-good

`KnownGood(candidate_id)` を持ち、`apply_decision(known_good, decision)` は

- promote → 候補を known-good に更新、
- reject → known-good を据え置き（＝作り直し）。

## 単発 canary は使わない

実測で 1 run のばらつきが非常に大きい（P5j 746 / P5k 1005 / locked-door 11 / low-HP 493 turns）ため、`min_episodes_per_arm` と seed 固定のペア比較を必須にしています。

## runner（P6e）

`ops/vm_actions/nethack_promotion_runner.py` が seed 固定で 2 arm を実行し、`EpisodeOutcome` と trace 検証を集めて `evaluate_promotion` を呼びます。

- baseline arm は image 同梱の catalog、candidate arm は候補 catalog を arena に置き `DOCICH_CANARY_CATALOG` で注入（両 arm とも canary tactical baseline policy）。
- 各 episode は seed 固定（`DOCICH_CANARY_ACTION_TRACE=1` で trace も取得し P6c 検証）。
- seed 制御は、image が NetHack の `DEV_RANDOM` を `/canary/episode/seed` へ向け、worker が `request.seed` を 8 byte で書くことで成立（`seed_applied=true`）。
- production isolation チェックは caller が `isolation_check` で注入。

## improvement loop（P6f）

`run_improvement_cycle`（`ops/vm_actions/nethack_promotion_runner.py` + `src/docich/nethack_catalog_proposer.py`）:

1. baseline arm を実行し `FailureSignal`（stall intent / exit_reason / turns / depth）を作る。
2. `build_proposal_request` で bounded な公開 JSON（failure + 現行 catalog + `allowed_effects` / `allowed_new_action_effects` / `allowed_placeholders` / `allowed_risk_classes` + constraints）を作り、外部 command（proposer）へ渡す。
3. proposer 出力は `parse_action_catalog` で schema/safety 検証し、**reviewed effect/predicate/placeholder 語彙のみ**許可する。新 action id は固定セマンティクスを持つ `allowed_new_action_effects` の effect を使う場合に限り data で追加可能。
4. 候補 catalog を runner で seed 比較 → P6c trace 検証 → `evaluate_promotion`。
5. promote なら known-good 更新、reject なら作り直し。

proposer は外部 command 境界（`CommandCatalogProposer`）。timeout / 非0 exit / 過大要求・応答 / 不正 JSON / 未知 effect・predicate・placeholder / 既存 action の effect・risk_class・key_pattern 変更・既存 action の削除 / 新 action での generic `keys` effect 使用は `CatalogProposalError` で fail-closed。`allowed_action_ids` 引数は後方互換のため残しているが、新規呼び出しは `allowed_effects` を使う。

## 新 capability（P6g で data 化）

reviewed effect のうち `attack_direction` / `open_door` / `eat_item` / `explore_step` / `directional_travel` のように **key_pattern の意味が固定された effect** を再利用する新 action id は、catalog entry の追加だけで policy 実行・trace 検証・昇格判定の対象になる（詳細は `nethack-action-catalog.md` の effect 語彙表）。

`keys` は任意の1文字 literal を送れる汎用 effect なので、自動 proposer の新 action id には使用できない。`>` など新しい literal key capability を追加する場合は、catalog/code の reviewed change が必要。未知 effect の追加も従来どおり reviewed なコード変更を要する。

## 未実装（次）

- fitness の本格化（simulator / 並列 rollout）。

Relates to #630, #631, #586, #490.
