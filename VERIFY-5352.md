# VERIFY-5352 — [半熟英雄] 敵召喚獣戦/ボス戦で切り札未使用: クースカン (ch11 戦闘)

- 対象: docich issue #5352 / 監査 `card_miss` (confidence: high)
- fingerprint `497c96b5221276f2` / occurrence `36c5e20483e57a827567`
- ラン: `g1190-ch1v9` / `hanjuku-chart-v1450-password-dispatch`（v1450 = `c01748172`、main の祖先）
- ch11 / 月 18-2、未使用の携行札: クースカン、理由「敵召喚獣戦で携行切り札を開けず、こうげきへ進んだ」
- 検証範囲: read-only（本ファイルの作成以外の編集なし。実装・push・PR・issue コメント・本番 SSH なし）
- 検証ベース: `origin/main` = `46d17f1a5`（`BOT_VERSION 'hanjuku-chart-v1455-password-in-game-retry'`。行番号はこのコミット基準）
- バージョン表記の訂正: 依頼文は「現行 main は v1453（PR #5361 マージ済み）」だが、本環境の `origin/main` は
  さらに `cf7f1a3c6`（v1454、#5364 非携行 issue の型表示）・`976b923b0`（VERIFY-5319、#5349）・
  `46d17f1a5`（v1455、#5367 パスワード失敗時のゲーム内リセット）まで進んでいる。検証は実測の
  `origin/main` = `46d17f1a5` に対して実施した。
- 観測版との差分: v1450 `c01748172` → `origin/main` で card_miss 発火経路は無変更。
  `battle_menus.py` の差分は v1453/#5361 のおくのて（`_safer_rescue_available` /
  `_okunote_risk_protected`、L1298 挿入・L1550 簡約・L1564 metric 拡張）のみで、
  切り札スクロールゲートと起票ブロックは同一。`month_audit.py` の差分は v1454/#5364 の
  `sortie_without_cards` render への +3 行（L3753 付近）のみで、`_track_card_miss` /
  `_render_card_miss` は同一行番号のまま。`password.py`（v1452/#5358・v1455/#5367）と
  `replan.py`（v1451/#5343）は本経路と無関係。v1453 分の +34 行シフトで
  VERIFY-5328/5331 の引用行は main でも一意に対応する。
- `gh` 実行不可（本環境が `gh`・`python3`/`pytest`・`git reset`/`for-each-ref`/`git grep` 等を拒否）。
  issue 本文は依頼記載の要点のみ使用。関連ブランチ・VERIFY はローカルの `refs/remotes/origin/*` と
  兄弟 worktree の blob 参照で読んだ。正本 handoff: `DOCICH_HANDOFF_PATH` 未設定のため未読
  （AGENTS.md §1 に従い通常検証）。
- 参照済み VERIFY: VERIFY-5328（`origin/verify/5328-card-miss` blob、PR #5356）、
  VERIFY-5331（`/home/hatch/work/docich-5331/VERIFY-5331.md`、PR #5360）、
  VERIFY-5322（`origin/verify/5322` blob）。いずれも結論 (a)・同一機構クラス。
- 同シグネチャ既検証: #5259（ch9/ブラッキー/v1423、(a)）、#5322（ch11/ころぼぐんだん/v1445、(a)）、
  #5328（ch11/ノリウツール/v1447、17-11＋17-12再発、(a)）、#5331（ch11/イッテツーン/v1447、(a)）。

## 結論: (a) 実欠陥 — #5259 / #5322 / #5328 / #5331 と同一機構クラス。現行 main (v1455) で再発しうる

「召喚獣戦の切り札行を画面に出す」有界スクロールの発火条件に穴があり、携行が確定した
切り札を一度も一覧化せず `こうげき` を確定しうる。同一 run `g1190-ch1v9` で #5259 →
#5322 → #5331 → #5328 に続く 5 件目の発火（本件は月 18-2、直前の #5331 は 18-1）。
v1423 → v1445 → v1447 → v1450 とバージョンを跨いで継続発火しており、欠陥は現行 main に
生存する。owner の意図（`battle_menus.py:2673-2676` のコメント「召喚獣戦では切り札行は
たまごをつかうの下にあり、先に切り札を開く」）に照らし、携行札を残して攻撃確定した判断は
設計意図と矛盾する。検出器は仕様どおり実事象を報告している。

## 根拠となるコード箇所（ファイル・行、origin/main = `46d17f1a5`）

### 発火遷移（焦点1: 実欠陥か設計どおりか）

発火関数は `egg_battle_step`（`src/docich/hanjuku/flows/battle_menus.py`）。

- `battle_menus.py:2663-2672` — `cards_left` は `planned_cards` 優先、空なら
  `_confirmed_carried_kit`（`judgment/battle.py:989-1001`: 出撃確定 `departure_binding`
  − 確定使用）で埋める。issue の「未使用の携行札: クースカン」は計画値ではなく実測の
  携行確定札（または計画携行札）。監査側も `_carried_card_left`
  （`battle_menus.py:895-907`）で同じ帳簿を見る。クースカンは既知の切り札
  （`hanjuku_reference.py` カード表 id 13、`STRONG_CARDS` 含む）。
- `battle_menus.py:2673-2676` — Owner 2026-11-22 の意図コメント: 召喚獣戦では
  `きりふだ` 行は `たまごをつかう` の下にあり、画面をスクロールしないと OCR に出ない。
  コード自身が「先に切り札を開く」を設計意図としている。
- `battle_menus.py:2677-2687` — 揭示ゲート。発火には
  `cards_left and 'きりふだ' not in (screen.text or '') and
  ('たまごをつかう' in (screen.text or '') or empty_egg) and not battle.get('card_flow')`
  がすべて必要。灰色の卵行は白 OCR（`screen.text`）に載らない — 灰色行は灰色専用
  predicate でしか読まれない例: `os_layer/screen.py:1089-1096`（`_okunote_only_box`
  系の灰色 きりふだ/たいきゃく 検出）、および使用済み卵行が OCR から落ちる実測コメント
  `os_layer/screen.py:1549-1552`（「the same spent-egg greying … たまごをつかう drops
  out of OCR」）。`empty_egg`（`battle_menus.py:2500`）は `_screen_egg_empty`
  （`:1936-1947`）経由で、白行の可視があれば偽（`_white_egg_available` `:1924-1933`）、
  なければ `_own_egg_empty`（`judgment/battle.py:2815-2833`）へ。後者は fail-closed で、
  観測済み整数 0 または `egg_zero_owners` 登録がない限り `empty_egg` は立たない。
  **卵行が OCR 欠落かつ卵回数が未確定の画面では、ゲート前提の
  `('たまごをつかう' in text or empty_egg)` が不成立となり、`summon_card_scroll` を
  1 回も消費せず攻撃経路へ直行する。**
- `battle_menus.py:2680-2687` + `:2893` — ゲートが立っても
  `SUMMON_CARD_SCROLL_LIMIT = 3` で打ち止め。3 回の down でも `きりふだ` が OCR に
  現れなければ同じ攻撃経路へ落ちる（scroll 枯渇型）。
- `battle_menus.py:2692-2713` — `きりふだ` が text にあれば `_battle_menu_to` で行へ向かい、
  `here` で `battle_summon_card_open` → `card_flow` 開始。目標行が `_options` に無い
  （灰色行のみ等）とき `move is None` で攻撃経路へ。
- `battle_menus.py:2817-2837` — `move == 'here' and label == 'こうげき'`（攻撃行を実際に
  選択した瞬間、L2817）に `missed = _carried_card_left(...)`（L2821）を取り、
  `cards_left` 非空・`cards_used` 空・`boss_card_missed_noted` 未設定のときだけ
  `battle_boss_card_missed` を起票（L2827-2837、#5217/#5241 のガード）。issue の理由文は
  この `reason`（L2836）と一致し、今回の起票は「1 枚も使わずに攻撃確定」の裏付けになる。
- 起票 → issue 投影: `_track_card_miss`（`month_audit.py:3163-3222`）は `battle` dict に
  `row.get('ally'/'enemy'/'castle'/'screen_kind')` を読む（`:3193-3196`）が、`_record`
  呼出（`battle_menus.py:2830`）は `**_battle_labels`（chart_step・strategy_variant・
  deviation_reason のみ、`judgment/battle.py:90-93`）＋ `card` + `reason` +
  `observed_metric` しか渡さず、`decision_log._record`（`decision_log.py:11-19`）も
  screen_kind を付記しない。**`戦闘: 不明 vs 不明 / 城: 不明 / 画面: None` はこの投影欠落の
  結果であり、実際の戦闘が不明だったことの証拠ではない。** `observed_metric` の
  `screen`/`text`/`scrolls`/`cursor`（`battle_menus.py:2831-2835`）は記録されるが
  issue へ投影されず捨てられる（`month_audit.py:3193-3196`）。
- 「卵なし・奥の手不可のときに」は `_render_card_miss` の固定テンプレート文
  （`month_audit.py:3635`）で実測条件ではない。

### 既知の類似件との異同（焦点1の比較）

- fingerprint は `_fingerprint(chapter, 'card_miss', [card, castle])`
  （`month_audit.py:3217-3218`、`_fingerprint` 本体 `:3032-3034`）なので章・札が違えば
  別 issue。#5259 = ch9/ブラッキー、#5322 = ch11/ころぼぐんだん、#5331 = ch11/イッテツーン、
  #5328 = ch11/ノリウツール、本件 = ch11/クースカン。同一 run で 5 発目の occurrence。
- 発火点の同一性: VERIFY-5259 / VERIFY-5322 / VERIFY-5328 / VERIFY-5331 が確定した
  発火点（スクロールゲート）は v1450 → main v1455 で無差分（v1453 の差分はおくのて系のみ）。
  月も連続（#5328: 17-11/17-12、#5331: 18-1、本件: 18-2）で、機構が矯正されないまま
  同一章・同一ランを通じて反復発火している実測。
- #5217 系（中断 `card_flow` の `summon_card_resume` 再開、`battle_menus.py:2647-2662`）
  は card_flow 存在時のみ動く別経路であり、本件の「一度も開かない」型をカバーしない。

### PR #5265（CLOSED）と後継 #5347 のカバー範囲（焦点2）

- #5265（対応ブランチ `origin/fix/5259-summon-card-row-v1426`、marker v1441）は
  CLOSED・unmerged であり、内容は **#5347（OPEN、Draft解除済み、v1452 marker、
  head `a9fd49d46` = `origin/fix/5312-5318-5331-evidence-v1451-main`、CI green）** へ
  継承されている（VERIFY-5331/5328 と同じ照合）。
- **origin/main への取り込み状況: 未マージ。** main のスクロールゲート
  （`battle_menus.py:2677-2679`）には `own_attack_menu` が存在しない —
  `('たまごをつかう' in (screen.text or '') or empty_egg)` のまま。
- #5347 の変更内容（`git diff origin/main...a9fd49d46` で実測）:
  - `battle_menus.py` ゲートへ第3トリガ `own_attack_menu` を追加:
    `enemy_summon_menu = chart.is_boss_castle(battle.get('castle')) and
    len(enemy_rows) == len(enemy_monsters) == 1`（`hanjuku_chart.py:1365-1366` の
    `is_boss_castle`、`battle_menus.py:2471-2474` の認識済み召喚獣行）かつ
    `general_reading is not None`（`:2462`）かつ `side == 'attack'` かつ
    `'こうげき' in screen.text` の全成立時に限り、卵行 OCR 欠落でも
    `SUMMON_CARD_SCROLL_LIMIT` 内の navigation-only down を許す。札選択は
    引き続き `きりふだ` 実表示を要求する fail-closed 設計。
  - `month_audit.py` の `_track_card_miss` が `screen`（`metric.get('screen')`
    フォールバック付き）と `screen_text`（`metric.get('text')`）を bounded projection
    し、`_render_card_miss` が「画面 / 文言」へ出す — 次回発火から卵行 OCR 欠落型か
    scroll 枯渇型かを文言で切り分けられる部分修正。ただし `scrolls`/`cursor` と
    `ally`/`enemy`/`castle` は依然未投影。
- **本 occurrence でフォールバック条件が成立していたかの判定**: issue の証拠からは
  判定不能（下記 (d) 要素）。ボス城（ch11 の boss = エッグママ、`hanjuku_chart.py:1197-1201`）
  か・`side == 'attack'` か・将軍パネルが読めたか・認識済み召喚獣がちょうど 1 体か・
  `こうげき` が text に見えていたか・scroll を何回消費したかが、投影欠落のため
  issue 本文に残らない。成立していれば発火前に scroll → 開放へ進み本起票は防げた。
  **対象外の残存経路**: 非ボス城・未認識召喚獣・防衛側での同型欠落、scroll 枯渇型、
  `card_flow` 残存（announce/selection_planned/read_kit 済み）による両ゲート skip。
- 同ブランチの追加テスト `tests/test_hanjuku_boss_egg_row_log.py` は #5259/#5331/#5322 を
  直接引用し、`text='こうげきたいきゃく'`（卵行・切り札行ともに OCR 欠落）で down を
  1 回だけ送ること、負例として非ボス城・未認識敵・将軍パネル未読での不発火を固定
  している。ch11 ボス城（`chart.boss_castle(11)`）の回帰も固定済み。

### #5328 との異同（焦点3）

- **不確実性は本件でも同じ。** #5328 も `戦闘: 不明 / 城: 不明` で、発生時にボス城・
  認識召喚獣・攻撃側が成立していたか確かめられず「#5347 で救えたか判定不能」とされた。
  本件 #5352 も同一の投影欠落下にあり、同じ判定不能性が残る。原因は記録側
  （`_battle_labels` に ally/enemy/castle/side/screen_kind を含めない設計）にあり、
  occurrence ごとの差ではない。
- 相違点は札と月のみ: #5328 = ノリウツール / 17-11（再発 17-12）、本件 = クースカン /
  18-2。クースカンは `STRONG_CARDS` に列挙される重い切り札であり、機構上の扱いに差はない。

## 証拠が薄い点 — (d) 要素として残す部分

結論は (a) だが、issue 単体では以下を一意に決められない（VERIFY-5328 §4 と同型）。

- **scroll 0 型か scroll 枯渇型か**: `observed_metric.scrolls` は記録されるが issue へ
  投影されない。卵行 OCR 欠落＋回数未確定でゲート不成立（#5259 型）か、ゲート成立後に
  3 回尽きたかは区別不能。同一 run で同機構が連続発火しているため最も parsimonious な
  読みは卵行欠落型だが、issue 単体の証拠では確定できない。
- **戦闘の内訳**: 敵・味方・城が `不明`。ボス城か・認識済み召喚獣だったか・攻撃側/
  防衛側かが不明なため、#5347 のフォールバックが本 occurrence を救えたかも判断不能。
- **「敵召喚獣戦」の分類自体**: reason 文（`battle_menus.py:2836`）は固定で、この攻撃
  ブロックは敵が将軍でも発火しうる。記録上は「召喚獣戦だった」ことの独立した裏付けはない。
- **「卵なし・奥の手不可」**: 静的テンプレート文（`month_audit.py:3635`）であり、実際の
  卵残数・おくのて可否は記録されていない。

## 判定詳細

- **(a) 実欠陥**: 確定携行札を開かず `こうげき` を確定したのは記録上の事実
  （`battle_boss_card_missed` は `cards_left` 非空・`cards_used` 空のときのみ起票、
  `battle_menus.py:2827-2828`）。原因機構は現行 main に残る。
- **(b) ではない**: マージ済み修正はどれもこのゲートを塞いでいない。#5218 系は中断
  `card_flow` の `summon_card_resume` 再開（`battle_menus.py:2647-2662`）、#5241 は
  `cards_used` 重複起票ガード、v1448〜v1455 のマージ済み PR は携行補充・パスワード・
  築城・防衛待機・おくのて保護で本経路無変更。#5347 は Ready for review だが未マージのため
  現行 main のカバーはゼロ。
- **(c) ではない**: 起票条件そのものは実測の確定携行（`planned_cards` または
  `_confirmed_carried_kit`）・未使用・攻撃行確定に基づく。投影欠落は証拠の品質問題で
  あり、起票の正しさを否定しない。
- **(d) 要素**: 上記の部分（scroll 0 vs 枯渇、敵・城・side の内訳）は証拠不足で判定不能。
  起票自体の実欠陥性（携行札を残して攻撃確定した事実）には影響しない。

## §6 修正案（実装はしない）

1. **後継 PR #5347（OPEN、Draft解除済み、v1452、head `a9fd49d46`、CI green）を
   DSH レビュー＋mergeable 確認のうえマージする。** `own_attack_menu` は navigation-only・
   fail-closed で設計上安全（実表示されるまで選択しない）。卵行 OCR 欠落型の主たる
   部分機構を塞ぐ。#5265・#5334 は closed で再使用しない。
2. **card_miss の証拠投影を完成させる。** #5347 の `screen`/`screen_text` 投影に加え、
   `battle_boss_card_missed` の emit（`battle_menus.py:2830-2837`）へ `ally`/`enemy`/
   `castle`/`side`（`_battle_labels` 外の top-level 引数）と `observed_metric.scrolls`/
   `cursor` の監査投影を追加すれば、次回発火時に scroll 0/枯渇・ボス城・敵種別・
   `own_attack_menu` 成立可否を issue 本文から一意に判定できる。
3. 非ボス城・未認識召喚獣・防衛側での同型発火が runtime で確認されたら、
   `own_attack_menu` のボス城・認識召喚獣・攻撃側限定（fail-closed 意図）の緩和を
   実害確認後に検討する。scroll 枯渇型（`SUMMON_CARD_SCROLL_LIMIT = 3` で行が現れない
   画面）が出たら別途 box 構造の解析が必要。

## 検証で使った根拠（抜粋・origin/main = `46d17f1a5` の行番号）

- `src/docich/hanjuku/flows/battle_menus.py`: `_carried_card_left` L895-907、
  `_white_egg_available` L1924-1933、`_screen_egg_empty` L1936-1947、
  `general_reading` L2462、`enemy_rows`/`enemy_monsters` L2471-2474、
  `empty_egg` L2500、`if action == 'attack':` L2644（interrupted card_flow 再開
  L2647-2662、`cards_left` L2663-2672、owner 意図コメント L2673-2676、
  scroll gate L2677-2687、resume L2688-2691、open gate L2692-2713、
  defender preempt L2743-2749、emit L2817-2837）、`SUMMON_CARD_SCROLL_LIMIT` L2893
- `src/docich/hanjuku/judgment/battle.py`: `_battle_labels` L90-93、
  `_confirmed_carried_kit` L989-1001、`_own_egg_empty` L2815-2833
- `src/docich/hanjuku/decision_log.py`: `_record` L11-19（screen_kind 非付記）
- `src/docich/hanjuku/os_layer/screen.py`: grey predicate L1089-1096、使用済み卵行の
  OCR 欠落コメント L1549-1552
- `src/docich/hanjuku/month_audit.py`: `_track_card_miss` L3163-3222（battle dict
  L3193-3196、fingerprint L3217-3218、auto_file L3219）、`_render_card_miss`
  L3617-3637（固定テンプレート L3635）、`_fingerprint` L3032-3034
- `src/docich/hanjuku_chart.py`: `BOSSES` L1197-1201（ch11 = エッグママ）、
  `boss_castle` L1226-1232、`is_boss_castle` L1365-1366
- `src/docich/hanjuku_reference.py`: クースカン = カード id 13（monster_damage 56、
  `STRONG_CARDS` 含む）
- PR #5347 ブランチ `origin/fix/5312-5318-5331-evidence-v1451-main`（`a9fd49d46`、
  marker `hanjuku-chart-v1452-battle-card-row-evidence`）: `git diff origin/main...` で
  `own_attack_menu` 実装と `_track_card_miss` の `screen`/`screen_text` 投影を実測
- 関連 VERIFY: `origin/verify/5328-card-miss:VERIFY-5328.md`（PR #5356、(a)）、
  `docich-5331/VERIFY-5331.md`（PR #5360、(a)）、`origin/verify/5322:VERIFY-5322.md`（(a)）

## 手順上の注記

- テスト実行は未実施: 本環境が `python3`/`pytest` の起動を拒否した。関連テスト
  （実行できていれば該当）: `tests/test_hanjuku_boss_egg_row_log.py`、
  `tests/test_hanjuku_month_audit.py`、該当部分の `tests/test_hanjuku_chart_bot.py`。
  VERIFY-5328 では別環境で同 2 ファイル 142 passed が記録されている。
- `gh` 不可のため issue #5352 本文・PR 状態は依頼記載の要点とローカル git 参照のみ使用。
  推測で補っていない。#5347 の head/状態は VERIFY-5331/5328 の記録とローカル ref
  （`a9fd49d46`、marker v1452）の照合。
- `git reset --hard`/`git add`/`git commit` 等の書き込み系 git 操作は環境が拒否したため、
  検証は `git show origin/main:...`・`git diff` の blob 参照で実施した。行番号は全て
  `origin/main` = `46d17f1a5` 基準（worktree の checkout は `2cbe3542f` = v1451 で、
  card_miss 経路は main と無差分）。
- 本ファイルは `verify/5352-card-miss`（現 checkout、base `2cbe3542f`）の worktree
  ルートに**未コミット**で置いてある。依頼の「ブランチでコミット」はこの環境では
  実行できなかった（VERIFY-5313/5331 と同じ制約パターン）— 別環境で
  `git add VERIFY-5352.md && git commit` が必要。root の新規追加のため origin/main 系へ
  そのまま rebase/merge しても衝突しない。push と PR 作成は依頼どおり実施しない
  （検品ゲート側で実施）。
- read-only 検証。VERIFY-5352.md の作成以外のファイル編集・本番 VM 操作・push・
  PR 作成なし。
