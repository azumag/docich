# VERIFY-5331 — [半熟英雄] 敵召喚獣戦/ボス戦で切り札未使用: イッテツーン (ch11 戦闘)

- 対象: docich issue #5331 / 監査 `card_miss` (confidence: high)
- fingerprint `d334d605d320f149` / occurrence `3424e25335ee80172aa2`
- ラン: `g1190-ch1v9` / `hanjuku-chart-v1447-password-measured-order`（v1447 = `23c108d62`、main の祖先）
- ch11 / 月 18-1、未使用の携行札: イッテツーン、理由「敵召喚獣戦で携行切り札を開けず、こうげきへ進んだ」
- 検証範囲: read-only（本ファイルの作成以外の編集なし。実装・push・PR・issue コメント・本番 SSH なし）
- 検証ベース: `origin/main` = `3a829da9d`（`BOT_VERSION 'hanjuku-chart-v1451-defense-wait-probe'`、#5343・#5355 マージ済み。行番号はこのコミット基準）
- v1447 → origin/main で `src/docich/hanjuku/flows/battle_menus.py`、`src/docich/hanjuku/month_audit.py`、`src/docich/hanjuku/judgment/battle.py` の差分はゼロ（`git diff HEAD..origin/main` で対象外）— 発火経路は観測バージョンと現行で同一。`os_layer/screen.py` のみ L1288 付近に +6 行差分があり、L1305 以降の引用は origin/main 基準で +6 シフトする
- `gh` 実行不可（本環境が `gh`・`python3`/`pytest`・`git fetch`・`git grep`・`git blame`・`git reset`・`for-each-ref`・外部書き込みを拒否）。issue 本文は依頼記載の要点のみ使用。関連ブランチは `remotes/origin/...` のローカル参照で読み、PR 番号との対応付けはコミット件名・marker・内容からの照合（推測にはその旨明記）
- 同シグネチャ既検証: VERIFY-5259（`origin/verify/5259-card-miss`、ch9/ブラッキー、(a)）、VERIFY-5322（`origin/verify/5322`、ch11/ころぼぐんだん、(a)）— 本検証は両文書の行番号を origin/main で再確認した上で同じ発火点を検証した

## 結論: (a) 実欠陥 — #5259 / #5322 と同一機構クラス。現行 main (v1451) で再発しうる

「召喚獣戦の切り札行を画面に出す」有界スクロールの発火条件に穴があり、携行が
確定した切り札を一度も一覧化せず `こうげき` を確定しうる。同一 run `g1190-ch1v9`
で #5259（ch9 ブラッキー、v1423）→ #5322（ch11 ころぼぐんだん、v1445）→ 本件
（ch11 イッテツーン、v1447）とバージョンを跨いで継続発火しており、欠陥は
現行 main に生存する。owner の意図（`battle_menus.py:2639-2642` のコメント、
2026-11-23「退却は禁止、札が無いときだけ攻撃」`:2685-2686`）は「召喚獣戦では
通常攻撃より先に切り札を開く」であり、携行札を残して攻撃確定した判断は
設計意図と矛盾する。検出器は仕様どおり実事象を報告している。

## 根拠となるコード箇所（ファイル・行、origin/main = `3a829da9d`）

### 発火遷移（焦点1）

- `src/docich/hanjuku/flows/battle_menus.py:2629-2638` — `cards_left` は
  `planned_cards` 優先、空なら `_confirmed_carried_kit`（出撃確定キット帳簿、
  `judgment/battle.py:989-1001`）で埋める。「イッテツーン」は確定携行札。
- `battle_menus.py:2643-2653` — 切り札行揭示の有界スクロールゲート。発火には
  `cards_left and 'きりふだ' not in screen.text and ('たまごをつかう' in
  screen.text or empty_egg) and not card_flow` がすべて必要。
- 灰色の卵行は白 OCR（`screen.text`）に載らない。灰色行は灰色専用 predicate で
  しか読まれない例: `os_layer/screen.py:1089-1096`（`_okunote_only_box` の
  灰色きりふだ/たいきゃく行の検出）および使用済み卵行が OCR から落ちる既知事例
  `os_layer/screen.py:1540-1553`（origin/main 行番号。コメント
  「the same spent-egg greying … たまごをつかう drops out of OCR」）。
- `_screen_egg_empty`（`battle_menus.py:1902-1913`）は `_white_egg_available`
  （`:1890-1899`、白行の可視のみ）の次に `_own_egg_empty`
  （`judgment/battle.py:2815-2833`）を見る。後者は fail-closed で、観測済み
  整数 0 または `egg_zero_owners` 登録がない限り `empty_egg` は立たない。
  **卵行が OCR 欠落かつ卵回数が未確定の画面では、ゲート前提の
  `('たまごをつかう' in text or empty_egg)` が不成立となり、
  `summon_card_scroll` を 1 回も消費せず攻撃経路へ直行する。**
- `battle_menus.py:2647` + `:2859` — ゲートが立っても
  `SUMMON_CARD_SCROLL_LIMIT = 3` で打ち止め。3 回の down でも `きりふだ` が
  OCR に現れなければ同じ攻撃経路へ落ちる（scroll 枯渇型）。
- `battle_menus.py:2658-2679` — `きりふだ` が text にあれば
  `_battle_menu_to`（`os_layer/commands.py:395-404`、`_options`
  `readers.py:45-47` から目標行を探しカーソル未読・行なしなら None）で
  行へ向かい、`here` で `battle_summon_card_open` → `card_flow` 開始。
  目標行が `_options` に無い（灰色行のみ等）とき `move is None` で攻撃経路へ。
- `battle_menus.py:2783-2802` — `move == 'here' and label == 'こうげき'`（攻撃
  行を実際に選択した瞬間）に `missed = _carried_card_left(...)`（`:895-908`）
  を取り、`cards_left` 非空・`cards_used` 空・`boss_card_missed_noted` 未設定
  のときだけ `battle_boss_card_missed` を起票（`:2793-2802`、#5217/#5241 の
  ガード）。issue の理由文はこの `reason` と一致し、今回の起票は「1 枚も
  使わずに攻撃確定」の裏付けになる。
- 起票 → issue 投影: `_track_card_miss`（`month_audit.py:3163-3222`）は
  `battle` dict に `row.get('ally'/'enemy'/'castle'/'screen_kind')` を読む
  （`:3193-3196`）が、`_record` 呼出（`battle_menus.py:2796`）は
  `**_battle_labels`（chart_step・strategy_variant・deviation_reason のみ、
  `judgment/battle.py:90-93`）＋ `card` + `reason` + `observed_metric` しか
  渡さず、`decision_log._record`（`decision_log.py:11-19`）も screen_kind を
  付記しない。**`戦闘: 不明 vs 不明 / 城: 不明 / 画面: None` はこの投影欠落の
  結果であり、実際の戦闘が不明だったことの証拠ではない。** `observed_metric`
  の `screen`/`text`/`scrolls`/`cursor`（`battle_menus.py:2797-2801`）は記録
  されるが issue へ投影されず捨てられる（`month_audit.py:3193-3196`）。
- 「卵なし・奥の手不可のときに」は `_render_card_miss` の固定テンプレート文
  （`month_audit.py:3635`）で実測条件ではない。

### 既知の類似件との異同（焦点1の比較）

- fingerprint は `_fingerprint(chapter, 'card_miss', [card, castle])`
  （`month_audit.py:3217-3218`）なので章・札が違えば別 issue。#5259 = ch9/
  ブラッキー、#5322 = ch11/ころぼぐんだん、本件 = ch11/イッテツーン。
  同一 run で 3 発目の occurrence であり、機構クラスは VERIFY-5259・
  VERIFY-5322 で (a) と確定済み。本件でも発火点のコードは同一（v1447→main
  で当該ファイル無差分）。
- **#5328 は検品ゲートで比較可能になった。** #5328 は Devin Web で検証完了し、結論は
  **(a) 実欠陥**（#5259 / #5322 / #5331 と同一機構クラス、同一 run `g1190-ch1v9` で
  4 件目の発火）。PR #5356（VERIFY-5328.md のみ、CI green、MERGEABLE）を作成済みで、
  issue へは検証結論を投稿済み。本件は同一 run で 3 発目、#5328 は 4 発目であり、
  機構クラスは #5328 でも再確認された。#5322 との同一機構性は VERIFY-5322
  （`origin/verify/5322`）の記述と本検証のコード再読で確認済み。

### PR #5265（CLOSED）と後継 #5347 のカバー範囲（焦点2）

- 対応ブランチ: `origin/fix/5259-summon-card-row-v1426`（head `917ab8e5b`
  2026-11-24 08:12、marker `hanjuku-chart-v1441-summon-card-hidden-row`。
  ブランチ名は v1426 だが marker は v1441）。
- **検品ゲートでの訂正：PR #5265 は CLOSED（unmerged）である。** 初版は「Draft、v1441」
  として現行の修正車両のように扱ったが、#5265 は閉じられており、内容は #5334 →
  **#5347（OPEN、Draft解除済み、v1452、head `a9fd49d46`、CI green）** へ継承されている。
  現行の修正車両は #5347 であり、#5265 ではない。
- 変更: `battle_menus.py` のゲートへ第3トリガ `own_attack_menu` を追加 —
  `chart.is_boss_castle(battle.get('castle'))`（`hanjuku_chart.py:1365-1366`）
  かつ `len(enemy_rows) == len(enemy_monsters) == 1` の `enemy_summon_menu`、
  および `general_reading is not None and side == 'attack' and
  enemy_summon_menu and 'こうげき' in screen.text` を全て満たす場合に限り、
  卵行 OCR 欠落でも `SUMMON_CARD_SCROLL_LIMIT` 内の down を navigation-only で
  許す（選択は引き続き `きりふだ` 実表示を要求、fail-closed）。
- **カバー判定: 本件の部分機構（卵行 OCR 欠落＋回数未確定→ゲート不成立型）は
  機構クラスとしてカバーする。** ただし成立条件が厳格で、issue の証拠
  （`observed_metric` 未投影）からは本 occurrence が「ボス城・attack 側・
  生存将軍パネル・認識済み召喚獣1体」を満たしたか一意に確かめられない。
  満たしていれば発火前に scroll→開放へ進む。**対象外の残存経路**: scroll
  枯渇型、非ボス城/未認識召喚獣での同型欠落、`card_flow` 残存（announce/
  selection_planned/read_kit 済み）による両ゲート skip。監査投影欠落
  （`画面: None`・`不明`）は #5265 単体では未対応（後述 #5334 系が担当）。
- 同ブランチの追加テスト（`tests/test_hanjuku_boss_egg_row_log.py`）は
  `#5259 / #5331` と本 issue を直接引用し、`text='こうげきたいきゃく'`（卵行・
  切り札行ともに OCR 欠落）で down を 1 回だけ送ることを固定。負例として
  非ボス城・未認識敵・将軍パネル未読での不発火も固定されている。

### PR #5334 の差分との重なり・吸収状況（焦点3）

- 依頼記載どおり #5334「preserve event ownership and reveal hidden summon
  cards (v1449)」は 2026-11-24 12:10:20 JST に unmerged close。
- 対応ブランチ（照合は content/marker ベース）: `origin/fix/5312-5331-hanjuku-
  evidence-v1449`（head `32500a0f5` 11:37、marker
  `hanjuku-chart-v1449-dome-camp-terminal-evidence`）は3系統を含む:
  1. `battle_menus.py` の `own_attack_menu` フォールバック（#5265 と同一内容、
     「integrate hidden summon card row」`d6a1bc9d1`/`1809d7b1c`）— **本件の
     修正方向と完全に重なる**
  2. `orchestrator.py` の `event_text_advance_step`（築城・ドーム野営の
     イベント文所有権 = 題名の "preserve event ownership"、#5312/#5278 系）
  3. `month_audit.py` の card_miss `screen`/`screen_text` 投影
     （`screen = row.get('screen_kind') or metric.get('screen')` フォールバック
     と `screen_text` 追加、`battle` dict への `_bounded_text`）+ chikujou
     terminal diagnostic + camp `screen_text` — **本件の「画面: None」
     「不明 vs 不明」を次回発火から可視化する修正**（ただし `scrolls`・
     `ally`/`enemy`/`castle` は依然未投影）。
- 吸収状況: 同内容は `origin/fix/5312-5318-5331-evidence-v1449-main`（head
  `ad9d20873` 12:07:59、v1451 marker）→ `origin/fix/5312-5318-5331-evidence-
  v1451-main`（head `a9fd49d46` 2026-11-24 12:33:55、main@v1451 との merge +
  「cover chapter 11 summon card row OCR omission (#5322)」、marker
  `hanjuku-chart-v1452-battle-card-row-evidence`）へ rebase 継承されている。
  close 後も作業は継続しており、後継 PR は **#5347（OPEN、Draft解除済み、v1452、
  head `a9fd49d46`、CI green）** である（検品ゲートで gh 照合済み。初版の
  「open Draft, v1451」は古い情報で訂正する）。**origin/main には未取り込み —
  `git diff origin/main...各ブランチ` で `own_attack_menu` が main に無い
  ことを確認済み。**

## 判定詳細

- **(b) でない**: マージ済み修正はどれもこのゲートを塞いでいない。#5218
  （`8e6889783`）は中断 card_flow の `summon_card_resume` 再開と `or empty_egg`
  （観測済み 0 のみ）、#5241（`aa83fb46a`）は `cards_used` 重複起票ガード、
  `b2e1407a4`（2026-11-22 ローカルラン修正束）はゲート本体。卵行 OCR 欠落型は
  いずれも対象外。
- **(c) でない**: 起票条件は実測の確定携行（`planned_cards` または
  `_confirmed_carried_kit`）・未使用・攻撃行確定に基づく。投影欠落は証拠の
  品質問題であって起票自体の正しさを否定しない。
- **(d) 要素（残存不確実性）**: issue 単体では scroll 0 型か scroll 枯渇型かを
  区別できない（`observed_metric.scrolls` 未投影）。敵・城・side が不明なため
  `own_attack_menu` の厳格条件を本 occurrence が満たしたかも確定できない。
  最も parsimonious な読みは #5259/#5322 と同じ卵行欠落型だが、issue 本文だけ
  では機構の一意特定は不能。起票の実欠陥性（携行札を残して攻撃確定した
  事実）には影響しない。

## §6 修正案（実装はしない）

1. **後継 PR #5347（OPEN、Draft解除済み、v1452、head `a9fd49d46`、CI green）を
   DSH レビュー＋mergeable 確認のうえマージする。** `own_attack_menu` は
   navigation-only・fail-closed で設計上安全（実表示されるまで選択しない）。
   検品ゲート時点では DSH レビュー記録は未確認のため、レビュー依頼が要る。
2. **card_miss の証拠投影を完成させる。** 同系統の audit 変更は `screen`/
   `screen_text` までをカバーするが、`battle_boss_card_missed` の emit に
   `ally`/`enemy`/`castle`（`_battle_labels` 外の top-level 引数）と
   `observed_metric.scrolls`/`cursor`/`card_flow` 有無を投影する部分は未対応。
   `battle_menus.py:2796-2801` の `_record` 引数と `month_audit.py:3193-3196`
   の `battle` dict へ追加すれば、次回発火時に scroll 0/枯渇・ボス城・敵種別を
   issue 本文から一意に判定できる。
3. 非ボス城・未認識召喚獣での同型発火が runtime で確認されたら、
   `own_attack_menu` のボス城・認識召喚獣限定（fail-closed 意図）の緩和を
   実害確認後に検討する。scroll 枯渇型（`SUMMON_CARD_SCROLL_LIMIT = 3` で
   行が現れない画面）が出たら別途 box 構造の解析が必要。

## 検証で使った根拠（抜粋・origin/main = `3a829da9d` の行番号）

- `src/docich/hanjuku/flows/battle_menus.py`: `_carried_card_left` L895-908、
  `_white_egg_available` L1890-1899、`_screen_egg_empty` L1902-1913、
  `general_reading`/`enemy_rows`/`enemy_monsters` L2428-2439、attack block
  L2610-2803（interrupted card_flow 再開 L2613-2628、`cards_left` L2629-2638、
  owner 意図コメント L2639-2642、scroll gate L2643-2653、resume L2654-2657、
  open gate L2658-2679、`_defender_summon_egg_menu` preempt L2709-2715、
  emit L2783-2802）、`SUMMON_CARD_SCROLL_LIMIT` L2859
- `src/docich/hanjuku/judgment/battle.py`: `_battle_labels` L90-93、
  `_confirmed_carried_kit` L989-1001、`_own_egg_empty` L2815-2833
- `src/docich/hanjuku/os_layer/commands.py`: `_battle_menu_to` L395-404、
  `_menu_move_batch` `battle_menus.py` L212-245、`_command_rows_to` L255-271
- `src/docich/hanjuku/os_layer/readers.py`: `_options` L45-47
- `src/docich/hanjuku/os_layer/screen.py`: grey predicate L1089-1096、使用済み
  卵行の OCR 欠落コメント L1540-1553（origin/main）
- `src/docich/hanjuku/decision_log.py`: `_record` L11-19（screen_kind 非付記）
- `src/docich/hanjuku/month_audit.py`: `_track_card_miss` L3163-3222（battle
  dict L3193-3196、fingerprint L3217-3218）、`_render_card_miss` L3617-3637
  （固定テンプレート L3635）
- `src/docich/hanjuku_chart.py`: `is_boss_castle` L1365-1366
- `src/docich/hanjuku/version.py`: L7（v1447 は `23c108d62`、origin/main は v1451）
- 関連コミット: `b2e1407a4`（ゲート本体 2026-11-22）、`8e6889783` #5218
  （resume + `or empty_egg`）、`01c879bab` #5221（card_miss 自動起票 +
  `_carried_card_left`）、`aa83fb46a` #5241（`cards_used` ガード）
- 関連ブランチ（origin）: `fix/5259-summon-card-row-v1426`（#5265、marker
  v1441）、`fix/5331-summon-card-hidden-row-v1448`（marker v1448、同じ
  battle_menus 変更のみ）、`fix/5312-5331-hanjuku-evidence-v1449`（#5334
  対応候補、marker v1449）、`fix/5312-5318-5331-evidence-v1449-main`、
  `fix/5312-5318-5331-evidence-v1451-main`（marker v1452、後継）、
  `verify/5259-card-miss`（VERIFY-5259.md）、`verify/5322`（VERIFY-5322.md）

## 手順上の注記

- テスト実行は未実施: 本環境が `python3`/`pytest` の起動を拒否した。関連
  テスト（実行できていれば該当）: `tests/test_hanjuku_chart_bot.py`
  L4550-4606（`battle_boss_card_missed` の起票/非起票）、
  `tests/test_hanjuku_month_audit.py` L2025 付近（card_miss インシデント）、
  `tests/test_hanjuku_boss_egg_row_log.py`（修正ブランチが #5331 回帰を追加）。
  → **検品ゲートで実行済み：現行 main（3a829da9d）で上記3ファイル 145件 green**
  （boss_egg_row_log + month_audit 142件、chart_bot の card_miss 起票3件）。
- `gh` 不可のため issue/PR 状態は依頼記載の要点＋ローカル git 参照のみ。
  #5328 の本文は未確認。#5334 のブランチ同定・#5347 への吸収はコミット
  件名・marker・内容の照合であり、PR 番号との 1:1 対応は未確認と明記する。
  → **検品ゲートで gh 照合済み：PR #5265 は CLOSED（unmerged）、PR #5347 は
  OPEN・Draft解除済み・v1452・head a9fd49d46・CI green。#5328 は (a) 実欠陥で
  PR #5356（CI green）と確認済み。**
- `git reset --hard` 等の書き込み系 git 操作は環境が拒否したため、検証は
  `git show origin/main:...`・`git diff` の blob 参照で実施した。行番号は
  全て origin/main 基準（当該3ファイルは v1447 と無差分のため観測世代とも
  一致）。
- `git add` / `git commit` も実行環境が拒否したため、本ファイルは
  `verify/5331-card-miss`（現 checkout、base は `23c108d62`）の worktree
  ルートに**未コミット**で置いてある。依頼の「ブランチでコミット」はこの
  環境では実行できなかった — 別環境で `git add VERIFY-5331.md && git commit`
  が必要（VERIFY-5313 と同じ制約パターン）。なお `verify/5331-card-miss` の
  base は `origin/main` の祖先 `23c108d62` で、本ファイルは root の新規追加の
  ためそのまま origin/main 系へ rebase/merge しても衝突しない。
  → **検品ゲートで origin/main（3a829da9d）へ merge --ff-only したうえで
  コミット・push・PR 化まで完了。**
- 正本 handoff: `DOCICH_HANDOFF_PATH` 未設定のため未読（AGENTS.md §1 に従い
  通常検証）。
- read-only 検証。VERIFY-5331.md の作成以外のファイル編集・本番 VM 操作・
  push・PR 作成なし。
