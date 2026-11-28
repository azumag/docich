# VERIFY-5776

## 結論

**(a) 実欠陥**

owner の手動分析どおり、キャトルミューを携行した アンディーブ vs にせヒーローのボス戦で、
「ダメージ判定側の拒否・保留」と「退却判断が未選択の切り札一覧を閉じる」経路が合成し、
札を選択しないまま敗北した事象は実欠陥と判断する。誤検知や設計どおりの挙動ではない。

ただし検証は issue 本文・公開 PR ページ・現行 main のコードだけであり、本番 VM の
decision log にはこの環境から到達していない。拒否時の正確な HP・兵数・role の実測値は
issue 本文だけでは確定できない。

## 検証範囲と現行 main の状態

- worktree は `verify/5776`。git メタデータの読み取りだけで確認した範囲では、HEAD と
  `refs/remotes/origin/main` は同じ `c032a6a37c0bd96d6e683bd37c2dbd53ded619eb`。
- 現行 main は #5777 マージ後で、`BOT_VERSION` は `hanjuku-chart-v1593-unreadable-card-source`。
  したがって issue の指摘行は現行 main では既に移動・削除されている。 pre-fix の行単位の確認は、
  #5777 の公開 diff（特に `@@ -2140,27` と `@@ -957,6` の hunk）と、現行でも残る同一処理を
  突き合わせて行った。
- `gh` コマンド、git の書き込み操作、SSH、本番ログ参照、コード修正は行っていない。
  fingerprint / occurrence の再計算は不要とされたため行っていない。

## owner 分析とコードの突き合わせ

### 1. ダメージ判定による拒否

owner 分析はおおむね現行コードと一致する。

- `src/docich/hanjuku/judgment/battle.py` の `_card_damage_gate` は、初期値として
  `allowed=False, role='nonlethal_egg_risk'` を持ち、各種の許可条件を通らない札を拒否する
  （現行 main 810-957 行）。致死、卵なし、卵落とし、削り、制御連携、危険時の
  `critical_card_spend` などの許可経路もある。
- 拒否記録 `_card_gate_refused` は現行 main 981-1002 行にあり、issue 本文と同じ
  「単発で倒し切れる保証がなく、次の接触・壁判定で敵に召喚される危険がある」という固定文言で
  `battle_card_damage_rejected` を記録する。#5777 で追加された 21 行の直前が pre-fix の
  `_card_damage_gate` 終端なので、owner の指摘した pre-fix `battle.py:966` はこの拒否記録
  付近と一致する。厳密には「拒否の計算」は `_card_damage_gate`、`battle.py:966` 付近は
  その拒否を記録・保持する箇所である。
- 拒否後の `_card_gate_close` は `src/docich/hanjuku/flows/battle.py:453-462` で
  切り札 flow を解放して `[B]` を返し、`card_damage_list_blocked` を残す。同時に
  `rescue['cards_exhausted'] = True` を立てる。ここが後述の `_critical_safe_card` に効く。

重要な補足として、キャトルミューが常に拒否されるわけではない。`hanjuku_reference.py` では
キャトルミューの boss damage は 90、にせヒーローは `BOSS_GENERALS` かつ兵士 HP 10 であり、
`_card_wears_down_enemy` / `_boss_card_wears_down_enemy`（`judgment/battle.py:766-797`）を通れば
`damage_not_a_kill` で許可される。実際 #5774 の最新 gate は `allowed=true,
role=damage_not_a_kill` だった。`battle_card_damage_rejected` の文言は全拒否共通の固定文言なので、
issue 本文だけでは 04:30:08 の拒否 role が `nonlethal_egg_risk` だったのか、
`damage_or_egg_risk_unclassified` などの一時状態だったのかは確定できない。
この点は owner 分析の完全な裏付けにはならないが、拒否イベントが存在し、その後に一覧 close と
未使用敗北が続いたという因果列自体は否定できない。

### 2. 退却による一覧 close

owner 分析は正しい。現行 main では削除済みだが、#5777 の diff は pre-fix
`flows/battle_menus.py:2140-2160` 付近に次の分岐があったことを示す。

- `side != 'defense'` で `_hero_retreat_needed(cur)` が真、
- card flow が `stage='list'`、
- `_critical_safe_card(..., for_retreat=True)` が偽、
- retreat が unavailable/exhausted ではない、

という条件で `_release_interrupted_card_flow` を呼び、`card_retreat_cancels` を増やし、
`battle_hero_retreat_cancel_card` を記録して `[B]` を返していた。未選択 flow では
`_release_interrupted_card_flow` が `cur['card_flow'] = None` にするだけなので、
「未選択の切り札一覧を閉じ、札は未選択のまま残る」という説明と一致する。

現行 main では `flows/battle_menus.py:2143-2145` にこの分岐を削除した旨のコメントがあり、
`battle_hero_retreat_cancel_card` の active な分岐は存在しない。回帰テストも
`tests/test_hanjuku_card_interrupted_release.py:63-78` と
`tests/test_hanjuku_issue_4958.py:118-128` で「退却でも一覧を閉じない」契約へ更新されている。

### 3. `_critical_safe_card` の安全弁

owner 分析は正しいが、実装は owner の説明よりさらに強いブロックを含む。

- `_critical_safe_card` は現行 main `judgment/battle.py:2603-2639` に残っている。
- `for_retreat=True` では `cards_checked >= 3`、`cards_exhausted`、または critical recheck 後の
  `exhausted` なら最初から False になる（2608-2611 行）。前述の `_card_gate_close` は
  `cards_exhausted=True` を立てるため、一度拒否で閉じた札は、後で gate が通る状態に変わっても
  retreat 保留の対象として再評価されない。これは owner が指摘する「1で拒否された札は安全札ではない」
  結果と一致する。
- さらに即敗北帯 `_retreat_immediate_loss`（`ally_hp <= RETREAT_RESOURCE_FLOOR_HP`、現行 4）では、
  単発致死または必要な回復以外の札を `continue` でスキップする（2628-2638 行）。
  これは owner の指摘する g460 安全弁そのものである。

## #5777 との突き合わせ

#5777 は現在 **merged** である（公開 PR ページ確認）。本検証開始時点の情報では WIP とされていたが、
現行 main に変更が入っている。

### 実際の修正内容

- `judgment/battle.py:960-978` に `CARD_REFUSAL_RETIRED_ROLES = {'nonlethal_egg_risk'}` と
  `_card_refusal_retired` がある。
- 最終実装は「無条件の拒否廃止」ではなく、`ally_hp * 3 <= start_ally_hp` の低 HP 時だけ
  `nonlethal_egg_risk` の拒否を撤回する条件付き実装である。
- PR 本文や依頼文の説明では「ボス戦では拒否を撤回」とあるが、現行の `_card_refusal_retired` には
  boss 判定がない。boss / non-boss を問わず低 HP なら撤回する。これは説明との不一致であり、
  意図的な scope 拡大かどうかはコードだけからは判断できない。
- `flows/battle_menus.py` では、chart card list の最終ゲート（現行 2216-2227 行）と
  survival list の最終ゲート（現行 975-978 行）に `_card_refusal_retired` が入っている。
- pre-fix の `battle_hero_retreat_cancel_card` 分岐は削除されている。

### 本件の 2 段ゲートを塞ぐか

**観測された致命分岐そのものは塞いでいるが、完全に全経路を閉じたとは言えない。**

塞いだ部分:

- 一覧画面へ到達した後は、`_critical_safe_card` が偽でも退却条件で一覧を閉じる分岐がなくなった。
  したがって #5776 の 04:30:27 に記録された `battle_hero_retreat_cancel_card` と同じイベントは
  現行コードでは発生しない。
- 本件のアンディーブは max/start HP 36 で、`ally_hp <= 12` が `_critical_survival_needed` と
  `ally_hp * 3 <= start_ally_hp` の両方に一致する。ボス戦ではこの帯域で `_card_damage_gate` が
  `critical_card_spend` を返しうる（`judgment/battle.py:926-931`）ため、一覧さえ残れば
  キャトルミュー選択へ進む道がある。

残る穴・未検証点:

1. **退却 open 本体は残っている。** `_hero_retreat_open` は現行でも
   `flows/battle.py:1468-1605` にあり、card flow が未選択のままなら
   `_release_interrupted_card_flow` で flow を落とす（1571-1590 行、解放本体は
   `judgment/battle.py:494-506`）。しかも `battle_step` では `_hero_retreat_open` が
   survival reopen より先に評価される（`flows/battle.py:1000-1002` vs 1235-1267）。
   そのため「一覧が既に開いている」状態での cancel close は消えたが、「一覧へ到達する前・再オープン前」
   に退却 open が card flow を中断する隣接経路は残っている。退却メニュー自体は
   `battle_menus.py:704-714` で一律禁止でも、open/中断のコストは残る。
2. **survival list の候補 filtering には `_card_refusal_retired` が効いていない。**
   `_survival_card_list` は現行 `battle_menus.py:953-961` で、まず `gate['allowed']` の札だけを
   `eligible` に入れ、拒否札は即 `_card_gate_refused` して `_card_gate_close` へ進む。
   低 HP だが `_critical_survival_needed`（HP <= 12）に届かない高 HP 将軍では、
   `_card_refusal_retired` の条件（開始 HP の 1/3以下）を満たしても、critical_card_spend が無いため
   survival list では選択へ進めない。chart list の最終ゲートには撤回が効くが、survival 経路では
   実質的に穴が残る。
3. **retire 対象は `nonlethal_egg_risk` だけである。** `damage_or_egg_risk_unclassified` や
   `control_has_no_observed_followup` は低 HP 判定だけでは撤回されない。boss/summon かつ
   HP <= 12 なら `critical_card_spend` がこれらも許可するが、それ以外では拒否が残る。
   04:30:08 の実拒否 role が本番ログなしに確定できないため、#5777 の第1ゲート側の効果範囲は
   完全には検証できない。
4. **実機確認は未実施。** #5777 マージ後の本番で、同じボス戦条件でキャトルミューが実際に
   選択・使用された証拠はこの read-only 検証では得られていない。

## #5774 との関係

#5774 と #5776 は同じ敗北の別観測と判断する。

- 両者とも run `g1373-b4c4126f`、generation 1373、カードはキャトルミュー、戦闘は
  アンディーブ vs にせヒーロー、城はボス、結果は loss。
- #5774 の時刻 `19:30:57Z` は JST `04:30:57` に対応し、#5776 の
  `battle_boss_card_missed` の時刻と一致する。
- #5774 は battle_end 時点の自動 `card_miss` 観測、#5776 はその同じ敗北へ至る途中の
  decision 時系列を owner が手動分析したものである。
- #5774 の `damage gate: allowed=true / damage_not_a_kill` と #5776 の
  `battle_card_damage_rejected` は矛盾しない。`_remember_card_gate` はカードごとの最新評価で
  上書きされるため、早期の拒否が後の許可評価で隠れうる。`damage_not_a_kill` は選択許可の評価であり、
  実際に選択した証拠ではない。

## 最終判定

- 結論: **(a) 実欠陥**
- owner 分析: 2 段ゲートの構造は現行コードと #5777 diffで裏付けられる。行番号の厳密さと、
  キャトルミューが常に拒否されるかという点には補足が必要だが、因果分析の主張は正しい。
- #5777: `battle_hero_retreat_cancel_card` という本件の致命分岐は削除済み。ただし、
  retreat open による pre-list flow 中断、survival list の filtering に残る撤回漏れ、
  retire 対象 role の限定、PR 説明と異なる boss 判定なし、という残件がある。
  実機再確認は未実施のままである。

## 検品ゲート確認（Muse、2026-11-28 09:25 JST）

- 本 issue は owner の手動分析 issue で fingerprint / occurrence マーカーがないため、ハッシュ再計算は不要。
- worktree HEAD は現行 main c032a6a37 と一致することを unit が確認済み。結論 (a) 実欠陥は、owner 分析と #5777 diff・現行コードの突き合わせで裏付けられている。
- #5774 と同じ敗北の別観測である点は、#5774 の検品ゲート結論とも一致する。
