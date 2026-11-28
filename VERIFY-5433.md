# VERIFY-5433 — [半熟英雄] 敵召喚獣戦/ボス戦で切り札未使用: クースカン (ch12 戦闘)

- 対象: docich issue #5433 / 監査 `card_miss` (confidence: high)
- fingerprint `41fc11d3b0e8f35c` / occurrence `0cebef66243132fe10e0` / 時刻 14:28:43Z
- ラン: `g1190-ch1v9` / generation 1190 / v1468（`hanjuku-chart-v1468-battle-menu-bounded-episode`、
  依頼記載）
- ch12 / 月 19-12、未使用の携行札: クースカン、理由「敵召喚獣戦で携行切り札を開けず、
  こうげきへ進んだ」
- 戦闘: 不明 vs 不明 / 城: 不明 / 画面: None（後述の投影欠落による。実際の戦闘が不明だった
  ことの証拠ではない）
- 検証範囲: read-only（本ファイルの作成以外の編集なし。実装・push・PR・issue コメント・
  本番 SSH なし）
- 検証ベース: 本 worktree（detached HEAD = `origin/main` = `8c346ca01`、
  `BOT_VERSION 'hanjuku-chart-v1486-locked-boss-activation'` を `version.py:7` で実測。
  行番号はこのコミット基準）
- **観測版 v1468 → 検証ベース v1486 の差分を実測**: 観測版は `00c2b6503`（#5422、
  `git log -S 'v1468-battle-menu-bounded-episode'` で特定）。`git diff 00c2b6503..HEAD` で
  `battle_menus.py` / `judgment/battle.py` / `os_layer/screen.py` / `strategy/kit.py` /
  `flows/deploy.py` / `decision_log.py` / `hanjuku_reference.py` は**無変更**。変更は
  `month_audit.py`（#5460 の sortie_kit 監査・`_issue_title`/`render` 等。`_track_card_miss`・
  `_fingerprint`・`_render_card_miss`・`_incident_id` は差分に含まれないことを hunk 実測で
  確認）と `hanjuku_chart.py`（+36 行、`STAGING_CHAPTERS`/`boss_staging` = v1483/#5451 の
  ch12 ボス布陣機能。card_miss 経路と無関係）のみ。**すなわち本件の発火経路は観測版と
  検証ベースで完全に同一**（系列 VERIFY が残していた「観測版との差分未照合」の (d) 要素は
  本件では解消）。
- `gh` 実行不可（本環境は `gh`・`python3`/`sha256sum` 等の起動を拒否。`git` の読取系・
  `cat`/`grep`/`sed` は実行可）。issue 本文は依頼記載の要点のみ使用。PR #5347 の内容は
  系列 VERIFY（VERIFY-5331/5352/5362/5373/5381/5398）が head `a9fd49d46` で実測済みの
  記載を参照。正本 handoff: `DOCICH_HANDOFF_PATH` の有無を本環境では確認できないため
  未読（AGENTS.md §1 に従い通常検証）。
- 参照済み VERIFY: VERIFY-5398（ch12・ノリウツール・**同一ラン g1190**、(a)）、
  VERIFY-5352/5373（ch11・**クースカン**・同一ラン、(a)）、VERIFY-5381（ch4・クースカン・
  g1342、(a)）、VERIFY-5328/5331/5362/5259（各 (a)）。系列は全件 (a)・同一機構クラスで
  確定済み。本稿はその確定内容を `8c346ca01`（v1486）上で再読・確認したうえで、本
  occurrence 固有の差分（札がクースカン・月 19-12・観測版 v1468 = ch12 限定スクロール機構の
  導入版）だけを詳述する。

## 結論: (a) 実欠陥 — #5259 家族と同一機構クラス。#5398 と同一ラン・同一章の再発

「召喚獣戦の切り札行を画面に出す」有界スクロールの発火条件に穴があり、携行が確定した
切り札を一度も一覧化せず `こうげき` を確定しうる。依頼の数えでは系列 9 件目（同型の
#5322 を含めると 10 件目）の発火で、**#5398（ch12・月 19-1・ノリウツール）と同一ラン
`g1190-ch1v9`・同一章の再発**（約 4.3 時間後の月 19-12）。札は #5352/#5373（ch11）・
#5381（ch4）と同じクースカン — 同じランが ch11→ch12 で、同じカードが別章で、それぞれ
継続発火している。観測版 v1468 と検証ベース v1486 のいずれにも当該ゲートの修正はない。
owner の意図（`battle_menus.py:3112-3115`「召喚獣戦の切り札行はたまごをつかうの下に
あるため、先に切り札を開く」）に照らし、携行札を残して攻撃確定した判断は設計意図と
矛盾する。検出器は仕様どおり実事象を報告している。

## 根拠となるコード箇所（ファイル・行、main = `8c346ca01` = v1486 = 観測版 v1468 と同じコード）

### 発火遷移（焦点1: 同一機構クラスか別経路か）

発火関数は `egg_battle_step`（`src/docich/hanjuku/flows/battle_menus.py:2802`）。
`orchestrator.py:2285-2286` が `kind == 'egg_battle_menu'`（または `mem['egg_battle']`
保持中の `text`）で振り分ける。**章条件はない**（章非依存の汎用パス）。

- `battle_menus.py:3104-3111` — `cards_left` は `planned_cards` 優先、空なら
  `_confirmed_carried_kit`（`judgment/battle.py:989-1001`: 出撃確定 `departure_binding`
  − 確定使用）で埋める。issue の「未使用の携行札: クースカン」は確定携行札（または
  計画携行札）。監査側も `_carried_card_left`（`battle_menus.py:1251-1264`）で同じ帳簿を
  見る。クースカンは `STRONG_CARDS`（`hanjuku_reference.py:160`）に列挙される既知の
  切り札（id 13、monster 56/boss 20、egg_drop 0、`hanjuku_reference.py:63-65`）。
- `battle_menus.py:3116-3118` — 揭示ゲート。発火には
  `cards_left and 'きりふだ' not in (screen.text or '') and
  ('たまごをつかう' in (screen.text or '') or empty_egg) and not battle.get('card_flow')`
  がすべて必要。穴（系列 VERIFY と同一）:
  - 灰色・使用済み・未所持の卵行は白 OCR（`screen.text`）に載らない
    （`os_layer/screen.py:1549-1557` の実測コメント「たまごをつかう drops out of OCR」。
    同 `:1551-1557` は卵行欠落でも `こうげき`/`もうこうげき` で `egg_battle_menu` に
    分類されることを示す — **卵行欠落画面はこのハンドラへ来るのにゲート前提を満たさない**）。
  - `empty_egg`（`battle_menus.py:2937`）は `_screen_egg_empty`（`:2309-2320`）経由。
    `_white_egg_available`（`:2297-2306`）は `screen.kind == 'battle_menu'` を要求するため
    `egg_battle_menu` では常に偽。後段の `_own_egg_empty`
    （`judgment/battle.py:2815-2833`）は fail-closed で、観測済み整数 0 または
    `egg_zero_owners` 登録がない限り偽 — **「卵行が OCR に無く、かつ卵0の確定記録も
    無い」画面ではゲートが立たず、scroll 0 回のまま攻撃へ進む**（#5259 型）。
  - その他の穴（同ゲート内の亜型）: `card_flow` 残存（announce/selection_planned/
    read_kit 済み）による両ゲート skip、scroll 3 回枯渇（`SUMMON_CARD_SCROLL_LIMIT`、
    `battle_menus.py:3332`）。
- `battle_menus.py:3256-3276` — `move == 'here' and label == 'こうげき'` でのみ
  `battle_boss_card_missed` を起票（`missed`・`cards_left` 非空・`cards_used` 空・
  `boss_card_missed_noted` 未設定、#5217/#5241 のガード）。issue の理由文はこの
  `reason`（L3275）と一致。emit サイトは全コード中この 1 箇所のみ（grep 実測）で、
  **「同じ理由文の発火」は必ずこのブロック経由**。
- 起票 → issue 投影: `_track_card_miss`（`month_audit.py:3236-3295`）は battle dict に
  `row.get('ally'/'enemy'/'castle'/'screen_kind')` を読む（`:3266-3269`）が、`_record`
  呼出（`battle_menus.py:3269`）は `**_battle_labels`（chart_step・strategy_variant・
  deviation_reason のみ、`judgment/battle.py:90-93`）＋ `card` + `reason` +
  `observed_metric` しか渡さず、`decision_log._record`（`decision_log.py:11-19`）も
  screen_kind を付記しない。**`戦闘: 不明 / 城: 不明 / 画面: None` はこの投影欠落の
  結果**（`month_audit.py:3713-3716`）。`observed_metric` の `screen`/`text`/`scrolls`/
  `cursor`（`battle_menus.py:3270-3274`）は記録されるが issue へ投影されず捨てられる。
- 「卵なし・奥の手不可のときに」は `_render_card_miss` の固定テンプレート文
  （`month_audit.py:3718`）で実測条件ではない。
- `melee_only`（ゾンビ/ウゴカザル、`battle_menus.py:2916-2917,2966-2967`）は
  `action = 'attack'` へ進めるだけで、**カード揭示ゲートを迂回しない**（L3116 の
  ゲートは `cards_left` を見るため、0 ダメージ召喚獣でも携行札があれば開く設計のまま）。
  card_miss 経路への影響なし。

**切り分け**: fingerprint は `_fingerprint(chapter, 'card_miss', [card, castle])`
（`month_audit.py:3290-3291`、`_fingerprint` 本体 `:3105-3107`）で、emit が castle を
渡さないため第3要素は実質空文字。`41fc11d3b0e8f35c` が系列の fingerprint（ch11/
クースカン `497c96b5221276f2`、ch4/クースカン `3922dfede2b0311f`、ch12/ノリウツール
`9aacaeceb5657175`）と異なるのは**章（ch12）と札（クースカン）の組合せが違うからで、
別機構の証拠にならない**。発火コードに章依存の分岐は無く、emit サイトは 1 箇所のみ、
理由文も同一。**同じ機構クラスの発火と切り分けられる**。亜型（scroll-0 型か scroll
枯渇型か `card_flow` 残存 skip か）は issue 単体では切り分け不能（後述 (d)）。

### 焦点2: ch12 の注文構成 — クースカンを計画携行する注文は存在しない

- `CHAPTER_12_ORDERS`（`hanjuku_chart.py:1042-1077`）: 11 件の注文のうち `cards` を
  持つのは boss 注文 `12-B1`（騎馬A、`ディランダ` → `'ボス'`、`after: all_captured`、
  cards `マグネガキン/ハリケーン/ファバード`、L1073-1076）のみ。**クースカンを計画
  携行する注文は ch12 に 1 件もない**（`CHAPTER_12_TACTICS` 自体も `_TACTICS` に無い、
  `hanjuku_chart.py:1210-1213`）。VERIFY-5398 のノリウツールと同じ結論。
- したがって本件のクースカンはチャート計画ではなく、非チャートの携行経路で載った。
  可能性のある経路（いずれも issue からは切り分け不能）:
  - `_auto_carry_safe_card`（`strategy/kit.py:117-146`、v1448/#5336 系・owner 2026-11-24
    コメント `kit.py:109-110`）: 計画が空の出撃に egg_drop 0 の在庫札を 1 枚だけ自動
    携行。クースカンは egg_drop 0（`hanjuku_reference.py:63-65`）で対象。
    **発火条件「計画が空」は ch12 の全 非ボス注文（12-C1/K1/K2/A1/C2/K3/K4/A2/K5/A3、
    いずれも `cards: ()`）で成立**する一方、12-B1 は計画非空のため対象外。
  - `strong_card_kit`（`flows/deploy.py:518-546`、L544）: 残り携行枠がある出撃へ実在庫の
    強い切り札を追加（クースカンは `STRONG_CARDS` の先頭）。`boss_card_reserved` の
    存在が示すとおり、**非ボス出撃へも付きうる**。
  - `rare_card_kit` / `dropper_card_kit` / `card_override`（`kit.py` 合成、`deploy.py:1443`）。
- **VERIFY-5398 と同じ構造的含意**: ch12 では切り札の携行が全て非チャート経路であり、
  最有力経路（自動携行）は計画空の出撃 = 非ボス注文でのみ発火する。12-B1（boss、
  `is_boss_castle` 成立）へクースカンが載るには strong/rare/dropper/override の
  上書きが必要で、より限定されたパス。
- 月 19-12 の局面: 同一ランは月 18-8 に非ボス城（ワフェルダ、12-C1 目標）を戦い
  （VERIFY-5393）、月 19-1 に ch12 初の card_miss（#5398）を出した。19-12 はその約 4.3
  時間後で、`all_captured` 達成後の 12-B1 局面になっている可能性もあるが、城が投影
  欠落のため確定できない。

### 焦点3: 観測版 v1468 の ch12 限定スクロール機構との関係

観測版 v1468（`00c2b6503`、#5422）は ch12 限定の
`_human_scroll_owns`/`_begin_human_scroll`/`_human_scroll_step`（有界・列限定の
「人間コマンド一覧」スクロール所有機構）を導入した版であり、v1463（`ca1e84372`）由来の
`_stale_row`/`battle_menu_scroll_attack`（`battle_menus.py:2829-2846`）も含む。
本件はこの機構の導入版そのもので発火しているため、系列 VERIFY に無い論点として
切り分ける。

- これらのブロック（`battle_menus.py:2826-2854`）は `action == 'attack'` ブロック
  （L3083-）より**前**にあり、`_human_scroll_step` が移動を返す間は本関数が早期 return
  して card ゲートにも攻撃確定にも到達しない。`_scroll is None` なら従来経路へ落ちる。
  つまり発火タイミングを遅らせうるが、**emit 条件・揭示ゲートの条件自体は変更しない**。
- `_human_scroll_owns` は `chapter != 12` で即 `False`（v1468 実測、現在版でも同じ
  ガード）。他章では無効で、ch12 だけの追加経路。
- `_stale_row` の early-A（L2839-2846、ch12 限定）: `stale` な `policy.battle` で右列
  コマンドのカーソル行が `こうげき` と完全一致すると、`cards_left` を見ずに
  `battle_menu_scroll_attack` を記録して `[pad('a')]` を返す。**この経路は card_miss を
  起票しない**（emit は L3256-3276 にしか無い）ため、仮にこの経路で攻撃確定されても
  監査には残らない — すなわち ch12 には card_miss が**検出できない**同型の silent-miss
  経路が別途存在する。本 issue は `battle_boss_card_missed` の理由文を持つため L3256-
  3276 経由で確定しており、この stale 経路が直接の原因ではないが、「ch12 で切り札を
  開かず攻撃」が起きうる面積は監査の見える部分より広い点に注意（§修正の方向 4）。

### 焦点4: PR #5347（v1452、is_boss_castle 条件付きフォールバック）との関係

- **未マージ。** v1486 のゲート（`battle_menus.py:3116-3118`）に `own_attack_menu` は
  存在せず（grep 実測、`('たまごをつかう' in (screen.text or '') or empty_egg)` のまま）。
  ソース内の `own_attack_menu`/`enemy_summon_menu` は VERIFY 文書内の記述のみ。
  系列 VERIFY（v1451-v1467 時点）では OPEN・CONFLICTING と記録されており、v1486 時点
  でも main に取り込まれていない。
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
- **ch12 特有の注意（#5398 と同じ構造）**: `is_boss_castle`（`hanjuku_chart.py:1401-1402`）
  は `'ボス'`（`BOSS_CASTLES`、`:455-459`、ch12 ボス = クーモン `BOSSES:1200`）のみを
  認める。ch12 で `is_boss_castle` が立つのは 12-B1 出撃（`all_captured` 後）だけ。
  焦点2のとおり**クースカンの最有力携行経路は非ボス注文への自動携行であり、非ボス城
  出撃中の発火なら #5347 マージ後も対象外**。さらに防衛側（`side == 'defense'`）の
  召喚獣戦でも `own_attack_menu` は立たない — 城防衛の召喚メニューは `mixed_item_box`
  経由で `egg_battle_menu` に分類され（`screen.py:1528-1537`、g1190 2026-11-18 の
  実測）、同じゲート穴を共有する。**本件は #5347 がマージされても救えない可能性が
  具体的に残る**。
- その他の残存経路（既 VERIFY と同じ）: 未認識召喚獣・防衛側での同型欠落、
  scroll 枯渇型、`card_flow` 残存による両ゲート skip。

## 証拠が薄い点 — (d) 要素として残す部分

結論は (a) だが、issue 単体では以下を一意に決められない（系列 VERIFY と同型）。

- **scroll-0 型か scroll 枯渇型か**: `observed_metric.scrolls` は記録されるが issue へ
  投影されない。卵行 OCR 欠落＋回数未確定でゲート不成立（#5259 型）か、ゲート成立後に
  3 回尽きたかは区別不能。
- **戦闘の内訳**: 敵・味方・城が `不明`（`month_audit.py:3713-3714` の投影欠落）。
  ボス城 12-B1 か非ボス城か・攻撃側/防衛側かが不明なため、#5347 のフォールバックが本
  occurrence を救えたかも判断不能。ch12 ではクースカン携行の最有力経路が非ボス注文への
  自動携行であり、非ボス城発火の可能性が具体的に残る（§焦点2・4）。月 19-12 は
  `all_captured` 後（12-B1）の可能性もあるが裏付けがない。
- **「敵召喚獣戦」の分類自体**: reason 文（`battle_menus.py:3275`）は固定で、この攻撃
  ブロックは敵が将軍でも発火しうる。「召喚獣戦だった」ことの独立した裏付けは記録にない。
- **「卵なし・奥の手不可」**: 静的テンプレート文（`month_audit.py:3718`）であり、実際の
  卵残数・おくのて可否は記録されていない。
- **クースカンの携行経路**: 自動携行・`strong_card_kit`・`rare_card_kit`・
  `dropper_card_kit`・`card_override` のいずれかは、出撃帳簿（`departure_binding`/
  `cards_autocarried`/`strong_card_kit` レコード）が無いと確定できない。
- **ch12 限定機構の寄与**: `_human_scroll` 有界エピソード（L2850-2854）が発火前の
  観測で動いていたか、`_stale_row` の early-A（L2839-2846）が別ターンで確定していたかは
  記録から読めない。いずれも emit 条件を変えないため (a) の結論には影響しない。
- **fingerprint の再計算は未実施**（本環境が `python3`/`sha256sum` を拒否）。計算式は
  `sha256("12|card_miss|クースカン+")[:16]`（`month_audit.py:3105-3107,3290-3291`、
  emit が castle を渡さないため第3要素は空文字）。形式（16 hex・章・札依存）は系列の
  実測値（ch11/クースカン `497c96b5221276f2` が #5352/#5373 で一致、ch4/クースカン
  `3922dfede2b0311f`、ch12/ノリウツール `9aacaeceb5657175`）と整合し、(章, 札) が違えば
  値が変わる決定的写像として矛盾はない。incident_id `0cebef66243132fe10e0` も
  `sha256("runtime_id|chapter|int(at)|card")[:20]`（`:3259-3261`）の 20 hex 形式と整合。

## 判定詳細

- **(a) 実欠陥**: 確定携行札を開かず `こうげき` を確定したのは記録上の事実
  （`battle_boss_card_missed` は `missed`・`cards_left` 非空・`cards_used` 空のときのみ
  起票、`battle_menus.py:3266-3268`）。原因機構は観測版 v1468 にも検証ベース v1486 にも
  生存する（両者の card_miss 経路は `git diff` 実測で無変更、ゲート
  `battle_menus.py:3116-3118` は系列確定時と同一構造）。
- **(b) ではない**: マージ済み修正はどれもこのゲートを塞いでいない。#5218 系は中断
  `card_flow` の `summon_card_resume` 再開（`battle_menus.py:3086-3101`）、#5241 は
  `cards_used` 重複起票ガード、v1461/v1463/v1467/v1468 のマージ済み変更（melee_only・
  5コマンド scroll-back・status panel・ch12 限定 bounded episode）はいずれも揭示ゲート・
  emit 条件を変更しない（v1468→v1486 diff 実測で battle_menus.py 無変更）。#5347 は
  未マージのため現行 main のカバーはゼロ。#5460（sortie kit 監査）は別 signal で無関係。
- **(c) ではない**: 起票条件そのものは実測の確定携行（`planned_cards` または
  `_confirmed_carried_kit`）・未使用・攻撃行確定に基づく。投影欠落は証拠の品質問題で
  あり、起票の正しさを否定しない。同一機構の連続再発（#5322 を含め 10 件・2 ラン・
  5 章）は誤検知ではなく未修正欠陥の反復観測と解するのが自然。
- **(d) 要素**: 上記の部分（scroll-0 vs 枯渇、敵・城・side の内訳、携行経路、
  #5347 の本 occurrence へのカバー可否、ch12 限定機構の寄与、fingerprint 再計算）は
  証拠不足または環境制約で判定不能。起票自体の実欠陥性（携行札を残して攻撃確定した
  事実）には影響しない。

## §修正の方向（実装はしない）

1. **PR #5347 を現行 main へ rebase/衝突解消してからマージする。** v1456 時点で
   CONFLICTING と記録され、main は v1486（`egg_battle_step` へ ch12 限定 bounded
   episode・`_stale_row` early-A 等の追加、L2813-2967）まで進んでいるため衝突解消が前提。
   解消後に `own_attack_menu`（navigation-only・fail-closed）を入れれば卵行 OCR 欠落型の
   主たる部分機構を塞げる。
2. **card_miss の証拠投影を完成させる。** #5347 の `screen`/`screen_text` 投影に加え、
   `battle_boss_card_missed` の emit（`battle_menus.py:3269-3275`）へ `ally`/`enemy`/
   `castle`/`side`（`_battle_labels` 外の top-level 引数 — runtime の battle dict は
   これらを保持する: `flows/battle.py` で他の emit も `observed_metric` に castle/side を
   載せる慣例あり）と `observed_metric.scrolls`/`cursor` の監査投影を追加する。本件の
   ように「非ボス城の可能性が高い発火」でも「#5347 で救えたか」を判定できない現状では、
   検証が毎回 (d) 要素を残す。
3. **非ボス城での発火に備える。** 本件（ch12・月 19-12）はクースカン携行の最有力経路が
   計画空の非ボス注文への自動携行（`_auto_carry_safe_card`）であり、`is_boss_castle`
   条件で対象外になる可能性が高い（VERIFY-5381・#5362・#5398 と同じ構造）。#5347
   マージ後に非ボス城・防衛側（`mixed_item_box` 経由の egg_battle_menu、
   `screen.py:1528-1537`）・未認識召喚獣での同型発火が runtime で確認されたら、
   `own_attack_menu` のボス城・攻撃側限定（fail-closed 意図）の緩和を実害確認後に
   検討する。scroll 枯渇型（`SUMMON_CARD_SCROLL_LIMIT = 3`）が出たら別途 box 構造の
   解析が必要。
4. **（ch12 固有・本件で新たに確認）`_stale_row` early-A 経路の silent-miss を塞ぐ。**
   `battle_menus.py:2839-2846` は ch12 で `cards_left` を見ずに `こうげき` を A 確定し、
   card_miss を起票しない。発火は監査に残らないため、同型欠陥が「見えない再発」と
   なる恐れがある。`battle_menu_scroll_attack` の確定前に `cards_left` 非空なら揭示
   ゲート側へ回すか、少なくとも同条件で `battle_boss_card_missed` と等価の記録を残す
   ことを検討する（fail-closed の原則は維持）。

## 検証で使った根拠（抜粋・main = `8c346ca01` = v1486 = card_miss 経路は v1468 と同一の行番号）

- `src/docich/hanjuku/flows/battle_menus.py`: `_carried_card_left` L1251-1264、
  `_white_egg_available` L2297-2306（`kind == 'battle_menu'` 要求）、
  `_screen_egg_empty` L2309-2320、`egg_battle_step` L2802-（ch12 限定
  `_human_scroll_owns`/`_begin_human_scroll`/`_human_scroll_step` L2826-2854、
  `_stale_row`/`battle_menu_scroll_attack` early-A L2829-2846、`general_reading` L2899、
  `enemy_rows`/`enemy_monsters` L2909-2912、`melee_only` L2916-2917・L2966-2967、
  `empty_egg` L2937、interrupted card_flow 再開 L3086-3101、`if action == 'attack':` L3083、
  `cards_left` L3104-3111、owner 意図コメント L3112-3115、scroll gate L3116-3126、
  resume L3127-3130、open gate L3131-3152、defender preempt L3182-3188、
  emit L3256-3276）、`SUMMON_CARD_SCROLL_LIMIT` L3332、`MELEE_ONLY_SUMMONS` L61
- `src/docich/hanjuku/judgment/battle.py`: `_battle_labels` L90-93、
  `_confirmed_carried_kit` L989-1001、`_own_egg_empty` L2815-2833
- `src/docich/hanjuku/decision_log.py`: `_record` L11-19（screen_kind 非付記）
- `src/docich/hanjuku/os_layer/screen.py`: 卵行 OCR 欠落でも `egg_battle_menu` 化する
  分類 L1549-1557、防衛召喚 `mixed_item_box` の `egg_battle_menu` 化 L1528-1537
- `src/docich/hanjuku/month_audit.py`: `_fingerprint` L3105-3107、`_track_card_miss`
  L3236-3295（incident_id L3259-3261、battle dict L3266-3269、fingerprint L3290-3291、
  auto_file L3292）、`_render_card_miss` L3700-3720（投影欠落の生成元 L3713-3716、
  固定テンプレート L3718）
- `src/docich/hanjuku/orchestrator.py`: `egg_battle_menu` 振り分け L2285-2286（章条件なし）
- `src/docich/hanjuku_chart.py`: `CHAPTER_12_ORDERS` L1042-1077（クースカン携行注文なし、
  唯一の携行注文 12-B1 L1073-1076 = 騎馬A→`ボス`、cards マグネガキン/ハリケーン/ファバード、
  after `all_captured`）、`_TACTICS` L1210-1213（ch12 なし）、`BOSS_CASTLES` L455-459、
  `BOSSES` L1197-1201（ch12 = クーモン）、`boss_castle` L1226-1232、
  `boss_sites` L1393-1398、`is_boss_castle` L1401-1402、
  `STAGING_CHAPTERS`/`boss_staging` L1306-1336（v1483、本経路と無関係）
- `src/docich/hanjuku_reference.py`: クースカン L63-65（id 13、monster 56、boss 20、
  egg_drop 0、price 24）、`STRONG_CARDS` L160（先頭がクースカン）
- `src/docich/hanjuku/strategy/kit.py`: `_auto_carry_safe_card` L117-146（egg_drop 0 の
  在庫札を計画空の出撃へ自動携行、owner コメント L109-110）
- `src/docich/hanjuku/flows/deploy.py`: `strong_card_kit` 決定 L518-546（L544、
  `boss_card_reserved` による非ボス出撃への添付あり）、kit キー一覧 L1443
- `src/docich/hanjuku/version.py`: `BOT_VERSION` L7（v1486 実測）
- PR #5347: `own_attack_menu`/`enemy_summon_menu` は v1486 のソースに存在しない
  （grep 実測 → 未マージ）。実装内容は系列 VERIFY が head `a9fd49d46` で実測済みの
  記載を参照（`git branch --contains` の確認は本環境が拒否）
- 関連 VERIFY（本 worktree）: VERIFY-5398/5381/5373/5362/5352/5331/5328/5322/5259、
  いずれも (a)。VERIFY-5393（同一ラン・v1460・ch12/18-8 非ボス城戦闘）
- テスト: `tests/test_hanjuku_chart_bot.py:4552-4608`（card_miss 起票/不起票/カーソル
  未読ガード）、`tests/test_hanjuku_month_audit.py:2072`
  （`test_card_miss_decision_seals_an_auto_file_incident`）、
  `tests/test_hanjuku_boss_egg_row_log.py`（存在確認、#5347 追加の 4 件は不在で
  未マージと整合 — 系列 VERIFY の記載と同一状態）

## 手順上の注記

- **テスト実行は未実施（静的のみ）**: 本環境が `python3`/`pytest`/`gh`/`sha256sum` 系の
  起動を拒否した。`git` の読取系（log/diff/status）は実行可能で、観測版 v1468→検証
  ベース v1486 の card_miss 経路差分が無いこと・v1468 導入コミットの特定は実測済み。
- `gh` 不可のため issue #5433 本文・PR #5347 の最新状態は依頼記載と系列 VERIFY の記録に
  依る。fingerprint `41fc11d3b0e8f35c` の再計算は未実施（同上）。計算式と章・札依存の
  形式は系列実測値と整合。
- 本ファイルは detached HEAD（`8c346ca01` = v1486）の worktree ルートに**未コミット**で
  置いてある。`git checkout -b verify/5433-card-miss && git add && git commit` を
  試行したが環境が拒否したため、系列 VERIFY と同様に別環境での
  `git add VERIFY-5433.md && git commit` が必要。push と
  PR 作成は依頼どおり実施しない（検品ゲート側で実施）。issue #5433 へのコメント投稿も
  `gh` 不可のため未実施。
- read-only 検証。VERIFY-5433.md の作成以外のファイル編集・本番 VM 操作・push・
  PR 作成なし。
