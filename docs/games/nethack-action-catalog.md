# NetHack action catalog と自動検証 (P6a/P6b)

P6 は capability 追加を「禁止」ではなく「canary で自動検証してから昇格」で扱うための土台です。人間 approve は runtime には無い（git PR レビューのみ）ため、昇格は機械契約で判定します。

## action をデータにする

`config/nethack-canary-actions.json` が reviewed な action catalog の正本です。

```json
{
  "id": "eat_food",
  "risk_class": "item",
  "effect": "eat_item",
  "preconditions": ["condition_any:Hungry|Weak|Fainting|Fainted|Starved", "inventory_food"],
  "key_pattern": ["e", "{item_letter}"],
  "postconditions": ["screen_changed"]
}
```

- `risk_class`: `message` / `prompt` / `movement` / `combat` / `door` / `item` / `rest` の reviewed 集合のみ。
- `effect`: 下記 reviewed effect 語彙のみ。policy は action id をハードコードせず、この `effect` で dispatch する。固定セマンティクスを持つ reviewed effect を使う新しい id はデータ追加だけで実行できる（ハンドラ用コード追加は不要）。
- `key_pattern`: 1文字キー、または `{direction}`（vi 8方向）/ `{item_letter}`（inventory letter）の placeholder。`effect` と整合すること（下表）。
- `preconditions` / `postconditions`: 下記 predicate 名（`name` または `name:arg`）。

`validate_action_catalog()` が schema と safety invariant（未知 predicate・未知 placeholder・未知 effect・effect/key_pattern 不整合・未レビュー risk_class・id 重複・production denylist）を検査します。

production denylist（`nethack_policy.PRODUCTION_DENIED_COMMANDS`、#2744）は catalog でも同じ正本を使います。`Q` / `S` / `!` / `F` / `X` の literal key と、`#quit` / `#save` / `#!` / `#wiz*` を文字単位で綴った extended command は `validate_action_catalog()` が拒否します。catalog は data なので、denylist を迂回する新しい key capability を追加できません。

## effect 語彙（P6g）

| effect | キー効果 | 要求 key_pattern |
|---|---|---|
| `keys` | `key_pattern` を literal のまま送出（placeholder 解決なし） | literal のみ（placeholder 不可）。既存 reviewed action 用であり、proposer が新 action id に使うことは禁止 |
| `attack_direction` | `{direction}` を隣接する攻撃可能 monster の方向に解決 | `["{direction}"]` |
| `open_door` | `("o", 隣接する閉じた door の方向)` を送出（失敗 door は記録し再試行しない） | `["o", "{direction}"]` |
| `eat_item` | `("e", inventory の unpaid-free food の letter)` を送出 | `["e", "{item_letter}"]` |
| `explore_step` | `{direction}` を `explorer.plan_step` の方向に解決 | `["{direction}"]` |
| `directional_travel` | `explore_step` と同じだが direction prompt 中でも許容 | `["{direction}"]` |
| `apply_item` | 可視 unpaid-free tool の letter で `("a", letter)` | `["a", "{item_letter}"]` |
| `read_item` | 可視 unpaid-free scroll の letter で `("r", letter)` | `["r", "{item_letter}"]` |
| `quaff_item` | 可視 unpaid-free potion の letter で `("q", letter)` | `["q", "{item_letter}"]` |
| `zap_wand` | 可視 unpaid-free wand の letter で `("z", letter)` | `["z", "{item_letter}"]` |
| `throw_item` | 予備 weapon を隣接する攻撃可能 monster 方向へ `("t", letter, direction)` | `["t", "{item_letter}", "{direction}"]` |
| `wear_item` | 可視 unpaid-free armor の letter で `("W", letter)` | `["W", "{item_letter}"]` |
| `wield_item` | 可視 unpaid-free weapon の letter で `("w", letter)` | `["w", "{item_letter}"]` |
| `takeoff_item` | 装備中の armor の letter で `("T", letter)` | `["T", "{item_letter}"]` |
| `close_door` | `explorer.memory` が閉扉として目視した隣接 tile が開いている時だけ `("c", direction)` | `["c", "{direction}"]` |
| `kick_door` | `o` が失敗した閉扉を一度だけ `("^D", direction)`（`failed_kicks` で再試行しない） | `["\u0004", "{direction}"]` |
| `pray` | `("#", "p", "r", "a", "y", CR)`。CR で extended command prompt を閉じる | `["#", "p", "r", "a", "y", "\r"]` |

`pray` の CR はゲームが開いた extended command prompt を自分で閉じるためのキーであり、未知の game prompt への回答ではありません。

`keys` は任意の1文字 literal を表せるため、これを proposer の新 action id に許すと、reviewed effect という境界を越えて任意キー capability を生成できてしまいます。そのため **新しい literal-key action（例: `>` で階段を降りる）を追加する場合は、reviewed catalog/code change として明示的にレビュー**します。自動 proposer が追加できるのは、`attack_direction` など固定された `key_pattern` 契約を持つ effect の再利用だけです。

## catalog が policy を駆動する（P6）

`CanaryTacticalPolicy` は catalog をそのまま action table として使います。

- `priority` の小さい順に評価し、全 `preconditions` が成立した最初の spec を選ぶ。
- `enabled=false` の spec は決して選ばれない。
- キー効果は spec の宣言的 `effect` で dispatch する（`nethack_canary_tactics` は action id をハードコードしない）。固定セマンティクスを持つ reviewed effect の新 id もそのまま実行できる。
- `keys` の新 id は proposer からは拒否される。任意 literal key の追加は reviewed change が必要。
- 生成した keys は spec の `key_pattern` と照合し、外れれば fail-closed。
- どの spec も成立しない場合のみ P3b base policy に委譲（selection prompt / severe status などは `requires_llm` のまま）。

したがって **catalog の `enabled` / `priority` / `preconditions` を変えると canary の挙動が変わる**。これが P6d 昇格ゲートが比較する behavioural な候補になります（例: `attack_adjacent` を disabled にした候補）。P6g 以降は **固定セマンティクスの reviewed effect を再利用する新 action id の追加も catalog 差分**として扱えます。

ゲーム内 prompt を取り違えないよう、gameplay 系 spec は `prompt:none` を precondition に含みます（yes/no prompt を移動キーで答えてしまう事故を防ぐ）。

catalog は image にも同梱します（`Dockerfile` の `COPY config/nethack-canary-actions.json`、build context の `REVIEWED_BUILD_PATHS` に同ファイルを追加）。container 内の policy は `resolve_catalog_path()` がチェックアウト/image の `config/nethack-canary-actions.json` を解決します。候補差し替えは `DOCICH_CANARY_CATALOG` で上書きできます。

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
adjacent_kickable_door
adjacent_open_door
inventory_food
inventory_tool / inventory_readable / inventory_potion / inventory_wand
inventory_wieldable / inventory_throwable / inventory_wearable / inventory_worn
no_equipped_weapon / no_equipped_armor
safe_step / no_safe_step
on_down_stairs / on_up_stairs
```

`adjacent_kickable_door` は「`o` を試して失敗した閉扉（`failed_doors`）で、まだ kick していない（`failed_kicks`）」場合だけ True です。`adjacent_open_door` は `explorer.memory` が過去に `+` として目視した tile が現在開いている場合だけ True で、壁と共有する `-` / `|` glyph を扉と誤認しません。

inventory 系 predicate は可視 description の coarse category（`tool` / `scroll` / `potion` / `wand` / `weapon` / `armor`）だけを使い、未識別 item の正体を解決しません。`unpaid` の item は使用対象外です（装備解除だけは装備中の item を対象にします）。

`no_safe_step` は `explorer.plan_step(replace(obs, prompt="none")) is None` のとき True です。`safe_step` と対称で、direction prompt は評価時だけ `none` に置き換えます。policy と trace verifier は同じ `precondition()` を呼び、既存 explorer の判定を再利用します。player や dungeon level が不明な場合も planner は None を返すため、search は別途 `player_visible` と `prompt:none` を要求します。

`on_down_stairs` は `explorer.memory` が同じ階で実際に `>` を目視した座標に hero が立っているときだけ True です（#2666）。単一 frame では足元が `@` に隠れるため、現在の frame の glyph からは判定しません。

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

## data だけで追加する例（P6h）

`no_safe_step` を reviewed predicate に追加した後、search の action 自体は次の catalog data だけで追加できます。専用 handler や action id 分岐は不要です。

```json
{
  "id": "search_when_blocked",
  "effect": "keys",
  "risk_class": "movement",
  "preconditions": ["prompt:none", "player_visible", "no_safe_step", "no_adjacent_attackable"],
  "key_pattern": ["s"],
  "postconditions": ["always"],
  "enabled": true,
  "priority": 88,
  "description": "探索不能で隣接する攻撃可能モンスターもいない時は search して隠し扉/罠を探す。"
}
```

`explore_step`（85）の後、`rest`（90）の前に選択します。既存の低HP・状態異常・戦闘・door の優先順位は変えません。`rest` の fallback 条件も保持します。両者の preconditions は論理的には重なりますが、最初に成立した action だけを選ぶので選択上は排他です。search を disabled にすると従来の rest に戻ります。

現行 proposer は新規 id の汎用 `keys` effect を拒否します。この例は **PR でレビューする catalog 追加**であり、任意 literal key の自動生成を許可する変更ではありません。レビュー済み search を disabled にした baseline から、proposer が enabled に戻す data-only 候補を生成し、policy → trace 検証 → 昇格判定へ渡せます。ローカルテストの合成 frame・outcome による経路確認と、実ゲームでの効果測定は別です。

## 標準コマンドの追加（#2745）

段階1（#2784）で `pick_up_item`（priority 55、`effect=keys`、`key_pattern=[","]`）を追加しました。message が `You see here` の単一 object を示し、`prompt:none` のときだけ `,` を送ります。複数 object の pile や blocking prompt では成立しません。

段階2では、reviewed effect と predicate を追加して残りの標準コマンドを catalog に載せました。

- 有効: `wield_weapon`（56）/ `wear_armor`（57）/ `quaff_potion`（58、HP 50% 以下）/ `ascend_stairs`（83）/ `kick_door`（81、`o` 失敗後 1 回）/ `severe_status_recovery` 以外の既存 action。
- 無効（reviewed だが既定では選ばれない）: `read_scroll`（64）/ `zap_wand`（66）/ `apply_tool`（67）/ `takeoff_armor`（68）/ `throw_item`（69）/ `close_door`（82）/ `severe_status_recovery`（5、pray）。
- 無効理由: 未識別 scroll の効果、wand / tool の後続 direction、throw の item 損失、door close の目的不在、prayer timeout の不可視性。proposer が有効化する場合も catalog 差分としてレビューし、trace で効果を確認します。

`kick_door` は `^D`（`"\u0004"`）+ 方向です。`o` が失敗した扉だけを対象にし、kick 後も `failed_kicks` に記録して再試行しません（lock ループ防止）。`ascend_stairs` は `descend_stairs` と同じく、`explorer.memory` が目視した `<` の上に hero がいる場合だけ成立します。

prompt の既定応答（yes_no→`n`、direction/selection/text/unknown→`ESC`）は catalog では扱いません。strategist 側の fallback（`default_prompt_answer`、#2746）が担い、catalog の policy は blocking prompt に対して LLM 委譲（`requires_llm`）を維持します。

## 単一 frame の検証境界

**player の足元 glyph は `@` に隠れるため、「階段の上に立っている」という precondition は単一 frame から検証できません。** そこで `descend_stairs`（priority 84、`effect=keys`、`key_pattern=[">"]`）は、`explorer.memory` が同じ階で目視した `>` タイルに hero がいる場合だけ成立する `on_down_stairs` を precondition にします（#2666）。近傍に `>` が見えることと player がその上にいることは同じではありません。

`on_down_stairs` は複数 frame の観測履歴を必要とするため、catalog の構文テストが通っても、単一 frame だけを与えた trace 検証では `precondition_not_met` に落ちます。descent を含む trace を verified にするには、同じ explorer memory を verifier に渡す経路が必要で、それまでは **descent は auto-promote 対象外**のままです。catalog の `descend_stairs` は本番と同じ reviewed code 経路（`NethackExplorer` の記憶）で実行され、`assert_canary_safe` は catalog の key_pattern だけを許可します。

`effect=keys` と `key_pattern=[">"]` の構文テストが通っても、階段上にいる証拠にはなりません。条件を `player_visible` だけへ弱めると形式上 verify できても安全な降下を検証したことにはなりません。履歴や追加観測による足元の証拠と、その reviewed verifier が必要です。

search の `postconditions=["always"]` も隠し扉・罠の発見や攻略性能の改善を保証しません。発見しない search は画面が変わらない場合もあるため、trace は発火条件と送出キーを確認するだけです。実際の有用性と昇格可否は、別担当による複数 seed の canary・fitness・smoke 検証で判断します。

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

`inventory` を probe した行には、その時点の可視 inventory も含みます。item letter を使う action（`eat_item` など）の trace は、記録された inventory に letter が無いと `precondition_not_met` になります（letter を捏造できない）。tracing は best-effort で、失敗しても episode は継続します。

環境変数は `run_container_worker(..., extra_env={"DOCICH_CANARY_ACTION_TRACE": "1"})` でコンテナへ渡します。`_extra_env_args()` が名前・値・予約 env（`HOME`/`TERM`/`PYTHONDONTWRITEBYTECODE`/`PATH`）を検証します。

## 昇格への使い方（P6d 以降）

action を追加・変更したら:

1. trace 付きで canary を実行し、`verify_trace_file` が全行 `verified` であること。
2. 既存 P5c regression suite が green のままであること。
3. seed 固定の複数 episode で fitness（生存 turns だけでなく depth/探索/terminal を含む退化しない指標）が baseline 比で非退行/改善であること。
4. 既存 smoke gate（production fingerprint 不変 / production_state_touched=false / container cleanup / image provenance）を満たすこと。

単発 canary はノイズが大きい（実測: P5j 746 / P5k 1005 / locked-door 11 / low-HP 493 turns）ので、昇格は複数 episode を要求します。

Relates to #630, #586, #490.
