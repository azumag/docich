# VERIFY-5373 — [半熟英雄] 敵召喚獣戦/ボス戦で切り札未使用: クースカン (ch11 戦闘)

- 対象: docich issue #5373 / 監査 `card_miss` (confidence: high)
- fingerprint `497c96b5221276f2` / occurrence `1a17a2860e7508949926`
- ラン: `g1190-ch1v9` / v1455（`hanjuku-chart-v1455-password-in-game-retry`、依頼記載。
  現行 main の `version.py:7` の `BOT_VERSION` と一致）
- ch11 / 月 18-3、未使用の携行札: クースカン、理由「敵召喚獣戦で携行切り札を開けず、
  こうげきへ進んだ」
- 検証範囲: read-only（本ファイルの作成以外の編集なし。実装・push・PR・issue コメント・
  本番 SSH なし）
- 検証ベース: 本 worktree の HEAD `46d17f1a5`（`fix(hanjuku): パスワード失敗時はゲーム内
  リセット入力を試して語順をやり直す (v1455) (#5367)`、`BOT_VERSION
  'hanjuku-chart-v1455-password-in-game-retry'` を `version.py:7` で実測。依頼の
  「現行 main は v1455（PR #5367 マージ済み）」と一致。行番号はこのコミット基準）
- **観測版 = 検証ベース。** 本件は v1455 そのもの（現行 main と同一コード）で観測された。
  従来の VERIFY が行った「観測版→main の差分照合」は不要で、読んだゲートコードが
  発火時のコードそのもの。v1450→v1455 で card_miss 発火経路は無変更であることも
  VERIFY-5352/5362 が `git diff` で実測済み（v1453/#5361 の差分はおくのて系のみ）。
- `gh` 実行不可（本環境は `gh`・`python3`/`pytest`・`sha256sum`・`git merge-tree` 等の
  起動を拒否）。issue 本文は依頼記載の要点のみ使用。PR #5347 の内容はローカル git の
  head `a9fd49d46` との `git diff` で実測。CONFLICTING 状態は依頼記載に依る
  （mergeability を gh/merge-tree で確認できない）。正本 handoff:
  `DOCICH_HANDOFF_PATH` 未設定のため未読（AGENTS.md §1 に従い通常検証）。
- 参照済み VERIFY: VERIFY-5352（`6ce26cb56:VERIFY-5352.md`、PR #5371、(a)）、
  VERIFY-5362（`d9eb1eacd:VERIFY-5362.md`、(a)）。両者が origin/main `46d17f1a5` 基準で
  本機構を網羅済みのため、本稿はその確定内容を同一コミット上で再読・確認したうえで
  本 occurrence 固有の差分（月・観測版・PR 状態）だけを詳述する。

## 結論: (a) 実欠陥 — #5352 と同一 fingerprint の再発。同一機構クラス。観測版が現行 main (v1455) そのもの

「召喚獣戦の切り札行を画面に出す」有界スクロールの発火条件に穴があり、携行が確定した
切り札を一度も一覧化せず `こうげき` を確定しうる。本件は #5352（ch11/クースカン/月 18-2/
v1450、occurrence `36c5e20483e57a827567`）と**同一 fingerprint・同一ランの翌月（18-3）
再発**であり、さらに観測版が **v1455 = 現行 main 自身** に更新されている。これは同機構の
系列発火（#5259 v1423 → #5322 v1445 → #5331 v1447 → #5328 v1447 → #5352 v1450 →
#5362 v1452）に続く 7 件目で、**初めて現行 main のコードそのものでの発火を観測した**点が
新しい。修正 PR #5347 は未マージ（依頼記載では CONFLICTING）のため、発火時のゲートは
VERIFY-5352 が切り分けた穴を持つ版のまま。owner の意図（`battle_menus.py:2673-2676`
「召喚獣戦では切り札行はたまごをつかうの下にあり、先に切り札を開く」）に照らし、携行札を
残して攻撃確定した判断は設計意図と矛盾する。検出器は仕様どおり実事象を報告している。

## 根拠となるコード箇所（ファイル・行、main = `46d17f1a5` = 観測版 v1455）

### 発火遷移（焦点1: 有界スクロールの発火条件の穴 → #5352 と同一機構クラス）

発火関数は `egg_battle_step`（`src/docich/hanjuku/flows/battle_menus.py:2402`）。
`orchestrator.py:2265-2266` が `kind == 'egg_battle_menu'`（または `mem['egg_battle']`
保持中の `text`）で無条件に振り分ける。

- `battle_menus.py:2663-2672` — `cards_left` は `planned_cards` 優先、空なら
  `_confirmed_carried_kit`（`judgment/battle.py:989-1001`: 出撃確定 `departure_binding`
  − 確定使用）で埋める。issue の「未使用の携行札: クースカン」は計画値ではなく確定携行札
  （または計画携行札）。監査側も `_carried_card_left`（`battle_menus.py:895-908`）で
  同じ帳簿を見る。クースカンは既知の切り札（`STRONG_CARDS` 系、VERIFY-5352 確認済み）。
- `battle_menus.py:2673-2676` — Owner 2026-11-22 の意図コメント: 召喚獣戦では
  `きりふだ` 行は `たまごをつかう` の下にあり、画面をスクロールしないと OCR に出ない。
- `battle_menus.py:2677-2687` — 揭示ゲート（有界スクロールの発火条件）。発火には
  `cards_left and 'きりふだ' not in (screen.text or '') and
  ('たまごをつかう' in (screen.text or '') or empty_egg) and not battle.get('card_flow')`
  がすべて必要。穴（VERIFY-5352/5362 と同一）:
  - 灰色・使用済み・未所持の卵行は白 OCR（`screen.text`）に載らない。
    `os_layer/screen.py:1547-1553` が「たまごをつかう drops out of OCR」でも
    `こうげき`/`もうこうげき` だけで `egg_battle_menu` に分類されることを示す —
    **卵行欠落画面はきちんとこのハンドラへ来る**のに、ゲートの前提を満たさない。
  - `empty_egg`（`battle_menus.py:2500`）は `_screen_egg_empty`（`:1936-1947`）経由。
    `_white_egg_available`（`:1924-1933`）は `screen.kind == 'battle_menu'` を要求
    するため `egg_battle_menu` では常に偽。後段の `_own_egg_empty`
    （`judgment/battle.py:2815-2833`）は fail-closed で、観測済み整数 0 または
    `egg_zero_owners` 登録がない限り `empty_egg` は立たない。
  - したがって **卵行が OCR 欠落かつ卵回数が未確定の画面では、ゲート前提が不成立となり
    `summon_card_scroll` を 1 回も消費せず攻撃経路へ直行する**（scroll-0 型）。
- `battle_menus.py:2680-2687` + `:2893` — ゲートが立っても
  `SUMMON_CARD_SCROLL_LIMIT = 3` で打ち止め。3 回の down でも `きりふだ` が OCR に
  現れなければ同じ攻撃経路へ落ちる（scroll 枯渇型）。
- `battle_menus.py:2692-2713` — `きりふだ` が text にあれば `_battle_menu_to` で行へ
  向かい、`here` で `battle_summon_card_open` → `card_flow` 開始。目標行が `_options`
  に無い（灰色行のみ等）とき `move is None` で攻撃経路へ。`card_flow` 残存
  （announce/selection_planned/read_kit 済み）でも両ゲートは skip される。
- `battle_menus.py:2817-2837` — `move == 'here' and label == 'こうげき'`（攻撃行を実際に
  選択した瞬間、L2817）に `missed = _carried_card_left(...)`（L2821）を取り、
  `cards_left` 非空・`cards_used` 空・`boss_card_missed_noted` 未設定のときだけ
  `battle_boss_card_missed` を起票（L2827-2837、#5217/#5241 のガード）。issue の
  理由文はこの `reason`（L2836「敵召喚獣戦で携行切り札を開けず、こうげきへ進んだ」）と
  一致し、「1 枚も使わずに攻撃確定」の裏付けになる。
- 起票 → issue 投影: `_track_card_miss`（`month_audit.py:3163-3222`）は battle dict に
  `row.get('ally'/'enemy'/'castle'/'screen_kind')` を読む（`:3193-3196`）が、`_record`
  呼出（`battle_menus.py:2830`）は `**_battle_labels`（chart_step・strategy_variant・
  deviation_reason のみ、`judgment/battle.py:90-93`）＋ `card` + `reason` +
  `observed_metric` しか渡さず、`decision_log._record`（`decision_log.py:11-19`）も
  screen_kind を付記しない。**`戦闘: 不明 vs 不明 / 城: 不明 / 画面: None` はこの投影
  欠落の結果**（`month_audit.py:3630-3633`）であり、実際の戦闘が不明だったことの証拠
  ではない。`observed_metric` の `screen`/`text`/`scrolls`/`cursor`
  （`battle_menus.py:2831-2835`）は記録されるが issue へ投影されず捨てられる。
- 「卵なし・奥の手不可のときに」は `_render_card_miss` の固定テンプレート文
  （`month_audit.py:3635`）で実測条件ではない。

### 焦点2: #5352 との異同 — 同一機構の再発（別経路の形跡なし）

- fingerprint は `_fingerprint(chapter, 'card_miss', [card, str(row.get('castle') or '')])`
  （`month_audit.py:3217-3218`、`_fingerprint` 本体 `:3032-3034`）。emit が castle を
  渡さないため第3要素は実質空文字で、**fingerprint 同一 = 章（ch11）と札（クースカン）
  が同一**。本件は #5352 と同じ `497c96b5221276f2` で、城まで同じかは fingerprint からは
  分からない（城が投影されないため）。
- 相違点は occurrence・月・観測版のみ: #5352 = occurrence `36c5e20483e57a827567` /
  月 18-2 / v1450、本件 = occurrence `1a17a2860e7508949926` / 月 18-3 / v1455。
  同一 run `g1190-ch1v9` の**翌月での再発**であり、incident_id は
  `runtime|chapter|at|card` の sha256（`month_audit.py:3186-3188`）で時刻を含むため
  別 occurrence として正しく起票されている。
- 同じ機構の再発か別経路か: **同じ機構の再発が最も自然な読み**。発火コード（scroll ゲート
  `:2677-2687` → 起票 `:2817-2837`）は #5352 観測版 v1450 → 本件 v1455 で無差分
  （VERIFY-5352/5362 実測済み）で、署名（章・札・理由文）も同一。別経路
  （scroll 枯渇型・`card_flow` 残存 skip 等）の可能性は同じ発火点内の亜型であり、
  issue 単体では切り分け不能（後述 (d)）だが、いずれも同じゲートブロックの実欠陥。
- 世代（generation）が異なる点について: 依頼記載どおり別 generation であっても、
  発火機構は世代非依存の汎用パス（#5362 が別 run ch3 で同型を観測して裏付け済み）。

### 焦点3: PR #5347 との関係 — 観測ランは修正未適用の main v1455。カバーは条件付き

- **観測ランは修正前か修正後か**: #5347 は未マージ（依頼記載: CONFLICTING）のため
  main 上に存在せず、観測版 v1455 は修正を含まない。ローカル実測: main のゲート
  （`battle_menus.py:2677-2679`）には `own_attack_menu` が存在せず
  `('たまごをつかう' in (screen.text or '') or empty_egg)` のまま。#5347 の head
  `a9fd49d46` は v1451 系譜（`2cbe3542f` = #5343/v1451 のマージコミット）上にあり、
  main が v1452〜v1455（うち v1453/#5361 が `battle_menus.py` を変更）へ進んだ後も
  rebase されていない — CONFLICTING の報告と整合。
- **#5347 の変更内容**（`git diff 46d17f1a5...a9fd49d46` で実測、VERIFY-5352/5362 と同じ
  内容を再確認）:
  - `battle_menus.py` ゲートへ第3トリガ `own_attack_menu` を追加:
    `enemy_summon_menu = chart.is_boss_castle(battle.get('castle')) and
    len(enemy_rows) == len(enemy_monsters) == 1`（`hanjuku_chart.py:1365-1366`、
    `battle_menus.py:2471-2474`）かつ `general_reading is not None`（`:2462`）かつ
    `side == 'attack'` かつ `'こうげき' in screen.text` の全成立時に限り、卵行 OCR
    欠落でも `SUMMON_CARD_SCROLL_LIMIT` 内の navigation-only down を許す。札選択は
    引き続き `きりふだ` 実表示を要求する fail-closed 設計。
  - `month_audit.py` 側は `_track_card_miss` が `screen`/`screen_text` を bounded
    projection し `_render_card_miss` が「画面 / 文言」へ出す部分修正。
    `scrolls`/`cursor`/`ally`/`enemy`/`castle`/`side` は依然未投影。
- **本 occurrence が #5347 でカバーされるか: 判定不能（(d) 要素）。** ch11 のボス城
  （`boss_castle(11)`、ボス = エッグママ `hanjuku_chart.py:1200`、注文 11-B1
  `:1035-1038`）で・`side == 'attack'` で・将軍パネルが読めて・認識済み敵召喚獣が
  ちょうど 1 体で・`こうげき` が text に見えていた場合に限り、発火前に scroll → 開放へ
  進み本起票は防げた。月 18-3 は終盤でボス攻略局面の可能性はあるが、城・敵・側が投影
  欠落で残らないため確定できない。非ボス城・防衛側・未認識召喚獣・scroll 枯渇型・
  `card_flow` 残存 skip での発火なら #5347 対象外。
- **結論**: 修正未適用コードでの発火であり、#5347 は「マージされれば主たる亜型を塞ぐ
  部分修正」。本 occurrence がその亜型だったかは証拠不足。かつ現状 CONFLICTING で
  そのままマージできない。

## 証拠が薄い点 — (d) 要素として残す部分

結論は (a) だが、issue 単体では以下を一意に決められない（VERIFY-5328/5352/5362 と同型）。

- **scroll-0 型か scroll 枯渇型か**: `observed_metric.scrolls` は記録されるが issue へ
  投影されない。卵行 OCR 欠落でゲート不成立（#5259 型）か、ゲート成立後に 3 回尽きたかは
  区別不能。同一 fingerprint が翌月に再発しているため最も parsimonious な読みは同一亜型
  の再現（scroll-0 型）だが、issue 単体の証拠では確定できない。
- **戦闘の内訳**: 敵・味方・城が `不明`（`month_audit.py:3630-3631` の投影欠落）。
  ボス城か・攻撃側/防衛側かが不明なため、#5347 のフォールバックが本 occurrence を
  救えたかも判断不能。
- **「敵召喚獣戦」の分類自体**: reason 文（`battle_menus.py:2836`）は固定で、この攻撃
  ブロックは敵が将軍でも発火しうる。「召喚獣戦だった」ことの独立した裏付けは記録にない。
- **「卵なし・奥の手不可」**: 静的テンプレート文（`month_audit.py:3635`）であり、実際の
  卵残数・おくのて可否は記録されていない。
- **fingerprint の再計算は未実施**（本環境が `python3`/`sha256sum` を拒否）。計算式は
  `sha256("11|card_miss|クースカン+")[:16]`（`month_audit.py:3032-3034,3217-3218`、
  emit が castle を渡さないため第3要素は空文字）で、形式（16 hex・章・札依存）とは整合。

## 判定詳細

- **(a) 実欠陥**: 確定携行札を開かず `こうげき` を確定したのは記録上の事実
  （`battle_boss_card_missed` は `cards_left` 非空・`cards_used` 空のときのみ起票、
  `battle_menus.py:2827-2828`）。原因機構は現行 main に残るどころか、本件は
  **現行 main と同一の v1455 で観測された**ため、欠陥が main に生存することは
  差分推論ではなく直接の事実。
- **(b) ではない**: マージ済み修正はどれもこのゲートを塞いでいない。#5218 系は中断
  `card_flow` の `summon_card_resume` 再開（`battle_menus.py:2647-2662`）、#5241 は
  `cards_used` 重複起票ガード、v1448〜v1455 のマージ済み PR は自動携行・パスワード・
  築城・防衛待機・おくのて保護で本経路無変更。#5347 は CONFLICTING・未マージのため
  現行 main のカバーはゼロ。
- **(c) ではない**: 起票条件そのものは実測の確定携行（`planned_cards` または
  `_confirmed_carried_kit`）・未使用・攻撃行確定に基づく。投影欠落は証拠の品質問題で
  あり、起票の正しさを否定しない。同一 fingerprint の連続再発は誤検知ではなく
  未修正欠陥の反復観測と解するのが自然。
- **(d) 要素**: 上記の部分（scroll-0 vs 枯渇、敵・城・side の内訳）は証拠不足で判定不能。
  起票自体の実欠陥性には影響しない。

## §6 修正案（実装はしない）

1. **PR #5347 を現行 main へ rebase/衝突解消してからマージする。** 依頼記載どおり現在
   CONFLICTING で、そのままでは取り込めない。衝突源として `battle_menus.py` が最有力
   （main 側は v1453/#5361 のおくのてハンクで変更済み、ブランチは v1451 系譜）。
   解消後に `own_attack_menu`（navigation-only・fail-closed）を入れれば卵行 OCR 欠落型の
   主たる部分機構を塞げる。
2. **card_miss の証拠投影を完成させる。** #5347 の `screen`/`screen_text` 投影に加え、
   `battle_boss_card_missed` の emit（`battle_menus.py:2830-2837`）へ `ally`/`enemy`/
   `castle`/`side`（`_battle_labels` 外の top-level 引数）と `observed_metric.scrolls`/
   `cursor` の監査投影を追加する。現状では本件のように同一 fingerprint が再発しても
   「#5347 で救えたか」を issue 本文から判定できず、検証が毎回 (d) 要素を残す。
3. 非ボス城・未認識召喚獣・防衛側・scroll 枯渇型の発火が runtime で確認されたら、
   `own_attack_menu` の限定条件緩和または box 構造解析を別途検討する
   （VERIFY-5352/5362 と同じ留保）。

## 検証で使った根拠（抜粋・main = `46d17f1a5` の行番号）

- `src/docich/hanjuku/flows/battle_menus.py`: `_carried_card_left` L895-908、
  `_white_egg_available` L1924-1933（`kind == 'battle_menu'` 要求）、
  `_screen_egg_empty` L1936-1947、`egg_battle_step` L2402-（`general_reading` L2462、
  `enemy_rows`/`enemy_monsters` L2471-2474、`empty_egg` L2500、
  `if action == 'attack':` L2644、interrupted card_flow 再開 L2647-2662、
  `cards_left` L2663-2672、owner 意図コメント L2673-2676、scroll gate L2677-2687、
  resume L2688-2691、open gate L2692-2713、emit L2817-2837）、
  `SUMMON_CARD_SCROLL_LIMIT` L2893
- `src/docich/hanjuku/judgment/battle.py`: `_battle_labels` L90-93、
  `_confirmed_carried_kit` L989-1001、`_own_egg_empty` L2815-2833
- `src/docich/hanjuku/decision_log.py`: `_record` L11-19（screen_kind 非付記）
- `src/docich/hanjuku/os_layer/screen.py`: grey predicate L1089-1096、卵行 OCR 欠落でも
  `egg_battle_menu` 化する分類 L1540-1553
- `src/docich/hanjuku/month_audit.py`: `_fingerprint` L3032-3034、`_track_card_miss`
  L3163-3222（incident_id L3186-3188、battle dict L3193-3196、fingerprint L3217-3218、
  auto_file L3219）、`_render_card_miss` L3617-3637（投影欠落の生成元 L3630-3633、
  固定テンプレート L3635）
- `src/docich/hanjuku/orchestrator.py`: `egg_battle_menu` 振り分け L2265-2266
- `src/docich/hanjuku_chart.py`: ch11 ボス注文 11-B1 L1035-1038、
  `BOSSES` L1197-1201（ch11 = エッグママ）、`boss_castle` L1226-1232、
  `is_boss_castle` L1365-1366
- `src/docich/hanjuku/version.py`: `BOT_VERSION` L7（v1455 実測、観測版と一致）
- PR #5347 ブランチ head `a9fd49d46`（marker `hanjuku-chart-v1452-battle-card-row-evidence`、
  `origin/fix/5312-5318-5331-evidence-v1451-main` 系）: `git diff 46d17f1a5...a9fd49d46`
  で `own_attack_menu` 実装と `_track_card_miss` の `screen`/`screen_text` 投影を実測
- 関連 VERIFY: `6ce26cb56:VERIFY-5352.md`（同一 fingerprint、(a)）、
  `d9eb1eacd:VERIFY-5362.md`（ch3 版、(a)）。#5259/#5322/#5328/#5331 の VERIFY は
  両ファイル内の参照経由で確認。

## 手順上の注記

- テスト実行は未実施: 本環境が `python3`/`pytest` の起動を拒否した。関連テスト
  （実行できていれば該当）: `tests/test_hanjuku_chart_bot.py:4550-4607` 付近
  （card_miss 起票/不起票）、`tests/test_hanjuku_month_audit.py:2028-2053`
  （`test_card_miss_decision_seals_an_auto_file_incident`）、
  `tests/test_hanjuku_boss_egg_row_log.py`（main 6 件＋#5347 で 4 件追加）。
- `gh` 不可のため issue #5373 本文・PR 状態は依頼記載の要点とローカル git 参照のみ使用。
  推測で補っていない。#5347 の CONFLICTING は依頼記載に依り、ローカルでは
  「ブランチが v1451 系譜で main が battle_menus.py 変更を含む v1452〜v1455 へ進行」
  までを実測した（`git merge-tree` も環境が拒否）。
- 行番号は全て `46d17f1a5`（= 観測版 v1455 と同一コード）基準。VERIFY-5352/5362 と
  同じコミットなので両者の引用行番号もそのまま有効であることを本 worktree で再読確認済み。
- 本ファイルは `verify/5373-card-miss`（現 checkout、base `46d17f1a5` = v1455）の
  worktree ルートに**未コミット**で置いてある。依頼の「ブランチでコミット」はこの環境では
  実行できなかった — `git add`/`git commit` 等の書き込み系 git 操作を環境が拒否した
  （VERIFY-5352/5362 と同じ制約パターン）。別環境で `git add VERIFY-5373.md && git commit`
  が必要。root の新規追加のため origin/main 系へそのまま rebase/merge しても衝突しない。
  push と PR 作成は依頼どおり実施しない（検品ゲート側で実施）。
- read-only 検証。VERIFY-5373.md の作成以外のファイル編集・本番 VM 操作・push・
  PR 作成なし。
