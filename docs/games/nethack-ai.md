# NetHack AI policy (P3)

Issue #490 の攻略AI。P3では **見えている情報だけを使う層分け**を維持し、危険な選択ほど上位層へ送る。

## 重要な原則

- AIへ渡すのはプレイヤーが端末上で見えている情報だけ。
- process memory、未探索map、未鑑定itemの真のidentity、見えていないmonster等は使わない。
- spectatorのtile分類をAI semantic observationとして使わない。
- `config/games/nethack.toml` の `agent.enabled=false` はP3cでも変更しない。
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

### Mid-level (P3b)

`src/docich/nethack_exploration.py` は現在見えているmapだけをBFSし、1 observationにつき最大1歩だけ返す。

自動で通行可能と扱うglyph:

```text
.   floor
#   corridor
<   upstairs tile
>   downstairs tile
```

上下階段の **マスへ歩くこと** は可能だが、階段コマンド `<` / `>` 自体は送らない。

自動で踏み込まないもの:

```text
letters / creature glyphs
items
^ trap
+ closed door
blank / unseen area
その他unknown glyph
```

探索memoryはvisible `Dlvl` ごとにvisible cellとvisit countを保持する。frontier候補は visit count → distance → stable coordinates の順で決める。

探索を止める条件:

- `Hungry` → `seek_food`
- HP <= 50% → `hold_low_hp`
- `Blind` / `Conf` / `Stun` / `Hallu` → `hold_impaired`
- 隣接creature → `assess_contact`
- player `@` が一意でない → `inspect_screen`
- safe pathなし → `exploration_blocked`

隠れた罠など、人間にも見えていない情報は回避できない。P3bは「可視情報から分かる危険を勝手に踏まない」範囲を保証する。

### Strategic (P3c)

P3cは **入力/出力schemaを作るだけ**で、まだモデル呼び出しもproposal実行も行わない。

#### Visible inventory parser

NetHack 5.0の通常UIが表示するinventory letter付き行だけを `src/docich/nethack_inventory.py` で読む。

保存するもの:

- inventory letter
- 画面に出たdescriptionそのもの
- visible quantity
- visible `blessed` / `uncursed` / `cursed` word（無ければ `unknown`）
- visible equipped annotation
- visible `unpaid`
- description中の単語から作るcoarse `category_hint`

特に:

```text
c - a potion called cloudy
```

を見ても、`cloudy` が実際に何のpotionかを補完しない。`true_identity` のようなfield自体をschemaへ持たない。

#### StrategicRequest

`build_strategic_request()` は:

```text
intent / reason
public observation summary
visible inventory summary
constraints
```

だけをJSON互換objectへする。constraintには「hidden stateを仮定しない」「未鑑定品の真のidentityを仮定しない」「proposalは助言であり直接実行しない」を含める。

#### StrategicProposal

将来LLMが返す候補schema:

```text
hold
inspect
move_to_stairs
ascend
descend
consume
equip
use
answer_prompt
```

`consume/equip/use` はinventory letter必須、`answer_prompt` は短いprompt answer必須。余計なinventory letterやprompt answerを別kindへ混ぜるとvalidation errorにする。

**StrategicProposalはActionを持たない。** P3cではproposalからNetHack keyへ変換するexecutorを実装しない。従ってLLMを将来接続しても、それだけではitem使用・階段移動・prompt回答はゲームへ到達しない。

#### Narration threshold

`should_narrate()` は毎歩しゃべらないための土台。

- strategic / `requires_llm=true`
- survival/status/food emergency
- prompt decision
- contact assessment
- exploration blocked
- intentが変化したとき

だけを主な読み上げ候補にする。通常の `explore_step` は読み上げない。

## Fail-closed action guard

P3b/P3c時点で自動actionとして許可するのは:

```text
1. tactical / advance_message / Space
2. midlevel / explore_step / h|j|k|l の1キー
```

攻撃、item使用、open、階段コマンド、prompt回答等はまだguardを通らない。

## Brain integration

`brain = "nethack"` はCLI NetHack専用。

```text
TTY Observation
  -> normalize_tty
  -> NethackLayeredPolicy
  -> reviewed-action guard
  -> agent loop
```

P3cのstrategic schemaはこの横にある**未接続のadvisory境界**。次の段階でmodel dispatchを追加する場合も、直接agent actionへは繋がずproposal evaluator/executorを別に置く。

標準configはまだ `enabled=false / brain=random` のままなので、これらのコードをマージしただけでは本番AI操作は開始しない。

## 次

P3d:

- strategic model dispatch（timeout / fallback / budget）
- proposal evaluator
- inventory letterが現在も同じitemを指すことの再確認
- stairs progression rule
- item risk / equipment comparison
- narration delivery
- executorごとの明示allowlist

P4:

- NLE/maintained fork等のstructured observation比較
- hidden information leak test
- TTYとstructured sourceを同じnormalized schemaへadapter化

P5:

- death retrospective
- lessons memory
- repeated-death detection
- versioned policy config
- regression evaluation + promote/rollback
