# VERIFY-5381 — [半熟英雄] 敵召喚獣戦/ボス戦で切り札未使用: クースカン (ch4 戦闘)

- 対象: docich issue #5381 / 監査 `card_miss` (confidence: high)
- fingerprint `3922dfede2b0311f` / occurrence `31826554919d39546f61`
- ラン: `g1342-1fe0734c` / v1456（`hanjuku-chart-v1456-password-phrase-ocr`、依頼記載。
  現行 main の `version.py:7` の `BOT_VERSION` と一致）
- ch4 / 月 6-7、未使用の携行札: クースカン、理由「敵召喚獣戦で携行切り札を開けず、
  こうげきへ進んだ」
- 戦闘: 不明 vs 不明 / 城: 不明 / 画面: None（後述の投影欠落による。実際の戦闘が不明だった
  ことの証拠ではない）
- 検証範囲: read-only（本ファイルの作成以外の編集なし。実装・push・PR・issue コメント・
  本番 SSH なし）
- 検証ベース: 本 worktree の HEAD `f30582172`（`feat(hanjuku): ランダム表示のパスワード語を
  画像認識で語順どおり入力 (v1456) (#5377)`、`BOT_VERSION
  'hanjuku-chart-v1456-password-phrase-ocr'` を `version.py:7` で実測。依頼の
  「現行 main は v1456（PR #5377 マージ済み）」と一致。行番号はこのコミット基準）
- **観測版 = 検証ベース。** 本件は v1456 そのもの（現行 main と同一コード）で観測された。
  #5373（v1455 で観測）に続き、系列で 2 件目の「現行 main 自身での発火」。
- `gh` 実行不可（本環境は `gh`・`python3`/`pytest`・`sha256sum` 等の起動を拒否）。
  issue 本文は依頼記載の要点のみ使用。PR #5347 の内容はローカル git の head
  `a9fd49d46` の blob 参照で実測。CONFLICTING 状態は依頼記載に依る。正本 handoff:
  `DOCICH_HANDOFF_PATH` 未設定のため未読（AGENTS.md §1 に従い通常検証）。
- 参照済み VERIFY: VERIFY-5352（`6ce26cb56:VERIFY-5352.md`、(a)）、VERIFY-5373
  （`8651fc533:VERIFY-5373.md`、(a)）、VERIFY-5362（`d9eb1eacd:VERIFY-5362.md`、(a)）。
  三者が本機構を網羅済みのため、本稿はその確定内容を `f30582172` 上で再読・確認したうえで、
  本 occurrence 固有の差分（章・ラン・観測版・ch4 の注文構成）だけを詳述する。

## 結論: (a) 実欠陥 — #5259/#5322/#5328/#5331/#5352/#5362/#5373 と同一機構クラス。系列初の ch4 発火

「召喚獣戦の切り札行を画面に出す」有界スクロールの発火条件に穴があり、携行が確定した
切り札を一度も一覧化せず `こうげき` を確定しうる。系列 8 件目の発火で、**系列ではじめての
ch4 での発火**。かつ #5362（ch3/バグストーム/v1452/月 6-1）と**同一ラン
`g1342-1fe0734c` での再発**（ch3 6-1 → ch4 6-7 と章を跨いで同一ラン内で継続発火）。
v1423 → v1456 とバージョンを跨ぎ、観測版は修正を含まない現行 main 自身。owner の意図
（`battle_menus.py:2673-2676`「召喚獣戦では切り札行はたまごをつかうの下にあり、先に
切り札を開く」）に照らし、携行札を残して攻撃確定した判断は設計意図と矛盾する。
検出器は仕様どおり実事象を報告している。

## 根拠となるコード箇所（ファイル・行、main = `f30582172` = 観測版 v1456）

`f30582172` の diff（`git show --stat` で実測）は `password.py`/`screen.py`/
`collect_diagnostics.py`/`version.py` のみで、`battle_menus.py`・`month_audit.py`・
`judgment/battle.py`・`decision_log.py`・`orchestrator.py` は v1455（`46d17f1a5`）から
無差分。よって VERIFY-5352/5362/5373 が `46d17f1a5` 基準で確定した行番号は本基準でも
そのまま有効であることを本 worktree で再読確認した。

### 発火遷移（焦点1: 同一機構クラスか別経路か）

発火関数は `egg_battle_step`（`src/docich/hanjuku/flows/battle_menus.py:2402`）。
`orchestrator.py:2265-2266` が `kind == 'egg_battle_menu'`（または `mem['egg_battle']`
保持中の `text`）で振り分ける。**章条件はない**（章非依存の汎用パス）。

- `battle_menus.py:2663-2672` — `cards_left` は `planned_cards` 優先、空なら
  `_confirmed_carried_kit`（`judgment/battle.py:989-1001`: 出撃確定 `departure_binding`
  − 確定使用）で埋める。issue の「未使用の携行札: クースカン」は確定携行札（または
  計画携行札）。監査側も `_carried_card_left`（`battle_menus.py:895-908`）で同じ帳簿を
  見る。クースカンは `STRONG_CARDS`（`hanjuku_reference.py:160`）に列挙される既知の
  切り札（id 13、monster 56/boss 20、egg_drop 0、`hanjuku_reference.py:63-65`）。
- `battle_menus.py:2677-2687` — 揭示ゲート。発火には
  `cards_left and 'きりふだ' not in (screen.text or '') and
  ('たまごをつかう' in (screen.text or '') or empty_egg) and not battle.get('card_flow')`
  がすべて必要。穴（既 VERIFY と同一）:
  - 灰色・使用済み・未所持の卵行は白 OCR（`screen.text`）に載らない
    （`os_layer/screen.py:1549-1557` の実測コメント「たまごをつかう drops out of OCR」、
    同 `:1549-1557` は卵行欠落でも `こうげき`/`もうこうげき` で `egg_battle_menu` に
    分類されることを示す — **卵行欠落画面はこのハンドラへ来るのにゲート前提を満たさない**）。
  - `empty_egg`（`battle_menus.py:2500`）は `_screen_egg_empty`（`:1936-1947`）経由。
    `_white_egg_available`（`:1924-1933`）は `screen.kind == 'battle_menu'` を要求するため
    `egg_battle_menu` では常に偽。後段の `_own_egg_empty`
    （`judgment/battle.py:2815-2833`）は fail-closed で、観測済み整数 0 または
    `egg_zero_owners` 登録がない限り偽 — **「卵行が OCR に無く、かつ卵0の確定記録も
    無い」画面ではゲートが立たず、scroll 0 回のまま攻撃へ進む**（#5259 型）。
  - その他の穴（同ゲート内の亜型）: `card_flow` 残存（announce/selection_planned/
    read_kit 済み）による両ゲート skip、scroll 3 回枯渇（`SUMMON_CARD_SCROLL_LIMIT`、
    `battle_menus.py:2893`）。
- `battle_menus.py:2817-2837` — `move == 'here' and label == 'こうげき'` でのみ
  `battle_boss_card_missed` を起票（`cards_left` 非空・`cards_used` 空・
  `boss_card_missed_noted` 未設定、#5217/#5241 のガード）。issue の理由文はこの
  `reason`（L2836）と一致。`battle_boss_card_missed` の emit サイトは全コード中
  この 1 箇所のみ（grep 実測）で、**「同じ理由文の発火」は必ずこのブロック経由**。
- 起票 → issue 投影: `_track_card_miss`（`month_audit.py:3163-3222`）は battle dict に
  `row.get('ally'/'enemy'/'castle'/'screen_kind')` を読む（`:3193-3196`）が、`_record`
  呼出（`battle_menus.py:2830`）は `**_battle_labels`（chart_step・strategy_variant・
  deviation_reason のみ、`judgment/battle.py:90-93`）＋ `card` + `reason` +
  `observed_metric` しか渡さず、`decision_log._record`（`decision_log.py:11-19`）も
  screen_kind を付記しない。**`戦闘: 不明 / 城: 不明 / 画面: None` はこの投影欠落の
  結果**（`month_audit.py:3630-3633`）。`observed_metric` の `screen`/`text`/`scrolls`/
  `cursor`（`battle_menus.py:2831-2835`）は記録されるが issue へ投影されず捨てられる。
  監査テスト `tests/test_hanjuku_month_audit.py:2033-2037` が decision へ
  `ally`/`enemy`/`castle`/`screen_kind` を手付けしているのは、この投影欠落がテスト
  データ上で補われているだけであり、実 emit はこれらを出力しない。
- 「卵なし・奥の手不可のときに」は `_render_card_miss` の固定テンプレート文
  （`month_audit.py:3635`）で実測条件ではない。

**切り分け**: 章（ch4）と fingerprint（`3922dfede2b0311f` ≠ #5352/#5373 の
`497c96b5221276f2`）は異なるが、fingerprint は `_fingerprint(chapter, 'card_miss',
[card, castle])`（`month_audit.py:3217-3218`、`_fingerprint` 本体 `:3032-3034`）で、
emit が castle を渡さないため第3要素は実質空文字 — **章が違えば fingerprint が別になるのは
当然であり、別 fingerprint は別機構の証拠にならない**。発火コードに章依存の分岐は無く、
emit サイトは 1 箇所のみ、理由文も同一。**同じ機構クラスの発火と切り分けられる**。
亜型（scroll-0 型か scroll 枯渇型か `card_flow` 残存 skip か）は issue 単体では
切り分け不能だが、いずれも同じゲートブロック内の実欠陥（後述 (d)）。

### 焦点2: 観測版 v1456 = 現行 main。#5377 は card_miss 経路に無影響

- `git show f30582172 --stat` で実測: 変更は `ops/vm_actions/collect_diagnostics.py`
  （+1 行）、`flows/password.py`、`os_layer/screen.py`、`version.py` の 4 ファイルのみ。
- `screen.py` の差分は `if screen.kind == 'password_entry':` ブロック内部（main
  `screen.py:1290-1308`）に `password_list_grey` を追跡する処理を追加したもので、
  password 専用 rect `(160,15,252,145)` と専用 predicate（`min>=170` の白、
  `95<=min<=165 かつ spread<=12` の灰）の中に閉じている。**戦闘メニューの灰色検出
  （`screen.py:1239-1241` と `:1255-1257`、rect `(176,176,256,216)`・別 predicate）は
  無変更**。`light`/`dark` マスク、`read_lines`、`_menu_rows`、`_menu_cursor`、
  `_egg_rows`、`classify_text` はすべて無差分。
- `password.py` の差分は章クリア画面の語順入力のみ。`collect_diagnostics.py` は
  診断収集の ops ツールで戦闘判断に無関係。
- **結論: #5377 は card_miss の発火経路（screen 解析 → ゲート → emit → 監査投影）に
  影響しない。** v1455 → v1456 で発火経路は無差分であり、本件は「現行 main 自身での
  発火」として #5373 と同じ立場にある。

### 焦点3: PR #5347（OPEN、CONFLICTING、v1452 marker、head `a9fd49d46`）との関係

- **未マージ。** main のゲート（`battle_menus.py:2677-2679`）に `own_attack_menu` は
  存在せず（grep 実測、`('たまごをつかう' in (screen.text or '') or empty_egg)` のまま）。
- `a9fd49d46` の blob で `own_attack_menu` を実測（ブランチ側 `battle_menus.py` 該当部）:
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
- **ch4 特有の注意（本件固有の観測）**: ch4 のチャート注文でクースカンを携行するのは
  `4-A1`（HERO → `ミヒラギアン`、`hanjuku_chart.py:676-679`）のみで、**目標は非ボス城**。
  ボス注文 `4-B1..B4`（`四季王城(南西/南東/北東/北西)`、`hanjuku_chart.py:700-715`）は
  クースカンを含まない。`is_boss_castle`（`hanjuku_chart.py:1365-1366`）は `'ボス'` と
  ch4 の 4 サイト（`hanjuku_map_sources.py:97`）のみを認めるため、**4-A1 出撃中の発火なら
  #5347 マージ後も対象外**。さらにクースカンは `egg_drop == 0`
  （`hanjuku_reference.py:63-65`）で `_auto_carry_safe_card`
  （`strategy/kit.py:117-146`、計画空の出撃への自動携行、v1448/#5336）の対象でもあり、
  通常城出撃への添付もありうる。月 6-7 は `all_captured` 前提のボス注文より通常城
  攻略局面である可能性が高い — **本件は #5347 がマージされても救えない可能性が
  具体的に残る**（#5362 の ch3/月 6-1 と同じ構造）。
- その他の残存経路（既 VERIFY と同じ）: 未認識召喚獣・防衛側での同型欠落、
  scroll 枯渇型、`card_flow` 残存による両ゲート skip。

## 証拠が薄い点 — (d) 要素として残す部分

結論は (a) だが、issue 単体では以下を一意に決められない（VERIFY-5328/5352/5362/5373 と同型）。

- **scroll-0 型か scroll 枯渇型か**: `observed_metric.scrolls` は記録されるが issue へ
  投影されない。卵行 OCR 欠落＋回数未確定でゲート不成立（#5259 型）か、ゲート成立後に
  3 回尽きたかは区別不能。
- **戦闘の内訳**: 敵・味方・城が `不明`（`month_audit.py:3630-3631` の投影欠落）。
  ボス城/ボスサイトか・攻撃側/防衛側かが不明なため、#5347 のフォールバックが本
  occurrence を救えたかも判断不能。ch4 ではクースカン携行の最有力経路が非ボス城の
  4-A1 であり、非ボス城発火の可能性が具体的に残る（§焦点3）。
- **「敵召喚獣戦」の分類自体**: reason 文（`battle_menus.py:2836`）は固定で、この攻撃
  ブロックは敵が将軍でも発火しうる。「召喚獣戦だった」ことの独立した裏付けは記録にない。
- **「卵なし・奥の手不可」**: 静的テンプレート文（`month_audit.py:3635`）であり、実際の
  卵残数・おくのて可否は記録されていない。
- **クースカンの携行経路**: `4-A1` の計画携行・`_auto_carry_safe_card` の自動携行・
  strong-card kit 等の策略上書きのいずれかは、出撃帳簿（`departure_binding`/
  `cards_autocarried` レコード）が無いと確定できない。
- **fingerprint の再計算は未実施**（本環境が `python3`/`sha256sum` を拒否）。計算式は
  `sha256("4|card_miss|クースカン+")[:16]`（`month_audit.py:3032-3034,3217-3218`、
  emit が castle を渡さないため第3要素は空文字）で、形式（16 hex・章・札依存）とは整合。

## 判定詳細

- **(a) 実欠陥**: 確定携行札を開かず `こうげき` を確定したのは記録上の事実
  （`battle_boss_card_missed` は `cards_left` 非空・`cards_used` 空のときのみ起票、
  `battle_menus.py:2827-2828`）。原因機構は現行 main に残るどころか、本件は
  **現行 main と同一の v1456 で観測された**ため、欠陥が main に生存することは
  差分推論ではなく直接の事実。
- **(b) ではない**: マージ済み修正はどれもこのゲートを塞いでいない。#5218 系は中断
  `card_flow` の `summon_card_resume` 再開（`battle_menus.py:2647-2662`）、#5241 は
  `cards_used` 重複起票ガード、v1448〜v1456 のマージ済み PR は自動携行・パスワード・
  築城・防衛待機・おくのて保護・非携行型表示で本経路無変更。#5347 は CONFLICTING・
  未マージのため現行 main のカバーはゼロ。
- **(c) ではない**: 起票条件そのものは実測の確定携行（`planned_cards` または
  `_confirmed_carried_kit`）・未使用・攻撃行確定に基づく。投影欠落は証拠の品質問題で
  あり、起票の正しさを否定しない。同一機構の連続再発（8 件・2 ラン・4 章）は誤検知ではなく
  未修正欠陥の反復観測と解するのが自然。
- **(d) 要素**: 上記の部分（scroll-0 vs 枯渇、敵・城・side の内訳、携行経路）は証拠不足で
  判定不能。起票自体の実欠陥性（携行札を残して攻撃確定した事実）には影響しない。

## §6 修正案（実装はしない）

1. **PR #5347 を現行 main へ rebase/衝突解消してからマージする。** 依頼記載どおり現在
   CONFLICTING で、そのままでは取り込めない。衝突源として `battle_menus.py` が最有力
   （main 側は v1453/#5361 のおくのてハンクで変更済み、ブランチは v1451 系譜）。
   解消後に `own_attack_menu`（navigation-only・fail-closed）を入れれば卵行 OCR 欠落型の
   主たる部分機構を塞げる。
2. **card_miss の証拠投影を完成させる。** #5347 の `screen`/`screen_text` 投影に加え、
   `battle_boss_card_missed` の emit（`battle_menus.py:2830-2837`）へ `ally`/`enemy`/
   `castle`/`side`（`_battle_labels` 外の top-level 引数）と `observed_metric.scrolls`/
   `cursor` の監査投影を追加する。本件のように「非ボス城の可能性が高い発火」でも
   「#5347 で救えたか」を判定できない現状では、検証が毎回 (d) 要素を残す。
3. **非ボス城での発火に備える。** 本件（ch4・月 6-7）はクースカン携行の最有力注文が
   非ボス城の 4-A1 であり、`is_boss_castle` 条件で対象外になる可能性が高い
   （#5362 と同じ構造）。#5347 マージ後に非ボス城・未認識召喚獣・防衛側での同型発火が
   runtime で確認されたら、`own_attack_menu` のボス城・認識召喚獣・攻撃側限定
   （fail-closed 意図）の緩和を実害確認後に検討する。scroll 枯渇型
   （`SUMMON_CARD_SCROLL_LIMIT = 3` で行が現れない画面）が出たら別途 box 構造の解析が必要。

## 検証で使った根拠（抜粋・main = `f30582172` の行番号）

- `src/docich/hanjuku/flows/battle_menus.py`: `_carried_card_left` L895-908、
  `_white_egg_available` L1924-1933（`kind == 'battle_menu'` 要求）、
  `_screen_egg_empty` L1936-1947、`egg_battle_step` L2402-（`general_reading` L2462、
  `enemy_rows`/`enemy_monsters` L2472-2475、`empty_egg` L2500、
  `if action == 'attack':` L2644、interrupted card_flow 再開 L2647-2662、
  `cards_left` L2663-2672、owner 意図コメント L2673-2676、scroll gate L2677-2687、
  resume L2688-2691、open gate L2692-2713、defender preempt L2743-2749、
  emit L2817-2837）、`SUMMON_CARD_SCROLL_LIMIT` L2893
- `src/docich/hanjuku/judgment/battle.py`: `_battle_labels` L90-93、
  `_confirmed_carried_kit` L989-1001、`_own_egg_empty` L2815-2833
- `src/docich/hanjuku/decision_log.py`: `_record` L11-19（screen_kind 非付記）
- `src/docich/hanjuku/os_layer/screen.py`: 戦闘箱の灰色 predicate L1239-1241・L1255-1257、
  卵行 OCR 欠落でも `egg_battle_menu` 化する分類 L1549-1557、
  #5377 の変更範囲 L1290-1308（password_entry ブロック内のみ）
- `src/docich/hanjuku/month_audit.py`: `_fingerprint` L3032-3034、`_track_card_miss`
  L3163-3222（incident_id L3186-3188、battle dict L3193-3196、fingerprint L3217-3218、
  auto_file L3219）、`_render_card_miss` L3617-3637（投影欠落の生成元 L3630-3633、
  固定テンプレート L3635）
- `src/docich/hanjuku/orchestrator.py`: `egg_battle_menu` 振り分け L2265-2266（章条件なし）
- `src/docich/hanjuku_chart.py`: `CHAPTER_4_ORDERS` L671-715（クースカン携行は 4-A1 のみ、
  target `ミヒラギアン` L678・非ボス城；ボス注文 4-B1..B4 L700-715 は四季王城サイトで
  クースカン非含有）、`BOSS_CASTLES` L455-459（ch4 = `'ボス'`）、`BOSSES` L1197-1201
  （ch4 = 四季王）、`boss_sites`/`is_boss_castle` L1357-1366
- `src/docich/hanjuku_map_sources.py`: `SOURCE_BOSS_SITES[4]` L97（四季王城 4 サイト）
- `src/docich/hanjuku_reference.py`: クースカン L63-65（id 13、monster 56、boss 20、
  egg_drop 0）、`STRONG_CARDS` L160
- `src/docich/hanjuku/strategy/kit.py`: `_auto_carry_safe_card` L117-146（egg_drop 0 の
  在庫札を自動携行、v1448/#5336）
- `src/docich/hanjuku/version.py`: `BOT_VERSION` L7（v1456 実測、観測版と一致）
- 観測版との差分: `git show f30582172 --stat`（card_miss 経路のファイルは無変更、
  観測版 = 検証ベースのため差分照合は不要）
- PR #5347 ブランチ head `a9fd49d46`（marker `hanjuku-chart-v1452-battle-card-row-evidence`）:
  blob 参照で `enemy_summon_menu`/`own_attack_menu` 実装を実測。`own_attack_menu` は
  main に存在しない（grep 実測）
- 関連 VERIFY: `6ce26cb56:VERIFY-5352.md`（(a)）、`8651fc533:VERIFY-5373.md`（(a)）、
  `d9eb1eacd:VERIFY-5362.md`（同一ラン g1342-1fe0734c の ch3 版、(a)）。
  #5259/#5322/#5328/#5331 の VERIFY は 3 ファイル内の参照経由で確認

## 手順上の注記

- テスト実行は未実施: 本環境が `python3`/`pytest` の起動を拒否した。関連テスト
  （実行できていれば該当）: `tests/test_hanjuku_chart_bot.py:4548-4607` 付近
  （card_miss 起票/不起票）、`tests/test_hanjuku_month_audit.py:2028-2053`
  （`test_card_miss_decision_seals_an_auto_file_incident`）、
  `tests/test_hanjuku_boss_egg_row_log.py`（main 6 件＋#5347 で 4 件追加）。
  VERIFY-5328 では別環境で同 2 ファイル 142 passed が記録されている。
- `gh` 不可のため issue #5381 本文・PR 状態は依頼記載の要点とローカル git 参照のみ使用。
  推測で補っていない。#5347 の CONFLICTING は依頼記載に依る（`git merge-tree` 未実行）。
- fingerprint `3922dfede2b0311f` の再計算は未実施（`python3`/`sha256sum` を環境が拒否）。
  計算式と章・札依存の形式は上記のとおり整合。
- 本ファイルは `verify/5381-card-miss`（現 checkout、base `f30582172` = v1456）の
  worktree ルートに**未コミット**で置いてある。依頼の「ブランチでコミット」はこの環境では
  実行できなかった — `git add`/`git commit` 等の書き込み系 git 操作を環境が拒否した
  （VERIFY-5352/5362/5373 と同じ制約パターン）。別環境で
  `git add VERIFY-5381.md && git commit` が必要。root の新規追加のため origin/main 系へ
  そのまま rebase/merge しても衝突しない。push と PR 作成は依頼どおり実施しない
  （検品ゲート側で実施）。
- read-only 検証。VERIFY-5381.md の作成以外のファイル編集・本番 VM 操作・push・
  PR 作成なし。
