# VERIFY-5362 — [半熟英雄] 敵召喚獣戦/ボス戦で切り札未使用: バグストーム (ch3 戦闘)

- 対象: docich issue #5362 / 監査 `card_miss` (confidence: high)
- fingerprint `e47e544ed63ff621` / occurrence `2cec6c6cdbe0c6feb9da`
- ラン: `g1342-1fe0734c` / v1452（main 側の v1452 = `c435441f7`、
  `BOT_VERSION 'hanjuku-chart-v1452-password-hold'`。PR #5347 の marker
  `v1452-battle-card-row-evidence` とは別物）
- ch3 / 月 6-1、未使用の携行札: バグストーム、理由「敵召喚獣戦で携行切り札を開けず、こうげきへ進んだ」
- 戦闘: 不明 vs 不明 / 城: 不明 / 画面: None（後述の投影欠落による。実際の戦闘が不明だった
  ことの証拠ではない）
- 検証範囲: read-only（本ファイルの作成以外の編集なし。実装・push・PR・issue コメント・本番 SSH なし）
- 検証ベース: `origin/main` = `46d17f1a5`（`BOT_VERSION 'hanjuku-chart-v1455-password-in-game-retry'`、
  `version.py:7` で実測。行番号はこのコミット基準。依頼の「現行 main は v1455（PR #5367 マージ済み）」
  と一致）
- 観測版との差分: v1452 `c435441f7` → `origin/main` で card_miss 発火経路は無変更。
  `git diff c435441f7..46d17f1a5` で `battle_menus.py` の差分はおくのて系 3 ハンク
  （`_safer_rescue_available`/`_okunote_risk_protected`、`:1298`/`:1550`/`:1564` 付近）のみで、
  切り札スクロールゲートと起票ブロック（`:2677-2687`/`:2817-2837`）は同一。`month_audit.py` の
  差分は render 1 ハンク（`:3753` 付近、v1454/#5364 の `sortie_without_cards` 型表示）のみで、
  `_track_card_miss`/`_render_card_miss` は不変。
- `gh` 実行不可（本環境は `gh`・`python3`/`pytest` 等の起動を拒否）。issue 本文は依頼記載の
  要点のみ使用。PR #5347 の内容はローカル git の head `a9fd49d46` との `git diff` で実測。
  正本 handoff: `DOCICH_HANDOFF_PATH` 未設定のため未読（AGENTS.md §1 に従い通常検証）。
- 参照済み VERIFY: VERIFY-5352（`verify/5352-card-miss` blob、PR #5371）、
  VERIFY-5331（`verify/5331-card-miss` blob、PR #5360）、VERIFY-5328
  （`refs/remotes/origin/verify/5328-card-miss` blob、PR #5356）、VERIFY-5322
  （`refs/remotes/origin/verify/5322` blob）、VERIFY-5259（`refs/remotes/origin/verify/5259-card-miss`
  blob、PR #5287）。いずれも結論 (a)・同一機構クラス。

## 結論: (a) 実欠陥 — #5259 / #5322 / #5328 / #5331 / #5352 と同一機構クラス。現行 main (v1455) で再発しうる

「召喚獣戦の切り札行を画面に出す」有界スクロールの発火条件に穴があり、携行が確定した
切り札を一度も一覧化せず `こうげき` を確定しうる。#5259（ch9/ブラッキー/v1423）→
#5322（ch11/ころぼぐんだん/v1445）→ #5331（ch11/イッテツーン/v1447）→
#5328（ch11/ノリウツール/v1447）→ #5352（ch11/クースカン/v1450）に続く 6 件目の発火で、
**はじめて g1190 ラン以外（g1342・ch3）での発火**。v1423 → v1452 とバージョンを跨いで
継続発火しており、欠陥は現行 main に生存する。owner の意図（`battle_menus.py:2673-2676`
のコメント「召喚獣戦では切り札行はたまごをつかうの下にあり、先に切り札を開く」）に照らし、
携行札を残して攻撃確定した判断は設計意図と矛盾する。検出器は仕様どおり実事象を報告している。

## 根拠となるコード箇所（ファイル・行、origin/main = `46d17f1a5`）

### 発火遷移（焦点1: 実欠陥か設計どおりか）

発火関数は `egg_battle_step`（`src/docich/hanjuku/flows/battle_menus.py:2402`）。
`orchestrator.py:2265-2266` が `kind == 'egg_battle_menu'` で無条件に振り分ける
（章条件なし → 焦点3 参照）。

- `battle_menus.py:2663-2672` — `cards_left` は `planned_cards` 優先、空なら
  `_confirmed_carried_kit`（`judgment/battle.py:989-1001`: 出撃確定 `departure_binding`
  − 確定使用、`strategy/orders.py:885` `_order_for_step` ＋ `strategy/kit.py:51`
  `_deploy_cards`）で埋める。issue の「未使用の携行札: バグストーム」は計画値ではなく
  確定携行札（または計画携行札）。監査側も `_carried_card_left`
  （`battle_menus.py:895-908`）で同じ帳簿を見る。
- `battle_menus.py:2673-2676` — Owner 2026-11-22 の意図コメント: 召喚獣戦では
  `きりふだ` 行は `たまごをつかう` の下にあり、画面をスクロールしないと OCR に出ない。
  コード自身が「先に切り札を開く」を設計意図としている。
- `battle_menus.py:2677-2687` — 揭示ゲート。発火には
  `cards_left and 'きりふだ' not in (screen.text or '') and
  ('たまごをつかう' in (screen.text or '') or empty_egg) and not battle.get('card_flow')`
  がすべて必要。穴は次の通り:
  - 灰色の卵行は白 OCR（`screen.text`）に載らない。灰色行は灰色専用 predicate でしか
    読まれない例: `os_layer/screen.py:1089-1096`（おくのてのみ白の箱で灰色
    きりふだ/たいきゃくを別系統で読む）。使用済み・未所持の卵行が OCR から落ちる実測
    コメント: `os_layer/screen.py:1549-1552`（「the same spent-egg greying …
    たまごをつかう drops out of OCR」）。同 `:1545-1553` は卵行が OCR に無くても
    `こうげき`/`もうこうげき` だけで `egg_battle_menu` と分類されることを示す —
    つまり**卵行欠落画面はきちんと召喚戦としてこのハンドラへ来る**。
  - `empty_egg`（`battle_menus.py:2500`）は `_screen_egg_empty`（`:1936-1947`）経由。
    前半の `_white_egg_available`（`:1924-1933`）は `screen.kind == 'battle_menu'`
    を要求するため **`egg_battle_menu` では常に偽**。後半の `_own_egg_empty`
    （`judgment/battle.py:2815-2833`）は fail-closed で、観測済み整数 0 または
    `egg_zero_owners` 登録がない限り `empty_egg` は立たない。
  - したがって召喚戦で `('たまごをつかう' in text or empty_egg)` が成立する事実上の
    経路は「白い卵行が OCR に見える」か「帳簿で卵 0 が確定済み」のみ。
    **卵行が OCR 欠落（灰色/未所持/使用済み）かつ卵回数が未確定の画面では、ゲート
    前提が不成立となり `summon_card_scroll` を 1 回も消費せず攻撃経路へ直行する。**
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
  理由文はこの `reason`（L2836）と一致し、今回の起票は「1 枚も使わずに攻撃確定」の
  裏付けになる。
- 起票 → issue 投影: `_track_card_miss`（`month_audit.py:3163-3222`）は battle dict に
  `row.get('ally'/'enemy'/'castle'/'screen_kind')` を読む（`:3193-3196`）が、`_record`
  呼出（`battle_menus.py:2830`）は `**_battle_labels`（chart_step・strategy_variant・
  deviation_reason のみ、`judgment/battle.py:90-93`）＋ `card` + `reason` +
  `observed_metric` しか渡さず、`decision_log._record`（`decision_log.py:11-19`）も
  screen_kind を付記しない。**`戦闘: 不明 vs 不明 / 城: 不明 / 画面: None` はこの投影
  欠落の結果。** `observed_metric` の `screen`/`text`/`scrolls`/`cursor`
  （`battle_menus.py:2831-2835`）は記録されるが issue へ投影されず捨てられる
  （`month_audit.py:3193-3196`）。
- 「卵なし・奥の手不可のときに」は `_render_card_miss` の固定テンプレート文
  （`month_audit.py:3635`）で実測条件ではない。「画面: None」は `battle.get('screen')`
  が `None` のままレンダリングされたもの（`month_audit.py:3633`）。

### バグストームの携行経路（ch3 特有の論点）

- バグストームは `hanjuku_reference.py:92-94` で id 24・monster_damage 100・
  boss_damage 50・egg_drop 0（両軍半減の自爆札）。`BOSS_SACRIFICE_CARDS`
  （`reference.py:178-183`）の唯一の札だが、自爆経路で携行されるには
  `_strong_card_names`（`flows/deploy.py:480-500`）がボス注文かつモンスター型
  ボスかつ `sacrifice_general`（弱い卵無し将軍）を要求する。
- ch3 のボスは プリンス（`hanjuku_chart.py:1197-1201`）で `BOSS_GENERALS` 所属
  （`reference.py:1234`）。`deploy.py:493-495` により将軍型ボス注文は
  `STRONG_CARDS`（クースカン/ミックミー/マグネガキン/ノリウツール）へ早期 return
  するため、**ch3 では自爆札経路でバグストームは携行されない**。
- 残る経路で最も自然なのは `_auto_carry_safe_card`（`strategy/kit.py:117-146`、
  v1448/#5336 導入、owner 2026-11-24）: 計画が空でも egg_drop 0 の在庫札を 1 枚
  自動携行する。バグストームは egg_drop 0 で適格。**つまり本件は、計画札を持たない
  通常出撃にも自動携行が載るようになった経路で発火した可能性が高い**（確定は
  出撃帳簿 evidence が無いと出来ない → §4 (d)）。

### PR #5347（OPEN、Draft解除済み、v1452 marker、head `a9fd49d46`、CI green）との関係（焦点2）

- `git diff 46d17f1a5...a9fd49d46` で実測。`battle_menus.py` ゲートへ第3トリガ
  `own_attack_menu` を追加:
  `enemy_summon_menu = chart.is_boss_castle(battle.get('castle')) and
  len(enemy_rows) == len(enemy_monsters) == 1`（`hanjuku_chart.py:1365-1366`、
  `:2471-2474` の認識済み召喚獣行）かつ `general_reading is not None`（`:2462`）
  かつ `side == 'attack'` かつ `'こうげき' in screen.text` の全成立時に限り、
  卵行 OCR 欠落でも `SUMMON_CARD_SCROLL_LIMIT` 内の navigation-only down を許す。
  札選択は引き続き `きりふだ` 実表示を要求する fail-closed 設計。
- `month_audit.py` 側は `_track_card_miss` が `screen`（`metric.get('screen')`
  フォールバック付き）と `screen_text`（`metric.get('text')`）を bounded projection
  し、`_render_card_miss` が「画面 / 文言」へ出す — 次回発火から卵行 OCR 欠落型か
  scroll 枯渇型かを文言で切り分けられる部分修正。`scrolls`/`cursor` と
  `ally`/`enemy`/`castle`/`side` は依然未投影。
- **origin/main への取り込み状況: 未マージ。** main のゲート（`battle_menus.py:2677-2679`）
  には `own_attack_menu` が存在しない。
- **本 occurrence でフォールバック条件が成立していたかの判定: 判定不能（(d) 要素）。**
  ボス城か（ch3 のボス城ラベルは `'ボス'`、`BOSS_CASTLES` `hanjuku_chart.py:455-459`、
  `is_boss_castle` は全章共通）・`side == 'attack'` か・将軍パネルが読めたか・認識済み
  召喚獣がちょうど 1 体か・`こうげき` が text に見えていたかが、投影欠落のため
  issue 本文に残らない。成立していれば発火前に scroll → 開放へ進み本起票は防げた。
- **ch3/月 6-1 特有の注意**: 月 6-1 は章中盤であり、発火した戦闘がボス城
  （`'ボス'`、ボス プリンス）ではなく通常城の制圧戦だった可能性が十分ある。
  非ボス城での発火なら `enemy_summon_menu` の `is_boss_castle` 条件で外れ、
  **#5347 マージ後も本機構は残存する**。本件が #5347 でカバーされるかは、
  城の判明に完全に依存する — 現状では「部分的カバー、本 occurrence は不明」が上限。
- その他の残存経路（VERIFY-5352 と同じ）: 未認識召喚獣・防衛側での同型欠落、
  scroll 枯渇型、`card_flow` 残存による両ゲート skip。

### ch3（g1342 ラン）での発火の意味（焦点3）

- 発火経路に章依存の分岐はない。`orchestrator.py:2265-2266` は `screen.kind` だけで
  `egg_battle_step` へ振り分け、`cards_left` は `planned_cards`/
  `_confirmed_carried_kit`（`_order_for_step`/`_deploy_cards`/`bound_fields`、
  いずれも章非依存の汎用帳簿）から作られる。ゲートにも章条件はない（章依存入力は
  未マージ #5347 の `is_boss_castle` とログ用 `chart.boss_castle` のみ）。
- 従来の 5 件はすべて同一 run `g1190-ch1v9` の ch9/ch11（終盤章・長期戦）だったため、
  「終盤章固有の条件（モンスター型ボス戦・長い月・召喚多発）でのみ発火する」という
  読みも可能だった。**本件は別 run `g1342` の ch3・月 6-1 で発火しており、この機構が
  章・ラン・世代に非依存の汎用コードパスの穴であることが裏付けられた。**
- バージョン横断性も確認済み: 観測 v1452 → main v1455 で発火経路無差分
  （`git diff c435441f7..46d17f1a5`、おくのて系のみ）。
- 札の種類も新しい: 既発火は ブラッキー/ころぼぐんだん/ノリウツール/イッテツーン/
  クースカン で、本件はイベント限定の自爆札 バグストーム。ch3 での携行経路は
  `_auto_carry_safe_card`（v1448/#5336）が最有力で、**計画札の無い出撃まで card_miss
  の発火面が広がった**ことを示唆する（確定は帳簿 evidence が必要）。

## 証拠が薄い点 — (d) 要素として残す部分

結論は (a) だが、issue 単体では以下を一意に決められない（VERIFY-5328/5352 §4 と同型）。

- **scroll 0 型か scroll 枯渇型か**: `observed_metric.scrolls` は記録されるが issue へ
  投影されない。卵行 OCR 欠落＋回数未確定でゲート不成立（#5259 型）か、ゲート成立後に
  3 回尽きたかは区別不能。バグストームの最有力な携行経路が「計画空の出撃への自動携行」
  である点を踏まえると、卵無し・卵行欠落の将軍での scroll-0 型が最も parsimonious だが、
  issue 単体の証拠では確定できない。
- **戦闘の内訳**: 敵・味方・城が `不明`。ボス城か・認識済み召喚獣だったか・攻撃側/
  防衛側かが不明なため、#5347 のフォールバックが本 occurrence を救えたかも判断不能。
  月 6-1 の中盤戦なら非ボス城の可能性があり、その場合 #5347 対象外。
- **「敵召喚獣戦」の分類自体**: reason 文（`battle_menus.py:2836`）は固定で、この攻撃
  ブロックは敵が将軍でも発火しうる。記録上は「召喚獣戦だった」ことの独立した裏付けはない。
- **「卵なし・奥の手不可」**: 静的テンプレート文（`month_audit.py:3635`）であり、実際の
  卵残数・おくのて可否は記録されていない。
- **バグストームの携行経路**: `_auto_carry_safe_card` が最有力だが、出撃帳簿
  （`departure_binding`/`cards_autocarried` レコード）が無いと確定できない。

## 判定詳細

- **(a) 実欠陥**: 確定携行札を開かず `こうげき` を確定したのは記録上の事実
  （`battle_boss_card_missed` は `cards_left` 非空・`cards_used` 空のときのみ起票、
  `battle_menus.py:2827-2828`）。原因機構は現行 main に残る。
- **(b) ではない**: マージ済み修正はどれもこのゲートを塞いでいない。#5218 系は中断
  `card_flow` の `summon_card_resume` 再開（`battle_menus.py:2647-2662`）、#5241 は
  `cards_used` 重複起票ガード、v1448〜v1455 のマージ済み PR は自動携行・パスワード・
  築城・防衛待機・おくのて保護で本経路無変更。#5347 は Ready for review だが未マージの
  ため現行 main のカバーはゼロ。
- **(c) ではない**: 起票条件そのものは実測の確定携行（`planned_cards` または
  `_confirmed_carried_kit`）・未使用・攻撃行確定に基づく。投影欠落は証拠の品質問題で
  あり、起票の正しさを否定しない。
- **(d) 要素**: 上記の部分（scroll 0 vs 枯渇、敵・城・side の内訳、バグストームの携行
  経路）は証拠不足で判定不能。起票自体の実欠陥性（携行札を残して攻撃確定した事実）には
  影響しない。

## §6 修正案（実装はしない）

1. **後継 PR #5347（OPEN、Draft解除済み、v1452 marker、head `a9fd49d46`、CI green）を
   DSH レビュー＋mergeable 確認のうえマージする。** `own_attack_menu` は
   navigation-only・fail-closed で設計上安全（実表示されるまで選択しない）。卵行
   OCR 欠落型の主たる部分機構を塞ぐ。#5265・#5334 は closed で再使用しない。
2. **card_miss の証拠投影を完成させる。** #5347 の `screen`/`screen_text` 投影に加え、
   `battle_boss_card_missed` の emit（`battle_menus.py:2830-2837`）へ `ally`/`enemy`/
   `castle`/`side`（`_battle_labels` 外の top-level 引数）と `observed_metric.scrolls`/
   `cursor` の監査投影を追加すれば、次回発火時に scroll 0/枯渇・ボス城・敵種別・
   `own_attack_menu` 成立可否を issue 本文から一意に判定できる。
3. **非ボス城での発火に備える。** 本件（ch3・月 6-1）は中盤の通常城制圧戦だった可能性が
   あり、その場合 #5347 の `is_boss_castle` 条件で対象外になる。#5347 マージ後に非ボス城・
   未認識召喚獣・防衛側での同型発火が runtime で確認されたら、`own_attack_menu` の
   ボス城・認識召喚獣・攻撃側限定（fail-closed 意図）の緩和を実害確認後に検討する。
   scroll 枯渇型（`SUMMON_CARD_SCROLL_LIMIT = 3` で行が現れない画面）が出たら別途
   box 構造の解析が必要。

## 検証で使った根拠（抜粋・origin/main = `46d17f1a5` の行番号）

- `src/docich/hanjuku/flows/battle_menus.py`: `_carried_card_left` L895-908、
  `_white_egg_available` L1924-1933（`kind == 'battle_menu'` 要求）、
  `_screen_egg_empty` L1936-1947、`egg_battle_step` L2402-（`general_reading` L2462、
  `enemy_rows`/`enemy_monsters` L2471-2474、`empty_egg` L2500、
  `if action == 'attack':` L2644、interrupted card_flow 再開 L2647-2662、
  `cards_left` L2663-2672、owner 意図コメント L2673-2676、scroll gate L2677-2687、
  resume L2688-2691、open gate L2692-2713、defender preempt L2743-2749、
  emit L2817-2837）、`SUMMON_CARD_SCROLL_LIMIT` L2893
- `src/docich/hanjuku/judgment/battle.py`: `_battle_labels` L90-93、
  `_confirmed_carried_kit` L989-1001、`_own_egg_empty` L2815-2833
- `src/docich/hanjuku/decision_log.py`: `_record` L11-19（screen_kind 非付記）
- `src/docich/hanjuku/os_layer/screen.py`: grey predicate L1089-1096、卵行 OCR 欠落の
  実測コメント L1549-1552、卵行無しでも `egg_battle_menu` 化する分類 L1545-1553
- `src/docich/hanjuku/month_audit.py`: `_track_card_miss` L3163-3222（battle dict
  L3193-3196、fingerprint L3217-3218、auto_file L3219）、`_render_card_miss`
  L3617-3637（固定テンプレート L3635、「画面: None」の生成元 L3633）、`_fingerprint`
  L3032-3034
- `src/docich/hanjuku/orchestrator.py`: `egg_battle_menu` 振り分け L2265-2266（章条件なし）
- `src/docich/hanjuku_chart.py`: `BOSSES` L1197-1201（ch3 = プリンス）、
  `BOSS_CASTLES` L455-459（ch3 = `'ボス'`）、`boss_castle` L1226-1232、
  `is_boss_castle` L1365-1366
- `src/docich/hanjuku_reference.py`: バグストーム L92-94（id 24・monster 100・boss 50・
  egg_drop 0）、`BOSS_SACRIFICE_CARDS` L178-183、`BOSS_GENERALS` L1234（プリンス含む）
- `src/docich/hanjuku/strategy/kit.py`: `_auto_carry_safe_card` L117-146
  （egg_drop 0 の在庫札を自動携行、v1448/#5336）、`_deploy_cards` L51-114
- `src/docich/hanjuku/flows/deploy.py`: `_strong_card_names` L480-500（将軍型ボスは
  `STRONG_CARDS` へ早期 return、L493-495）
- `src/docich/hanjuku/version.py`: `BOT_VERSION` L7（v1455 実測）
- 観測バージョンとの差分: `git diff c435441f7..46d17f1a5`（battle_menus おくのて系
  3 ハンク、month_audit render 1 ハンクのみ。発火経路無差分）
- PR #5347 ブランチ head `a9fd49d46`（marker `hanjuku-chart-v1452-battle-card-row-evidence`）:
  `git diff 46d17f1a5...a9fd49d46` で `own_attack_menu` 実装と `_track_card_miss` の
  `screen`/`screen_text` 投影を実測。同ブランチの追加テスト
  `tests/test_hanjuku_boss_egg_row_log.py` は #5259/#5331/#5322 を直接引用し、
  `test_a_carried_card_scrolls_when_the_grey_egg_row_is_omitted` /
  `test_hidden_egg_row_fallback_requires_a_live_own_attack_menu` 等 4 件を追加
- 関連 VERIFY: `verify/5352-card-miss:VERIFY-5352.md`（PR #5371、(a)）、
  `verify/5331-card-miss:VERIFY-5331.md`（PR #5360、(a)）、
  `refs/remotes/origin/verify/5328-card-miss:VERIFY-5328.md`（PR #5356、(a)）、
  `refs/remotes/origin/verify/5322:VERIFY-5322.md`（(a)）、
  `refs/remotes/origin/verify/5259-card-miss:VERIFY-5259.md`（PR #5287、(a)）

## 手順上の注記

- テスト実行は未実施: 本環境が `python3`/`pytest` の起動を拒否した。関連テスト
  （実行できていれば該当）: `tests/test_hanjuku_chart_bot.py:4548-4607`
  （card_miss 起票/不起票 3 件）、`tests/test_hanjuku_month_audit.py:2028-2053`
  （`test_card_miss_decision_seals_an_auto_file_incident`）、
  `tests/test_hanjuku_boss_egg_row_log.py`（main 6 件＋#5347 で 4 件追加）。
  VERIFY-5328 では別環境で同 2 ファイル 142 passed が記録されている。
- `gh` 不可のため issue #5362 本文・PR 状態は依頼記載の要点とローカル git 参照のみ使用。
  推測で補っていない。#5347 の head/状態は VERIFY-5331/5328/5352 の記録とローカル
  ref（`a9fd49d46`、marker v1452）の照合。
- fingerprint `e47e544ed63ff621` の再計算は未実施（本環境が `python3`/`sha256sum` を
  拒否）。計算式は `_fingerprint(3, 'card_miss', ['バグストーム', ''])` =
  `sha256("3|card_miss|バグストーム+")[:16]`（`month_audit.py:3032-3034,3217-3218`、
  emit が castle を渡さないため第3要素は空文字）で、形式（16 hex・章・札依存）とは整合。
- 本ファイルは `verify/5362-card-miss`（現 checkout、base `46d17f1a5` = v1455）の
  worktree ルートに**未コミット**で置いてある。依頼の「ブランチでコミット」はこの環境
  では実行できなかった — `git add`/`git commit` 等の書き込み系 git 操作を環境が拒否
  した（VERIFY-5331/5352 と同じ制約パターン）。別環境で
  `git add VERIFY-5362.md && git commit` が必要。root の新規追加のため origin/main 系へ
  そのまま rebase/merge しても衝突しない。push と PR 作成は依頼どおり実施しない
  （検品ゲート側で実施）。
- read-only 検証。VERIFY-5362.md の作成以外のファイル編集・本番 VM 操作・push・
  PR 作成なし。
