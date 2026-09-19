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

#### 保留のあいだもターンを進める（rest）

NetHackはターン制なので、agentが何もしない間はゲーム内で何も変わらず、同じ画面に対して同じ保留が永久に返る
（ペット相当の曖昧なglyphが唯一の通路を塞ぐ、HPは時間でしか回復しない、など。本番で実際に停止した）。
そこで production agent は、次の**中位・LLM不要の保留**で policy が無入力、playerが一意、promptなし、かつ認識済みの隣接creatureがいない場合に限り、reviewed な単一キー `.`（1ターン休む）を実行する。

| policy の判定 | 実行する入力 |
|---|---|
| `exploration_blocked` / `hold_low_hp` / `hold_impaired` / `seek_food`、かつ認識済み隣接creatureなし | `.` |
| `assess_contact`、または上記 hold でも認識済み隣接 creature あり | **休まず**、explorer の安全な1歩（`h`/`j`/`k`/`l`）。無ければ無入力 |
| `survival_emergency` / `status_emergency`、かつ認識済み隣接creatureなし | `.`（時間経過でしか回復しないため） |
| `food_emergency`（休むと空腹が進み、食事は安全面の外） | 無入力のまま |
| `inspect_screen`（playerが一意でない）・プロンプト表示中 | 無入力のまま（`.` を誤入力させない） |

`Hungry` / 低HP / 状態異常はpolicy上、contact判定より先に決まる。そのため `rest_action_for_hold()` 自身が `visible_neighbors()` を再確認し、これらのhold名になっていても隣に認識済みcreatureが見えていれば `.` を送らない。敵味方不明の隣接相手へ無入力のまま1ターン渡して攻撃を受けることを、stall解消だけを理由に許可しない。

緊急（`survival_emergency` / `status_emergency`）は本来 strategist の回復計画（薬・祈り・逃走）が要るが、strategist 未設定では「無入力」= ターン制では永久停止になる（2026-09-19 本番 HP 4/16 で実測、stall guard に切られるまで `0 件のアクション` を繰り返した）。`.` は計画の代わりにはならないが、HP と多くの状態異常は時間経過でしか回復しないため、凍り付くよりはよい。空腹だけは休むと悪化するので除外する。

隣接 creature がいる hold は「休まない」だけでは**永久停止**になる。ターン制なので、ヒーローが動かなければ
その creature も手番を得ず、画面は一切変化しない（2026-09-19 本番 generation 250 で実測: HP 4/16・`:` が斜め隣・
90秒間フレーム完全同一・`0 件のアクション` が 63 行）。そこで休む代わりに **explorer が選んだ安全な1歩**を実行する。
explorer は creature・アイテム・罠・扉・未知マスの上へは踏み込まないので、P3b の reviewed な移動面のままである。
安全な1歩が無ければ（壁で囲まれている等）従来どおり無入力で、これは嘘のない「本当に打つ手が無い」状態である。
`food_emergency` と `inspect_screen` は対象外。`assert_step_out_safe` は `h`/`j`/`k`/`l` 1個以外を拒否する。

policy 自身の判定（`PolicyDecision`）は変えず、agent brain が実行する action だけを差し替える。
strategy / advisory / shadow は従来どおり保留として観測する。`assert_rest_safe` は「`.` 1個だけ」以外を拒否する。
連続して休み続けても TTY が変化しなければ、既存の stall guard（`stall_timeout_minutes`）がコーナーを終了する。

注意: 観測はプレーンテキストで色を失うため、`f` は色によって噴水・猫（ペット/敵）のどれにもなり、区別できない。
planner はどれであっても `f` の上へは踏み込まない一方、現行のcontact判定では `f` をcreature確定扱いしない。そのため、唯一の通路を `f` が塞ぐ既知のproduction stallではbounded restが引き続き可能。色を使った判別は今後の課題。

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

P3b/P3c時点で自動actionとして許可するのは、明示された小さいsurfaceだけ:

```text
1. tactical / advance_message / Space
2. tactical / decline_save / n
3. midlevel / explore_step / h|j|k|l の1キー
4. production hold fallback / . の1キー
```

4はpolicy actionそのものではなく `rest_action_for_hold()` によるbounded fallbackで、prompt/player/contact条件を別途確認し `assert_rest_safe()` が`.`以外を拒否する。攻撃、item使用、open、階段コマンド、任意prompt回答等はまだguardを通らない。

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
  `search`・戦闘・階段コマンド等の未実装操作を実行したとは語らない。contactや低HP等では実際にrestが抑止される場合があるため、「休んだ」と断定しない。
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
本番反映後は別担当が正規運用経路で新agentの実効設定、gameplay到達、P3bの移動/保留、
音声の順次再生と終了復帰、共通配信PID維持を確認する必要がある。
P3bは敵・ドア・空腹などで保留するため、これだけで長期攻略が完走するとは主張しない。

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
