# NetHack action catalog と自動検証 (P6a/P6b)

P6 は capability 追加を「禁止」ではなく「canary で自動検証してから昇格」で扱うための土台です。人間 approve は runtime には無い（git PR レビューのみ）ため、昇格は機械契約で判定します。

## action をデータにする

`config/nethack-canary-actions.json` が reviewed な action catalog の正本です。

```json
{
  "id": "eat_food",
  "risk_class": "item",
  "preconditions": ["condition_any:Hungry|Weak|Fainting|Fainted|Starved", "inventory_food"],
  "key_pattern": ["e", "{item_letter}"],
  "postconditions": ["screen_changed"]
}
```

- `risk_class`: `message` / `prompt` / `movement` / `combat` / `door` / `item` / `rest` の reviewed 集合のみ。
- `key_pattern`: 1文字キー、または `{direction}`（vi 8方向）/ `{item_letter}`（inventory letter）の placeholder。
- `preconditions` / `postconditions`: 下記 predicate 名（`name` または `name:arg`）。

`validate_action_catalog()` が schema と safety invariant（未知 predicate・未知 placeholder・未レビュー risk_class・id 重複）を検査します。

## predicate

preconditions（可視 frame に対して評価）:

```text
prompt:<kind>            # more / yes_no / direction / selection / text / none
message_contains:<s>
condition:<name>
condition_any:<a|b|c>
hp_ratio_at_most:<float>
player_visible / player_absent
adjacent_attackable / no_adjacent_attackable
adjacent_closed_door
inventory_food
safe_step
```

postconditions（before/after frame に対して評価）:

```text
always
prompt_is:<kind> / prompt_not:<kind>
condition_cleared:<name>
message_changed
turn_advanced
screen_changed
```

predicate は `nethack_canary_tactics` と同じ判定関数を使うため、policy の挙動と検証が一致します。

## 検証

`verify_action_spec(spec, before, after, keys, inventory=..., failed_doors=...)` は

1. 全 preconditions が before で成立、
2. `keys` が `key_pattern` に一致、
3. 全 postconditions が before/after で成立

の順に確認し、`verified` / `precondition_not_met` / `keys_mismatch` / `postcondition_not_met` を返します。

canary の action trace（下記）を丸ごと検証するのが `verify_trace_file(path, catalog)` です。

## action trace の出力（opt-in）

canary worker に `DOCICH_CANARY_ACTION_TRACE=1` を渡すと、適用した各 action について
`<episode_root>/action-trace.jsonl` へ 1 行ずつ記録します。

```json
{"intent": "attack_adjacent", "keys": ["l"], "before": "<frame>", "after": "<frame>"}
```

`eat_food` の行には probe した `inventory` も含みます。tracing は best-effort で、失敗しても episode は継続します。

環境変数は `run_container_worker(..., extra_env={"DOCICH_CANARY_ACTION_TRACE": "1"})` でコンテナへ渡します。`_extra_env_args()` が名前・値・予約 env（`HOME`/`TERM`/`PYTHONDONTWRITEBYTECODE`/`PATH`）を検証します。

## 昇格への使い方（P6d 以降）

action を追加・変更したら:

1. trace 付きで canary を実行し、`verify_trace_file` が全行 `verified` であること。
2. 既存 P5c regression suite が green のままであること。
3. seed 固定の複数 episode で fitness（生存 turns だけでなく depth/探索/terminal を含む退化しない指標）が baseline 比で非退行/改善であること。
4. 既存 smoke gate（production fingerprint 不変 / production_state_touched=false / container cleanup / image provenance）を満たすこと。

単発 canary はノイズが大きい（実測: P5j 746 / P5k 1005 / locked-door 11 / low-HP 493 turns）ので、昇格は複数 episode を要求します。

Relates to #630, #586, #490.
