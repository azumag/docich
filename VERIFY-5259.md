# VERIFY-5259 — [半熟英雄] 敵召喚獣戦/ボス戦で切り札未使用: ブラッキー (ch9 戦闘)

- 対象: azumag/docich issue #5259 / fingerprint `883c71d9e8e1cf93` / occurrence `a27c0a6ff69e52fa4f59`（再発 `575ef56475a3d96edc08`）
- ラン: `g1190-ch1v9` / generation 1190 / `hanjuku-chart-v1423-ch9-hero-front`（v1423 = `6d00a1f15`、main の祖先）
- 検証範囲: read-only（本ファイルの作成以外の編集なし）
- 現行 main: `c3cf80fda`（v1432）。v1423→HEAD 間で `egg_battle_step` の切り札揭示ゲートに差分なし（v1425/v1428/v1429 は別箇所）

## 結論: (a) 実欠陥 — #5217 とは別機構。現行 main で再発しうる

「召喚獣戦の切り札行を画面に出す」ゲートの前提条件に穴があり、携行が確定した
切り札を一度も一覧化せず攻撃へ進みうる。owner の修正 PR #5265 は主たる部分機構
（ボス城・既知召喚獣・卵行 OCR 欠落）を修正するが未マージのため、現行 main では
同じ欠落条件で再発しうる。#5217 の機構（中断された card_flow が古いメニュー状態に
残る）とは別系統であり、#5218/#5241/#5244 では本件をカバーしていない。

## 根拠となるコード箇所（ファイル・行、main @ c3cf80fda）

### 切り札を開かず攻撃へ進んだ遷移（検証観点1）

- `src/docich/hanjuku/flows/battle_menus.py:2643-2653` — 「きりふだ行を出す」有界スクロールのゲート。発火には
  `cards_left and 'きりふだ' not in screen.text and ('たまごをつかう' in screen.text or empty_egg) and not card_flow`
  のすべてが必要。**灰色の卵行は白 OCR（`screen.text`）に載らない**（`os_layer/screen.py:1086-1092`・`:1523-1535` — 灰色行は grey predicate 系でのみ読まれる。`:1523-1526`・`:1531-1535` はまさに「使用済み卵行が灰色で OCR から落ちる」既知事例）。
- `src/docich/hanjuku/judgment/battle.py:2737-2755` — `_own_egg_empty` は fail-closed: `empty_egg` が立つのは「この将軍の観測済み整数 0」または `egg_zero_owners` 登録のみ。未読・不明な回数は unknown = `empty_egg` False。
- つまり **卵行が OCR 欠落（灰色）かつ卵回数が未確定** のとき、ゲート前提 `('たまごをつかう' in text or empty_egg)` が不成立となり、`summon_card_scroll` を 1 回も消費せず 2716 行以降の攻撃経路へ直行する。
- `src/docich/hanjuku/flows/battle_menus.py:2783-2802` — `move == 'here' and label == 'こうげき'`（攻撃行を実際に確定した瞬間）で `missed = _carried_card_left(...)`（`:895-908`、planned_cards または `_confirmed_carried_kit`）を取り、`battle_boss_card_missed` を起票。issue の理由文「敵召喚獣戦で携行切り札を開けず、こうげきへ進んだ」はこの `reason` と一致。
- 第2の部分機構: ゲートが成立しても `SUMMON_CARD_SCROLL_LIMIT = 3`（`:2859`）で打ち止め。きりふだ行が 3 回の down でも OCR に現れなければ同じ攻撃経路へ落ちる（この場合は「画面が出ない」側の問題）。
- issue 単体では「ゲート不成立（scroll 0）」と「scroll 枯渇（scroll 3）」を区別できない。`battle_boss_card_missed` の `observed_metric` には `scrolls`・`screen` が入るが（`:2797-2801`）、監査が issue へ投影する `battle_event` は `ally/enemy/castle/screen/reason/cards_left` だけを保持し（`month_audit.py:3154-3157`）、`scrolls`・`card_flow` 状態は捨てられる。`画面: None`・`不明 vs 不明 / 城: 不明` はこの投影欠落の結果で、実際の戦闘が不明だったわけではない（`battle_boss_card_missed` は `ally/enemy/castle` を `_record` に渡していない）。

### #5217 系との異同（検証観点2）

- #5217 の機構: 敵召喚の会話がメニューを差し替えた際、未選択の `card_flow`（stage `menu`/`down`/`list`）が古いカーソル状態のまま残り札一覧が開かなかった。修正は `battle_menus.py:2613-2628` — 未選択かつ未 read_kit の card_flow を `summon_card_resume` へ変換し、召喚メニューの実一覧から再開する（PR #5218）。PR #5241 は「1 枚使った戦闘では card_miss を記録しない」ガード（`:2793` の `not battle.get('cards_used')`）、PR #5244 は危険戦闘での 2 枚目以降の開放。
- 本件は**異なる機構**: card_flow の中断ではなく、「きりふだ行を出す」前段ゲートの前提条件（卵行の可視性または卵回数確定 0）が、卵行 OCR 欠落＋回数未確定の画面で不成立になること。中断 card_flow 変換 (`:2613-2628`) は `card_flow` が存在する場合のみ走り、ゲートは `not card_flow` のときだけ評価される — 両経路は直交しており、#5217 系修正では本件の穴は塞がらない。
- （残余可能性: `selection_planned`/`read_kit` 済みの card_flow が残るとゲートが skip されるが、その場合札は `cards_selected` 入りして `missed` から外れるため、issue の「未使用」には整合しない。）

### PR #5265 のカバー範囲と CI FAILURE の影響（検証観点3）

- PR #5265（Draft、v1426 marker）は同じゲートへ `own_attack_menu` フォールバックを追加する: ボス城（`chart.is_boss_castle`）・生存する自将軍パネル（`general_reading`）・attack 側・`こうげき` 表示・既知召喚獣 1 体（`len(enemy_rows) == len(enemy_monsters) == 1`）をすべて満たす場合に限り、卵行 OCR 欠落でも有界 down を許す。選択は引き続き `きりふだ` の実表示を要求する navigation-only で、fail-closed 設計は守られている。
- カバーするのは上記部分機構（卵行欠落＋回数未確定、ボス城・既知召喚獣に限定）。**非ボス城の敵召喚獣戦**と**scroll 枯渇**の部分機構は対象外 — 前者は「未知メニューへ盲目 down しない」ための意図的な絞り込み（`tests` の非ボス・未知敵の負例も同 PR が追加）。本 issue の戦闘がボス城だったかは記録から一意に決められない（`城: 不明` は投影欠落、前述）。
- CI FAILURE は**最新 HEAD ではないコミット上の失敗**。失敗 run 45236572179 は `0b6d2a71`（9/10 コミット目）で実行され、失敗したのは PR が追加した `test_a_carried_card_scrolls_when_the_grey_egg_row_is_omitted` — fixture が `ヘーゼル`（最大 HP 33）に HP40 を与えたため `_egg_general_reading()` がパネルを拒否し、`own_attack_menu` が立たず通常カーソル移動になった。最終コミット `f3e667d3` がこの fixture を HP30 へ修正済みで、**`f3e667d3` には CI run が存在しない**（再実行待ち）。本検証 box（Python 3.10）で `tests/test_hanjuku_boss_egg_row_log.py` 8/8 pass を確認。したがって CI FAILURE は「古いコミット上の fixture 不整合」であり、修正の機構自体を否定するものではない — ただし HEAD の CI 証跡はまだない。
- PR は draft で main から遅れている（owner コメントどおり BOT_VERSION 再採番・main 追従が必要）。現行 main には何のカバーもない。

### 「卵なし・奥の手不可」条件の影響（検証観点4）

- issue 本文の「卵なし・奥の手不可のときに」は **静的テンプレート文**（`month_audit.py:3596` の固定行）であり、実測条件ではない。監査は `empty_egg` の真偽や卵行の可視性を記録していない。
- 実際の分岐: 卵回数が**観測済み 0**（`empty_egg` True）ならゲートは発火し scroll ≤3 を行う — この場合の miss は scroll 枯渇型。卵行が灰色で OCR 欠落かつ回数未確定ならゲート不成立 — scroll 0 で直行する型。後者が PR #5265 の対象であり、issue の状況説明（灰色行が消える）と整合するより parsimonious な説明。
- 「奥の手不可」は攻撃側のこの経路では無関係（`_defender_summon_egg_menu` は `:2709-2715`、防衛側のみ）。

## 修正案

1. PR #5265 を main 追従・BOT_VERSION 再採番し、`f3e667d3` の CI を回してマージする（主たる部分機構を塞ぐ）。
2. `battle_boss_card_missed` に `ally`/`enemy`/`castle`/`screen_kind` を `_record` の top-level 引数として渡し、`battle_event` へ `scrolls` と `card_flow` 有無を投影する（`month_audit.py:3154-3157`）。これで次回発生時に scroll 0 / 枯渇を一意に判定できる。
3. 非ボス城の敵召喚獣戦で卵行が欠落する事例が出たら、`own_attack_menu` のボス城限定を緩めるか検討（現状は fail-closed 意図どおり、拡張は実害確認後が安全）。

## 手順上の注記

- read-only 検証。VERIFY-5259.md の作成以外のファイル編集・本番 VM 操作なし。
- `agent-guards/secret_scan.py` は本 box 未配置のため、投稿物（PR 本文・issue コメント）は同等のローカル秘密パターンスキャンで clean を確認してから投稿する（AGENTS.md §3.1 フォールバック）。
