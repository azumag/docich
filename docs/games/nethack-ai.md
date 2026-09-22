# NetHack AI policy (P3)

Issue #490 の攻略AI。P3では **見えている情報だけを使う層分け**を維持し、危険な選択ほど上位層へ送る。

## 重要な原則

- AIへ渡すのはプレイヤーが端末上で見えている情報だけ。
- process memory、未探索map、未鑑定itemの真のidentity、見えていないmonster等は使わない。
- spectatorのtile分類をAI semantic observationとして使わない。
- P3cでは自動起動を無効にしていたが、本番経路接続段では標準configの `agent.enabled=true / brain=nethack` とする（配備・本番検証は別途）。
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
  prompt                    # more / yes_no / direction / selection / text / unknown / none
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

`hold_low_hp` / `hold_impaired` は従来の観測用intent名として残るが、実行上の選択肢ではない。
完全な gameplay frame でActionが空なら、progress resolverが明示的な `.` に変換する。

隠れた罠など、人間にも見えていない情報は回避できない。P3bは「可視情報から分かる危険を勝手に踏まない」範囲を保証する。

#### 待機の解決（production progress resolver）

現在の実行契約は [本番の行動・待機契約](nethack-progress-contract.md) を正本とする。
探索は縦横を優先し、候補がなければ斜め `y/u/b/n` も調べる。隣接creatureがいる場合、退避を先に試し、
可視の退避先がない場合だけ通常の方向入力1個で接触する。攻撃確認は `n` で拒否し、同じ拒否を繰り返さない。
これは旧P3bからの意図した行動面拡張であり、生存を保証しない。

通常の完全な gameplay frame で、移動・接触・回復計画などの reviewed action がない場合は、判断保留を返さず
`.` を1回送る。ただし重篤状態（`Sick/FoodPois/Ill/Slime/Strngl/Stone/TermIll`）と `Fainting/Fainted/Starved`
は回復手段がレビューされるまで無入力で、空腹側で `.` を送れるのは `Weak` のみである（詳細と条件は
`nethack-progress-contract.md` の優先順位テーブルが正本）。低HPで移動候補がない場合も `.` を1回送る。
`Hungry` では探索を優先し、隣接creatureでは退避・通常接触を先に試し、候補が尽きた時だけ `.` にする。
`Blind/Conf/Stun/Hallu` の安全な移動は作らないが、完全な gameplay frame の無入力にはしない。
`f` は猫科、`{` は噴水である。`f` を地形扱いして休む旧説明・実装は誤りだった。

policyの判断と実行候補を分離し、`last_decision` と `last_progress_decision` で観測する。
候補が全て拒否された場合は、完全な gameplay frame なら `.` を実行候補にし、未知のキーを推測して進めない。
`progress_blocked` の無入力は、質問・未知画面・player不明など `.` を安全に送れない場合に限定する。

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
rest
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

**StrategicProposalは任意のAction列を持たない。** 待機候補 `rest` だけはP3dの最終gateで
NetHackの `.` 1キーへ固定変換する。item使用・階段移動・prompt回答などのstate-changing proposalは、
別executorを実装しない限りゲームへ到達しない。

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

自動actionとして許可するのは、観測条件を満たした次のsurfaceだけ:

```text
1. tactical / advance_message / Space
2. tactical / decline_save / n（canonical ready・境界要求なしを送信直前に確認）
3. midlevel / explore_step / h|j|k|l|y|u|b|n の1キー
4. production / retreat_step または bump_creature / 方向1キー
5. production / rest_turn / . の1キー
6. production / decline_attack / n（共有base policy/canaryには追加しない）
```

`assert_production_safe()` が最終候補のprompt/player/status/targetを再検証する。
強制攻撃、item使用、open、階段コマンド、任意prompt回答はguardを通らない。

## Brain integration

`brain = "nethack"` はCLI NetHack専用。

```text
TTY Observation
  -> normalize_tty
  -> NethackLayeredPolicy
  -> NethackProgressResolver（待機解決・拒否記憶）
  -> observed-context action guard
  -> agent loop / shared_section内でfresh再観測・canonical境界確認
  -> adapter.act成功後だけprogressへ送信通知
```

P3cのstrategic schemaはこの横にある**未接続のadvisory境界**。次の段階でmodel dispatchを追加する場合も、直接agent actionへは繋がずproposal evaluator/executorを別に置く。

標準configは `agent.enabled=true / brain=nethack`。起動ゲートとローカルナレーションも明示有効化する。
設定の省略時は両機能とも無効で、既存の明示brain利用・canary・shadowの意味論を変えない。
本変更はPR段階であり、マージ・VM配備・本番での操作/音声確認は別担当が行う。

### 本番起動ゲート（P3bとは独立）

`[nethack.startup] enabled=true` で `NethackPolicyBrain` の観測直後に実行する。
`NethackLayeredPolicy` と `assert_p3b_safe` は変更しない。
起動キーも通常の `Action` として返すだけで、agent loopのlease fence / coordinator lockを迂回しない。

| 観測 | 応答 |
|---|---|
| `Do you want a tutorial? [yn…]`（map上の質問も含む） | `n` |
| `Shall I pick a character for you? [yn…]` または race/role/gender/alignment版 | `y` |
| `Pick a character? [yn…]` / `Is this ok? [yn…]` | `y` |
| 既知の起動質問を認識済みで末尾が `--More--` | Space |
| gameplay前に `Restoring save file...--More--`（program boundary で保存した run の復元） | Space（1画面1回） |
| normalize_ttyで一意のplayerとHPが見える（起動質問なし） | gameplayへ不可逆移行、以降はP3bだけ |
| 未知画面・質問・save/restore/recover質問（上の復元バナーの `--More--` を除く） | 無入力、policyへも渡さず待機 |
| 60秒 / 40観測 / 12応答のいずれかを消費 | `exhausted`、以降このbrainは無入力 |

時間は初回観測からmonotonic計測、1観測1キーまで。同一画面への再応答は抑止する。
未知画面は上限内なら次の既知画面を待てるが、上限超過は自動リセットしない。
定型質問の語句・疑問符・回答選択肢を照合し、任意プロンプトの推測回答はしない。
英語TTY向けで、ローカライズ版や独自メニューは未対応。
停止時の既存save boundary（キャラ作成中はsaveを要求しない #687）、corner recover（#690）を変更しない。
自動再起動・新run・save復元の質問への回答は追加しない。復元では、gameplay前の固定文言 `Restoring save file...` の `--More--` だけを Space で進める（復元した run にはキャラ作成質問が出ず、これを進めないと地図が描画されず60秒の上限で `exhausted` になり、以後 agent が入力しなくなるため）。

### P3bローカルナレーション（LLM不要）

`[nethack.narration] enabled=true / cooldown_s=20.0 / speaker=""`。
`cooldown_s` は5〜300秒の有限値。speakerは空なら共通audio workerの既定音声、
指定時は64文字以内の英数字と `._:-` のみ。contextは固定 `nethack:policy`。
AGENTSの `work_indicator` 作業中音声や、コーナー開始/終了の `nethack:announce` とは別物。

- `PolicyDecision.intent` と既知reasonを日本語の固定1文へ写す（座標・raw reason・TTY本文は読まない）。
- 初回階層/階層変化、探索の意図、安全な道なし、低HP、空腹、状態異常、接触などを説明する。
  `search`・戦闘・階段コマンド等の未実装操作を実行したとは語らない。contactでは退避・通常接触を先に試し、
  候補が尽きた時だけ `.` を送るため、実際の候補を越えて「休んだ」と断定しない。
- 可視 `You die.` / `You have died.` は死亡表示として説明し、確定した終了結果は既存corner終了音声が担当する。
- 1決定1文まで、全イベント共通cooldown、連続した同一intentは座標/HPが変わっても再発話しない。
  cooldown中のイベントは蓄積/再生しない（次の観測時の現在状態だけを判断）。
- daemon threadは最大1件のenqueueのみ。遅いキューでもactionを待たせず、ローカルbacklogやretryは作らない。
  `enqueue_audio_text` →既存Sorenキュー→audio workerで順次再生する。sinkのpause/dedupeは維持する。
- enqueue失敗や補助処理例外は固定statusだけstderrへ記録し、確定済みdecision/actionはそのまま返す。
  worker再起動で発話抑制memoryは失われ、終了直前のin-flight発話は欠落し得る（best-effort）。
  既に共通キューへ入った音声のゲーム切替時キャンセルは行わない。
- strategist/advisoryの発話設定とは独立。標準configではそちらは無効のまま。
  同時opt-in時は独立cooldownなので重複説明の可能性がある。

運用観測: startupは固定 `state=pending/answering/waiting/unknown/gameplay/exhausted`、
ナレーション失敗は固定 `status=delivery_failed/consider_failed` をagent stderrへ出す。
本文・例外文字列・機密はログへ追加しない。新しい常駐worker/queue laneはなく、既存agent/audio workerを利用する。
owner-only diagnosticsの既存corner/worker/queue coverageは変更しないが、このbrain内部状態のcollector公開は未実装。
agent生存だけではgameplay到達や音声再生の証拠にならない。

無効化は `agent.enabled=false`（自動agent全体）、`nethack.narration.enabled=false`（発話のみ）、
`nethack.startup.enabled=false`（追加起動応答のみ）。設定はbrain生成時に読むので既存workerへhot reloadしない。
本番反映後は別担当が正規運用経路で新agentの実効設定、gameplay到達、P3bの移動/`.` 待機、
音声の順次再生と終了復帰、共通配信PID維持を確認する必要がある。
P3bは安全性を確認できる局面で `.` 待機しますが、重い状態異常・`Fainting` 以上の空腹・隣接creatureでは
fail-closed で無入力になるため、これだけで長期攻略が完走するとは主張しない（`Weak` だけは明示 `.` を送る）。

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
