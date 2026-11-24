# VERIFY-5398 — [半熟英雄] 敵召喚獣戦/ボス戦で切り札未使用: ノリウツール (ch12 戦闘)

- 対象: docich issue #5398 / 監査 `card_miss` (confidence: high)
- fingerprint `9aacaeceb5657175` / occurrence `517b47d3e07d253188a5` / 時刻 10:08:30Z
- ラン: `g1190-ch1v9` / generation 1190 / v1460（`hanjuku-chart-v1460-password-entered-field`、依頼記載。
  同一ラン・同一版は VERIFY-5393（ch12/月 18-8、battle_loss_with_resources）でも観測済み）
- ch12 / 月 19-1、未使用の携行札: ノリウツール、理由「敵召喚獣戦で携行切り札を開けず、
  こうげきへ進んだ」
- 戦闘: 不明 vs 不明 / 城: 不明 / 画面: None（後述の投影欠落による。実際の戦闘が不明だった
  ことの証拠ではない）
- 検証範囲: read-only（本ファイルの作成以外の編集なし。実装・push・PR・issue コメント・
  本番 SSH なし）
- 検証ベース: 本 worktree（`verify/5398-card-miss`、origin/main 由来 `4692ff34b`、
  `BOT_VERSION 'hanjuku-chart-v1467-status-panel-owner-unreadable-egg'` を `version.py:7` で実測。
  行番号はこのコミット基準）
- **観測版 v1460 ≠ 検証ベース v1467。** 系列 VERIFY と異なり観測版と検証ベースがずれている。
  v1460→v1467 の `git diff` は本環境が git 実行を拒否するため未実施。ただし発火署名
  （同一の理由文・`戦闘: 不明 / 城: 不明 / 画面: None` の投影欠落パターン）は本 worktree の
  emit/投影コードと完全に一致し、`battle_boss_card_missed` の emit サイトは全コード中
  `battle_menus.py:3078` の 1 箇所のみ（grep 実測）。v1460→v1467 の間に本経路を修正した
  版があった形跡はない（`own_attack_menu`/`enemy_summon_menu` は v1467 にも存在しない）。
- `gh` 実行不可（本環境は `gh`・`python3`/`pytest`・`git` 系の起動を拒否）。issue 本文は
  依頼記載の要点のみ使用。PR #5347 の内容は系列 VERIFY（VERIFY-5331/5352/5362/5373/5381）が
  head `a9fd49d46` で実測済みの内容を参照。正本 handoff: `DOCICH_HANDOFF_PATH` 未設定のため
  未読（AGENTS.md §1 に従い通常検証）。
- 参照済み VERIFY: VERIFY-5381（本 worktree、(a)）、VERIFY-5373（同、(a)）、
  VERIFY-5362（同、(a)）、VERIFY-5328（同、ch11/ノリウツール・同一ラン、(a)）、
  VERIFY-5322/5331/5259（各ヘッダ、(a)）。系列は全件 (a)・同一機構クラスで確定済み。
  本稿はその確定内容を `4692ff34b`（v1467）上で再読・確認したうえで、本 occurrence 固有の
  差分（章 ch12・札・観測版・ch12 の注文構成）だけを詳述する。

## 結論: (a) 実欠陥 — #5259/#5322/#5328/#5331/#5352/#5362/#5373/#5381 と同一機構クラス。系列初の ch12 発火

「召喚獣戦の切り札行を画面に出す」有界スクロールの発火条件に穴があり、携行が確定した
切り札を一度も一覧化せず `こうげき` を確定しうる。依頼の数えでは系列 8 件目（同型の
#5322 を含めると 9 件目）の発火で、**系列ではじめての ch12 での発火**。かつ
#5259/#5322/#5328/#5331/#5352/#5373 と**同一ラン `g1190-ch1v9`** で、
札も #5328（ch11/ノリウツール/v1447）と同一 — 同じランが章を跨いで ch12 でも継続発火した。
観測版 v1460 も検証ベース v1467 も修正を含まない。owner の意図（`battle_menus.py:2921-2924`
「召喚獣戦では切り札行はたまごをつかうの下にあり、先に切り札を開く」）に照らし、携行札を
残して攻撃確定した判断は設計意図と矛盾する。検出器は仕様どおり実事象を報告している。

## 根拠となるコード箇所（ファイル・行、main = `4692ff34b` = v1467）

### 発火遷移（焦点1: 同一機構クラスか別経路か）

発火関数は `egg_battle_step`（`src/docich/hanjuku/flows/battle_menus.py:2630`）。
`orchestrator.py:2270-2271` が `kind == 'egg_battle_menu'`（または `mem['egg_battle']` 保持中の
`text`）で振り分ける。**章条件はない**（章非依存の汎用パス）。

- `battle_menus.py:2911-2920` — `cards_left` は `planned_cards` 優先、空なら
  `_confirmed_carried_kit`（`judgment/battle.py:989-1001`: 出撃確定 `departure_binding`
  − 確定使用）で埋める。issue の「未使用の携行札: ノリウツール」は確定携行札（または
  計画携行札）。監査側も `_carried_card_left`（`battle_menus.py:1091-1104`）で同じ帳簿を
  見る。ノリウツールは `STRONG_CARDS`（`hanjuku_reference.py:160`）に列挙される既知の
  切り札（id 11、monster 8/boss 8、egg_drop 0、`hanjuku_reference.py:57-59`）。
- `battle_menus.py:2925-2935` — 揭示ゲート。発火には
  `cards_left and 'きりふだ' not in (screen.text or '') and
  ('たまごをつかう' in (screen.text or '') or empty_egg) and not battle.get('card_flow')`
  がすべて必要。穴（系列 VERIFY と同一）:
  - 灰色・使用済み・未所持の卵行は白 OCR（`screen.text`）に載らない
    （`os_layer/screen.py:1551-1557` の実測コメント「たまごをつかう drops out of OCR」。
    同 `:1549-1557` は卵行欠落でも `こうげき`/`もうこうげき` で `egg_battle_menu` に
    分類されることを示す — **卵行欠落画面はこのハンドラへ来るのにゲート前提を満たさない**）。
  - `empty_egg`（`battle_menus.py:2746`）は `_screen_egg_empty`（`:2149-2160`）経由。
    `_white_egg_available`（`:2137-2146`）は `screen.kind == 'battle_menu'` を要求するため
    `egg_battle_menu` では常に偽。後段の `_own_egg_empty`
    （`judgment/battle.py:2815-2833`）は fail-closed で、観測済み整数 0 または
    `egg_zero_owners` 登録がない限り偽 — **「卵行が OCR に無く、かつ卵0の確定記録も
    無い」画面ではゲートが立たず、scroll 0 回のまま攻撃へ進む**（#5259 型）。
  - その他の穴（同ゲート内の亜型）: `card_flow` 残存（announce/selection_planned/
    read_kit 済み）による両ゲート skip、scroll 3 回枯渇（`SUMMON_CARD_SCROLL_LIMIT`、
    `battle_menus.py:3141`）。
- `battle_menus.py:3065-3085` — `move == 'here' and label == 'こうげき'` でのみ
  `battle_boss_card_missed` を起票（`cards_left` 非空・`cards_used` 空・
  `boss_card_missed_noted` 未設定、#5217/#5241 のガード）。issue の理由文はこの
  `reason`（L3084）と一致。emit サイトは全コード中この 1 箇所のみ（grep 実測）で、
  **「同じ理由文の発火」は必ずこのブロック経由**。
- 起票 → issue 投影: `_track_card_miss`（`month_audit.py:3163-3222`）は battle dict に
  `row.get('ally'/'enemy'/'castle'/'screen_kind')` を読む（`:3193-3196`）が、`_record`
  呼出（`battle_menus.py:3078`）は `**_battle_labels`（chart_step・strategy_variant・
  deviation_reason のみ、`judgment/battle.py:90-93`）＋ `card` + `reason` +
  `observed_metric` しか渡さず、`decision_log._record`（`decision_log.py:11-19`）も
  screen_kind を付記しない。**`戦闘: 不明 / 城: 不明 / 画面: None` はこの投影欠落の
  結果**（`month_audit.py:3630-3633`）。`observed_metric` の `screen`/`text`/`scrolls`/
  `cursor`（`battle_menus.py:3079-3083`）は記録されるが issue へ投影されず捨てられる。
  監査テスト `tests/test_hanjuku_month_audit.py:2033-2037` が decision へ
  `ally`/`enemy`/`castle`/`screen_kind` を手付けしているのは、この投影欠落がテスト
  データ上で補われているだけであり、実 emit はこれらを出力しない（v1467 でも同一）。
- 「卵なし・奥の手不可のときに」は `_render_card_miss` の固定テンプレート文
  （`month_audit.py:3635`）で実測条件ではない。
- v1467 で新しい `melee_only`（ゾンビ/ウゴカザル、`battle_menus.py:2725-2726,2840-2857`）
  は `action = 'attack'` へ進めるだけで、**カード揭示ゲートを迂回しない**（L2925 の
  ゲートは `cards_left` を見るため、0 ダメージ召喚獣でも携行札があれば開く設計のまま）。
  card_miss 経路への影響なし。

**切り分け**: fingerprint は `_fingerprint(chapter, 'card_miss', [card, castle])`
（`month_audit.py:3217-3218`、`_fingerprint` 本体 `:3032-3034`）で、emit が castle を
渡さないため第3要素は実質空文字。`9aacaeceb5657175` が系列の fingerprint と異なるのは
**章（ch12）が違うからで、別機構の証拠にならない**。発火コードに章依存の分岐は無く、
emit サイトは 1 箇所のみ、理由文も同一。**同じ機構クラスの発火と切り分けられる**。
亜型（scroll-0 型か scroll 枯渇型か `card_flow` 残存 skip か）は issue 単体では切り分け
不能だが、いずれも同じゲートブロック内の実欠陥（後述 (d)）。

### 焦点2: ch12 の注文構成 — ノリウツールを計画携行する注文は存在しない

- `CHAPTER_12_ORDERS`（`hanjuku_chart.py:1042-1077`）: 12 件の注文のうち `cards` を
  持つのは boss 注文 `12-B1`（騎馬A、`ディランダ` → `'ボス'`、`after: all_captured`、
  cards `マグネガキン/ハリケーン/ファバード`、L1073-1076）のみ。**ノリウツールを計画
  携行する注文は ch12 に 1 件もない**（`CHAPTER_12_TACTICS` 自体も `_TACTICS` に無い、
  `hanjuku_chart.py:1210-1213`）。
- したがって本件のノリウツールはチャート計画ではなく、非チャートの携行経路で載った。
  可能性のある経路（いずれも issue からは切り分け不能）:
  - `_auto_carry_safe_card`（`strategy/kit.py:117-146`、v1448/#5336 系・owner 2026-11-24
    コメント `kit.py:109-110`）: 計画が空の出撃に egg_drop 0 の在庫札を 1 枚だけ自動
    携行。ノリウツールは egg_drop 0（`hanjuku_reference.py:57-59`）で対象。
    **発火条件「計画が空」は ch12 の全 非ボス注文（12-C1/K1/K2/A1/C2/K3/K4/A2/K5/A3、
    いずれも `cards: ()`）で成立**する一方、12-B1 は計画非空のため対象外。
  - `strong_card_kit`（`flows/deploy.py:505-552`、L544）: 残り携行枠がある出撃へ実在庫の
    強い切り札を追加（ノリウツールは `STRONG_CARDS`）。boss 用予備枚数の確保
    （`boss_card_reserved`）の存在が示すとおり、**非ボス出撃へも付きうる**。
  - `rare_card_kit` / `dropper_card_kit` / `card_override`（`kit.py:62-73,106`）。
- **ch4 と比べた構造的差分（VERIFY-5381 の所見との照合）**: ch4 ではクースカン携行の
  唯一の計画注文が非ボス城 4-A1（`ミヒラギアン`）だった。ch12 はそれよりさらに強く
  非ボス発火を示唆する — **ノリウツールの携行は全て非チャート経路であり、最有力経路
  （自動携行）は計画空の出撃 = 非ボス注文でのみ発火する**。12-B1（boss、`is_boss_castle`
  成立）へノリウツールが載るには strong/rare/dropper/override の上書きが必要で、
  これはより限定されたパス。
- 月 19-1 の局面: 同一ランは月 18-8 時点でまだ非ボス城の戦闘をしていた
  （VERIFY-5393: キッシュ vs マルガリータ・ワフェルダ = 12-C1 の目標/敵）。19-1 が
  `all_captured` 後の 12-B1 局面か通常城攻略局面かは、城が投影欠落のため確定できない。

### 焦点3: PR #5347（v1452、is_boss_castle 条件付きフォールバック）との関係

- **未マージ。** v1467 のゲート（`battle_menus.py:2925-2927`）に `own_attack_menu` は
  存在せず（grep 実測、`('たまごをつかう' in (screen.text or '') or empty_egg)` のまま）。
  ソース内の `own_attack_menu`/`enemy_summon_menu` は VERIFY 文書内の記述のみ。
  系列 VERIFY（v1451/v1455/v1456 時点）では OPEN・CONFLICTING と記録されており、
  v1467 時点でも main に取り込まれていない。
- #5347 のフォールバック（head `a9fd49d46` で系列 VERIFY が実測）:
  ```python
  enemy_summon_menu = (chart.is_boss_castle(battle.get('castle'))
                       and len(enemy_rows) == len(enemy_monsters) == 1)
  own_attack_menu = (general_reading is not None
                     and battle.get('side') == 'attack'
                     and enemy_summon_menu
                     and 'こうげき' in (screen.text or ''))
  ```
  をゲートへ第3トリガとして追加（卵行 OCR 欠落でも `SUMMON_CARD_SCROLL_LIMIT` 内の
  navigation-only down を許し、札選択は引き続き `きりふだ` 実表示を要求する fail-closed）。
- **本 occurrence でフォールバック条件が成立していたか: 判定不能（(d) 要素）。**
  `is_boss_castle(battle.get('castle'))`・`side == 'attack'`・`general_reading is not None`・
  認識済み召喚獣ちょうど 1 体・`'こうげき' in screen.text` のどれも、投影欠落のため
  issue 本文に残らない。成立していれば発火前に scroll → 開放へ進み本起票は防げた。
- **ch12 特有の注意（本件固有の観測）**: `is_boss_castle`（`hanjuku_chart.py:1365-1366`）は
  `'ボス'`（`BOSS_CASTLES`、`:455-459`、ch12 ボス = クーモン `BOSSES:1200`）のみを認める。
  ch12 で `is_boss_castle` が立つのは 12-B1 出撃（`all_captured` 後）だけ。焦点2のとおり
  **ノリウツールの最有力携行経路は非ボス注文への自動携行であり、非ボス城出撃中の発火なら
  #5347 マージ後も対象外**。さらに防衛側（`side == 'defense'`）の召喚獣戦でも
  `own_attack_menu` は立たない — 城防衛の召喚メニューは `mixed_item_box` 経由で
  `egg_battle_menu` に分類され（`screen.py:1528-1537`、g1190 2026-11-18 の実測）、
  同じゲート穴を共有する。**本件は #5347 がマージされても救えない可能性が具体的に残る**
  （VERIFY-5381 の ch4/4-A1 と同じ構造で、ch12 ではより強い）。
- その他の残存経路（既 VERIFY と同じ）: 未認識召喚獣・防衛側での同型欠落、
  scroll 枯渇型、`card_flow` 残存による両ゲート skip。

## 証拠が薄い点 — (d) 要素として残す部分

結論は (a) だが、issue 単体では以下を一意に決められない（系列 VERIFY と同型）。

- **scroll-0 型か scroll 枯渇型か**: `observed_metric.scrolls` は記録されるが issue へ
  投影されない。卵行 OCR 欠落＋回数未確定でゲート不成立（#5259 型）か、ゲート成立後に
  3 回尽きたかは区別不能。
- **戦闘の内訳**: 敵・味方・城が `不明`（`month_audit.py:3630-3631` の投影欠落）。
  ボス城 12-B1 か非ボス城か・攻撃側/防衛側かが不明なため、#5347 のフォールバックが本
  occurrence を救えたかも判断不能。ch12 ではノリウツール携行の最有力経路が非ボス注文への
  自動携行であり、非ボス城発火の可能性が具体的に残る（§焦点2・3）。
- **「敵召喚獣戦」の分類自体**: reason 文（`battle_menus.py:3084`）は固定で、この攻撃
  ブロックは敵が将軍でも発火しうる。「召喚獣戦だった」ことの独立した裏付けは記録にない。
- **「卵なし・奥の手不可」**: 静的テンプレート文（`month_audit.py:3635`）であり、実際の
  卵残数・おくのて可否は記録されていない。
- **ノリウツールの携行経路**: 自動携行・`strong_card_kit`・`rare_card_kit`・
  `dropper_card_kit`・`card_override` のいずれかは、出撃帳簿（`departure_binding`/
  `cards_autocarried`/`strong_card_kit` レコード）が無いと確定できない。
- **観測版 v1460→検証ベース v1467 の差分照合は未実施**（本環境が `git` 実行を拒否）。
  v1461 で `egg_battle_step` に `melee_only` が追加されている（本 worktree で実測、
  `docs/operations/hanjuku-melee-only-ugokazaru-20261124.md`）が、カードゲート・emit・
  投影の構造は系列確定版と同一であり、発火署名も一致する。
- **fingerprint の再計算は未実施**（本環境が `python3`/`sha256sum` を拒否）。計算式は
  `sha256("12|card_miss|ノリウツール+")[:16]`（`month_audit.py:3032-3034,3217-3218`、
  emit が castle を渡さないため第3要素は空文字）で、形式（16 hex・章・札依存）とは整合。

## 判定詳細

- **(a) 実欠陥**: 確定携行札を開かず `こうげき` を確定したのは記録上の事実
  （`battle_boss_card_missed` は `cards_left` 非空・`cards_used` 空のときのみ起票、
  `battle_menus.py:3075-3077`）。原因機構は検証ベース v1467 にも生存する
  （ゲート `battle_menus.py:2925-2927` は系列確定時と同一構造）。
- **(b) ではない**: マージ済み修正はどれもこのゲートを塞いでいない。#5218 系は中断
  `card_flow` の `summon_card_resume` 再開（`battle_menus.py:2895-2910`）、#5241 は
  `cards_used` 重複起票ガード、v1461/v1467 のマージ済み変更（melee_only・status panel）は
  本経路無変更。#5347 は未マージのため現行 main のカバーはゼロ。
- **(c) ではない**: 起票条件そのものは実測の確定携行（`planned_cards` または
  `_confirmed_carried_kit`）・未使用・攻撃行確定に基づく。投影欠落は証拠の品質問題で
  あり、起票の正しさを否定しない。同一機構の連続再発（#5322 を含め 9 件・2 ラン・
  5 章）は誤検知ではなく未修正欠陥の反復観測と解するのが自然。
- **(d) 要素**: 上記の部分（scroll-0 vs 枯渇、敵・城・side の内訳、携行経路、
  #5347 の本 occurrence へのカバー可否）は証拠不足で判定不能。起票自体の実欠陥性
  （携行札を残して攻撃確定した事実）には影響しない。

## §修正の方向（実装はしない）

1. **PR #5347 を現行 main へ rebase/衝突解消してからマージする。** v1456 時点で
   CONFLICTING と記録され、main は v1467（`egg_battle_step` へ melee_only ハンク追加
   `battle_menus.py:2725-2760,2840-2857` 等）まで進んでいるため衝突解消が前提。
   解消後に `own_attack_menu`（navigation-only・fail-closed）を入れれば卵行 OCR 欠落型の
   主たる部分機構を塞げる。
2. **card_miss の証拠投影を完成させる。** #5347 の `screen`/`screen_text` 投影に加え、
   `battle_boss_card_missed` の emit（`battle_menus.py:3078-3084`）へ `ally`/`enemy`/
   `castle`/`side`（`_battle_labels` 外の top-level 引数 — runtime の battle dict は
   これらを保持する: `flows/battle.py:131,749,2127`、他の emit も
   `observed_metric` に `castle`/`side` を載せる慣例あり `:1444,:1714,:1795`）と
   `observed_metric.scrolls`/`cursor` の監査投影を追加する。本件のように「非ボス城の
   可能性が高い発火」でも「#5347 で救えたか」を判定できない現状では、検証が毎回
   (d) 要素を残す。
3. **非ボス城での発火に備える。** 本件（ch12・月 19-1）はノリウツール携行の最有力経路が
   計画空の非ボス注文への自動携行（`_auto_carry_safe_card`）であり、`is_boss_castle`
   条件で対象外になる可能性が高い（VERIFY-5381・#5362 と同じ構造で ch12 はより強い）。
   #5347 マージ後に非ボス城・防衛側（`mixed_item_box` 経由の egg_battle_menu、
   `screen.py:1528-1537`）・未認識召喚獣での同型発火が runtime で確認されたら、
   `own_attack_menu` のボス城・攻撃側限定（fail-closed 意図）の緩和を実害確認後に
   検討する。scroll 枯渇型（`SUMMON_CARD_SCROLL_LIMIT = 3`）が出たら別途 box 構造の
   解析が必要。

## 検証で使った根拠（抜粋・main = `4692ff34b` = v1467 の行番号）

- `src/docich/hanjuku/flows/battle_menus.py`: `_carried_card_left` L1091-1104、
  `_white_egg_available` L2137-2146（`kind == 'battle_menu'` 要求）、
  `_screen_egg_empty` L2149-2160、`egg_battle_step` L2630-（`general_reading` L2708、
  `enemy_rows`/`enemy_monsters` L2718-2721、`melee_only` L2725-2726・L2840-2857、
  `empty_egg` L2746、`if action == 'attack':` L2892、interrupted card_flow 再開
  L2895-2910、`cards_left` L2911-2920、owner 意図コメント L2921-2924、
  scroll gate L2925-2935、resume L2936-2939、open gate L2940-2961、defender preempt
  L2991-2997、emit L3065-3085）、`SUMMON_CARD_SCROLL_LIMIT` L3141、
  `MELEE_ONLY_SUMMONS` L61
- `src/docich/hanjuku/judgment/battle.py`: `_battle_labels` L90-93、
  `_confirmed_carried_kit` L989-1001、`_own_egg_empty` L2815-2833
- `src/docich/hanjuku/decision_log.py`: `_record` L11-19（screen_kind 非付記）
- `src/docich/hanjuku/os_layer/screen.py`: 卵行 OCR 欠落でも `egg_battle_menu` 化する
  分類 L1549-1557、防衛召喚 `mixed_item_box` の `egg_battle_menu` 化 L1528-1537
- `src/docich/hanjuku/month_audit.py`: `_fingerprint` L3032-3034、`_track_card_miss`
  L3163-3222（incident_id L3186-3188、battle dict L3193-3196、fingerprint L3217-3218、
  auto_file L3219）、`_render_card_miss` L3617-3637（投影欠落の生成元 L3630-3633、
  固定テンプレート L3635）
- `src/docich/hanjuku/orchestrator.py`: `egg_battle_menu` 振り分け L2270-2271（章条件なし）
- `src/docich/hanjuku_chart.py`: `CHAPTER_12_ORDERS` L1042-1077（ノリウツール携行注文なし、
  唯一の携行注文 12-B1 L1073-1076 = 騎馬A→`ボス`、cards マグネガキン/ハリケーン/ファバード、
  after `all_captured`）、`_TACTICS` L1210-1213（ch12 なし）、`BOSS_CASTLES` L455-459、
  `BOSSES` L1197-1201（ch12 = クーモン）、`boss_castle` L1226-1232、
  `is_boss_castle` L1365-1366
- `src/docich/hanjuku_reference.py`: ノリウツール L57-59（id 11、monster 8、boss 8、
  egg_drop 0、price 18）、`STRONG_CARDS` L160
- `src/docich/hanjuku/strategy/kit.py`: `_auto_carry_safe_card` L117-146（egg_drop 0 の
  在庫札を計画空の出撃へ自動携行）、`_with_strong_card` L191、rare/strong/dropper/
  override の合成 L62-114
- `src/docich/hanjuku/flows/deploy.py`: `strong_card_kit` 決定 L505-552（L544、
  `boss_card_reserved` による非ボス出撃への添付あり）
- `src/docich/hanjuku/flows/battle.py`: battle dict が `ally`/`enemy`/`castle`/`side` を
  保持（L131、L749、L2127）、他 emit の `observed_metric` に castle/side を載せる
  慣例（L1444、L1714、L1795）
- `src/docich/hanjuku/version.py`: `BOT_VERSION` L7（v1467 実測）
- PR #5347: `own_attack_menu`/`enemy_summon_menu` は v1467 のソースに存在しない
  （grep 実測 → 未マージ）。実装内容は系列 VERIFY が head `a9fd49d46` で実測済みの
  記載を参照（本環境は git 参照を拒否）
- 関連 VERIFY（本 worktree）: VERIFY-5381/5373/5362/5331/5328/5322/5259、いずれも (a)。
  VERIFY-5393（同一ラン・同一観測版 v1460・ch12/18-8）
- テスト: `tests/test_hanjuku_chart_bot.py:4550-4609`（card_miss 起票/不起票/カーソル
  未読ガード）、`tests/test_hanjuku_month_audit.py:2028-2053`
  （`test_card_miss_decision_seals_an_auto_file_incident`）、
  `tests/test_hanjuku_boss_egg_row_log.py`（main 6 件 — #5347 追加の 4 件は不在、
  未マージと整合）

## 手順上の注記

- **テスト実行は未実施（静的のみ）**: 本環境が `python3`/`pytest`/`gh`/`git` 系の起動を
  拒否した。静的照合のみで、関連テストの存在と内容は読取で確認済み（上記）。
- `gh` 不可のため issue #5398 本文・PR #5347 の最新状態は依頼記載と系列 VERIFY の記録に
  依る。`v1460→v1467` の diff・`git merge-tree` による CONFLICTING 再確認も未実施。
- fingerprint `9aacaeceb5657175` の再計算は未実施（同上）。計算式と章・札依存の形式は
  整合。
- 本ファイルは `verify/5398-card-miss`（現 checkout、base `4692ff34b` = v1467）の
  worktree ルートに**未コミット**で置いてある。系列 VERIFY と同様、書き込み系 git 操作を
  環境が拒否するため別環境で `git add VERIFY-5398.md && git commit` が必要。
  push と PR 作成は依頼どおり実施しない（検品ゲート側で実施）。issue #5398 への
  コメント投稿も `gh` 不可のため未実施（Muse が代理投稿）。
- read-only 検証。VERIFY-5398.md の作成以外のファイル編集・本番 VM 操作・push・
  PR 作成なし。
