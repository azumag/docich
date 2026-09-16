# NetHack AI policy (P3)

Issue #490 の攻略AI。P3では **見えている情報だけを使う層分け**を維持し、危険な選択ほど上位層へ送る。

## 重要な原則

- AIへ渡すのはプレイヤーが端末上で見えている情報だけ。
- process memory、未探索map、未鑑定itemの真のidentity、見えていないmonster等は使わない。
- spectatorのtile分類をAI semantic observationとして使わない。
- `config/games/nethack.toml` の `agent.enabled=false` はP3bでも変更しない。
- 自動操作は明示的にレビュー・テストした小さいsurfaceだけを許可する。

## Normalized observation (P3a)

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

低遅延・deterministic。

実装済み:

- `--More--` → Space

### Mid-level

P3bで **保守的な可視地形探索** を追加した。

`src/docich/nethack_exploration.py` は現在見えているmapだけをBFSし、1 observationにつき最大1歩だけ返す。

自動で通行可能と扱うglyph:

```text
.   floor
#   corridor
<   upstairs tile
>   downstairs tile
```

上下階段の **マスへ歩くこと** は可能だが、階段コマンド `<` / `>` 自体は送らない。階層移動は長期進行判断なので後続のstrategic policyに残す。

自動で踏み込まないもの:

```text
letters / creature glyphs
items
^ trap
+ closed door
blank / unseen area
その他unknown glyph
```

探索memoryはvisible `Dlvl` ごとに持ち、visible cellとvisit countを記録する。frontier候補は:

1. visit countが少ない
2. 現在地から近い
3. 座標順（replayをdeterministicにするtie-break）

の順で選ぶ。

ただし次の場合は探索actionを出さない。

- `Hungry` → `seek_food`
- HP <= 50% → `hold_low_hp`
- `Blind` / `Conf` / `Stun` / `Hallu` → `hold_impaired`
- 隣接にcreature glyph → `assess_contact`
- player `@` が一意に特定できない → `inspect_screen`
- safe cardinal pathが無い → `exploration_blocked`

隠れた罠など、人間にも見えていない情報は当然このpolicyでも回避できない。P3bは「可視情報から分かる危険を勝手に踏まない」範囲を保証する。

### Strategic

まだLLM call自体は行わず、`requires_llm=true` のdecisionとして上位層へ渡す。

- HP <= 25%: `survival_emergency`
- severe visible status: `status_emergency`
- Weak/Fainting等: `food_emergency`
- yes/no / direction / selection / naming prompt: `prompt_decision`

未鑑定品、装備、branch progression、店、祭壇、階段を降りる判断等もP3c以降でここへ入れる。

## Fail-closed action guard

P3bの `assert_p3b_safe()` が許可する自動actionは2種類だけ。

```text
1. tactical / advance_message / Space
2. midlevel / explore_step / h|j|k|l の1キー
```

攻撃コマンド、item使用、open、階段コマンド、prompt回答等をpolicy実装途中で誤って返すとguardで拒否する。

## Brain integration

`brain = "nethack"` はCLI NetHack専用。

```text
TTY Observation
  -> normalize_tty
  -> NethackLayeredPolicy
  -> reviewed-action guard
  -> agent loop
```

標準configはまだ `enabled=false / brain=random` のままなので、これらのコードをマージしただけでは本番AI操作は開始しない。

## 次

P3c:

- inventory/menu parser
- item risk model
- equipment comparison
- stairs/branch progression decision
- strategic LLM request/response schema
- narration threshold
- LLM不調時のfail-closed fallback

P3d/P4:

- NLE/maintained fork等のstructured observation比較
- hidden information leak test
- TTYとstructured sourceを同じnormalized schemaへadapter化

P5:

- death retrospective
- lessons memory
- repeated-death detection
- versioned policy config
- regression evaluation + promote/rollback
