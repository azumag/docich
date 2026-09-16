# NetHack AI policy (P3)

Issue #490 の攻略AI。P3aでは **観測契約と安全な層分け**だけを先に固定し、まだ自動探索・自動戦闘は有効化しない。

## 重要な原則

- AIへ渡すのはプレイヤーが端末上で見えている情報だけ。
- process memory、未探索map、未鑑定itemの真のidentity、見えていないmonster等は使わない。
- spectatorのtile分類をAI semantic observationとして使わない。
- `config/games/nethack.toml` の `agent.enabled=false` はP3aでも変更しない。
- P3aの `brain=nethack` が自動送信できる操作は **visible `--More--` に対するSpaceだけ**。

## Normalized observation

`src/docich/nethack_observation.py` がTTYを以下へ正規化する。

```text
NethackObservation
  message
  map_rows                 # visible raw glyphs
  status_lines
  player=(x,y) | None
  vitals
    hp / hp_max / hp_ratio
    power / power_max
    ac
    experience_level
    dungeon_level
    gold
    turn
  conditions               # visible status words only
  prompt                    # more / yes_no / direction / selection / text / none
  local_map(radius=2)
  visible_neighbors()
```

`public_summary()` は将来のLLM input用の小さなJSON互換summaryを返す。raw full screenを毎回LLMへ投げず、必要な局所情報だけを渡す土台にする。

## 3層policy

### Tactical

低遅延・deterministic。P3aで唯一実装済みの自動操作:

- `--More--` → Space

今後ここへ追加する候補:

- 明白に安全なmenu操作
- tested escape action
- 敵味方が構造化観測で確定した場合の単純戦闘

### Mid-level

P3aではintentだけを生成し、まだキーを送らない。

- `explore`
- `seek_food`
- `assess_contact`
- `inspect_screen`

探索memory、visited map、stairs、resource threshold等はP3b以降。

### Strategic

P3aでは `requires_llm=true` として上位層へ渡すだけで、LLM call自体はまだ行わない。

- HP <= 25%: `survival_emergency`
- severe visible status: `status_emergency`
- Weak/Fainting等: `food_emergency`
- yes/no / direction / selection / naming prompt: `prompt_decision`

未鑑定品、装備、branch progression、店、祭壇等も後続でここへ入れる。

## Fail-closed action guard

`assert_p3a_safe()` はP3aから返る自動actionを検査する。

許可されるのは:

```text
layer=tactical
intent=advance_message
Action(type="text", text=" ")
```

だけ。

このguardにより、後続実装の途中で誤って移動・攻撃・item使用を返してもテスト/実行時に止まる。

## Brain integration

`brain = "nethack"` を選択可能にするが、標準configはまだ `enabled=false / brain=random` のまま維持する。

P3a brainは:

1. CLI ObservationのTTY textをnormalize
2. layered policyへ渡す
3. `assert_p3a_safe()`
4. safe actionだけagent loopへ返す

したがってbrainの存在だけで自動攻略が開始されることはない。

## 次

P3b:

- visited/exploration memory
- local path planning
- stairs discovery
- HP/hunger/resource threshold
- safe retreat
- deterministic movement actionの評価テスト

P3c:

- inventory/menu parser
- item risk model
- equipment comparison
- strategic LLM request schema
- narration threshold

P3d/P4:

- NLE/maintained fork等のstructured observation比較
- hidden information leak test
- TTYとstructured sourceを同じnormalized schemaへadapter化
