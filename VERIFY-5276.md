# VERIFY-5276 — [半熟英雄] ボスフェーズでボス戦闘ゼロ: ch9 ハデス

- 対象: azumag/docich issue #5276 / fingerprint `4c1d0ae56bf9ad73` / occurrence `0130dd46a4b46323e5c1`
- ラン: `g1190-ch1v9` / generation 1190 / `hanjuku-chart-v1426-ch9-oshiroga-up`（v1426 = `294b60ae3`、main の祖先）
- 検証範囲: read-only（本ファイルの作成以外の編集なし）
- 現行 main: `c3cf80fda`（v1432）。v1426→HEAD で `src/docich/hanjuku/flows/deploy.py` の差分なし（`git log 294b60ae3..HEAD`）。関連差分は `flows/map.py`（PR #5279）のみ。

## 結論: (a) 実欠陥 — #5275 と同一機構。PR #5279（v1431）は観測された保留経路をカバーしない

ボス注文 `A:3814542a:M381c6a`（attack イデ！→ボス）がフェーズ全 600.1 秒
`pending` のままだったのは、「実一覧で十分な代役を確認できない」場合の
`boss_companion_required` 保留が上限を持たないため。issue の記録上の発火箇所は
出撃将軍一覧（`general_list`）段の `deploy.py:2381` であり、#5279 が上限を
追加したのは目的地選択（`map_target`）段の `map.py:2809-2815` だけ。
出撃 UI の画面順序は `general_list → card_select → sortie_confirm → map_target`
で、`general_list` 段の保留では `target_step` に到達しないため、新設の
`BOSS_COMPANION_WAIT_TICKS=200` タイムアウトはこの経路では発火しない。
同症状は v1432 でも再発しうる。

## 根拠となるコード箇所（ファイル・行、main @ c3cf80fda）

### 1. 発射 10・order_start 11 なのに battle_start 0 の経路（検証観点1）

出撃 UI の画面順序（`docs/operations/hanjuku-fort-camp-recall-20261101.md:15` と
#5275 の決定列 `cursor_order_pick → card_pick → sortie_confirm → y_jump_* →
sortie_departure_requested` で確認）:

```
castle_menu → しゅつげき → general_list（本人選択）→ card_select → sortie_confirm → map_target（目的地＋出発要求）→ 発射
```

- `src/docich/hanjuku/flows/deploy.py:2307-2383` — `general_list` 段。一覧スキャン後、`_cursor_order_pick` は `_is_boss_order` で除外（:1169）するためボス注文はカーソル即出しを通らず、:2377-2383 で実一覧の代役候補を評価する。候補 0 なら **:2381** が `boss_companion_required`（理由: 「十分な能力の代役を実一覧で確認できないため主人公を出撃させず準備を待つ」）で無条件保留 → `_hold_guarded_sortie`（:1767-1784）は `orders[step]='pending'` + `[b,b]` で手戻りする。上限・記録済み経路の逃げはない。#5275 の決定 JSON はこの理由文と `screen_kind: general_list` が一字一致するため、発火箇所は :2381 と確定できる。
- 代役フェリー: 計画表の `I:c9:イソ>イデ！:1-6`（en_route 179-722t）と `I:c9:カラ>イソ:*` は代役を出撃元へ運ぶ interim 移動。期限超過・failed・発射未確認を繰り返し、代役は一度も イデ！へ到着していない（`sortie_overdue_retired` 19 / `sortie_unverified_rearmed` 18 / `sortie_location_recheck_queued` 22）。
- 「発射 10 / order_start 11 / order_launched 8」はほぼこのフェリー・配置出撃で、`month_audit._departures`（:2798-2814）が `purpose=='move'` を除いても launch_events=22 が残るのは攻撃系の再試行を含むため。確定 0 は ▲ マーカー不検出による成立確認の失敗（`sortie_launch_unconfirmed_kept` / `sortie_launch_unverified`）で、#5154 系の確認ギャップ。
- 「出撃元の将軍がいない」シグネチャは末期状態と一致: フェーズ末の在城は イデ！0 / イソ0 / サヌト のみプラリネ（issue 世界状態）。
- `battle_start 全体: 0` は誤記: decisions.before に 19:54:54Z の月一イベント一騎打ち `battle_start` が1件あるが、`month_audit._decision_counts`（:2843-2846）が `most_common(30)` に切り詰めるため、全 729 決定中 1 件の `battle_start` は `counts` から脱落し `_build_boss_incident` の `battle_starts` が 0 と表示される（:614 付近）。監査側の副次的な集計欠陥。`boss_battles` は rows 直接走査（:612-616）で、一騎打ちの敵はボス将軍名でないため 0 は正しい。

### 2. #5275（boss_month_zero_sortie、同ラン、ch9 12-12）との異同（検証観点2）

- **同一機構。** 同一ラン・同一ボス注文 `A:3814542a:M381c6a` が代役待ちで pending のまま、代役フェリーが不成立。#5275 の決定に `boss_companion_required`（イデ！→ボス）が実際に連発されている。
- 監査種別が違う: #5275 は月次種別 `boss_month_zero_sortie`（12-12、観測 177、月内ボス出撃 0）、#5276 はフェーズ集約 `boss_phase_no_battle`（フェーズ全体 600.1s・観測 1874、13-7 締め時点でボス戦闘 0）。
- 状態の進行差: #5275 時点は イデ！在城 4（ただし全員不適格: 卵切れ・負傷等で代役候補 0）で確定 3。#5276 は駐留が枯渇（イデ！0・イソ0）し確定 0、シグネチャに `no_source`・`launch_confirmation_missing` が追加。共通原因に、確認不能・行軍滞留が時間経過で悪化した末端状態。

### 3. PR #5279（v1431、merge 7b6539872）のカバー範囲（検証観点3）

- 実差分は `flows/map.py` のみ: `BOSS_COMPANION_WAIT_TICKS = 200` と `_boss_companion_wait_expired()`（map.py:2749-2774）、呼出しは `target_step` 内の主人公ガード 1 箇所（map.py:2809-2815、「主人公のボス目標」＝ `_sortie_general(order, mem) == NAME` のとき）。
- しかし観測経路は **general_list 段（deploy.py:2381）で無条件保留のまま**。general_list は map_target より前段なので、この経路では `target_step` に到達せずタイムアウトは発火しない。
- 仮に主人公が一覧で選ばれた場合も `card_select`/`sortie_confirm` 段の同型ガード deploy.py:2477-2481（理由: 「主人公のボス出撃を取り消し、十分な戦力の代役を待つ」）が無条件のまま残り、ここも map_target より前段。
- 計画層 `src/docich/hanjuku/strategy/orders.py:734-747`: corridor 章は出撃元在城が 2 未満で抑制、加えて `_boss_companion_source(order, mem) is None` かつ出撃元在城が既知なら注文を ready にしない。フェーズ末期（イデ！在城 `[]` = 既知・全 owned で適格代役なし）では active 化すら永久に起きない。この層にも上限はない。
- したがって本件症状は **カバーされない**。残存欠陥: 「実一覧で代役を確認できない」無限保留が deploy.py:2381・deploy.py:2479・orders.py:745 の 3 層に残る。#5279 が効くのは `_sortie_general == NAME` のまま map_target に到達する経路のみ（観測経路ではない）。

### 4. `boss_companion_required` と月内ボス出撃ゼロの関係（検証観点4）

確認済み。#5275 で `boss_companion_required`（イデ！→ボス、general_list）が連発しボス注文は月を跨いで pending。#5276 では同注文がフェーズ終了まで pending で、`boss_phase.battle_count/battle_starts` が 0。代役待ち → ボス出撃ゼロ → ボス戦闘ゼロの因果は記録どおり。

## 判定詳細

- **(a) 実欠陥**で確定。ボスフェーズ 600 秒にボス出撃ゼロ・ボス戦闘ゼロは進行停止であり、原因は代役待ちの無限保留 + 代役フェリー不成立。
- **(b) ではない**: 既存修正（#5279）は観測された保留経路（general_list 段）をカバーしない。残存欠陥として、同型の無条件保留が deploy.py:2381・deploy.py:2479・orders.py:745 に残る。
- 副次（監査側）: `battle_start 全体:0` は `most_common(30)` 切り詰めによる誤記（実値 ≥1、月一イベント一騎打ち）。判定自体は `boss_battles` の直接走査で正しい。

## 修正の方向（実装はしない）

- `deploy.py:2381`・`deploy.py:2479` に `_boss_companion_wait_expired` と同じ step 単位の待機上限を適用し、上限到達で主人公単独出撃へ倒す（#5279 と同じ方針の横展開）。
- `orders.py:745-747` の ready ゲートにも同等の上限（または companion source 不在時の主人公許可）を検討。
- 監査側: `battle_starts` を切り詰めた `counts` でなく全行から数える（`sum(1 for row in rows if decision=='battle_start')`）と誤記が消える。

## 手順上の注記

- read-only 検証。VERIFY-5276.md の作成以外のファイル編集・本番 VM 操作なし。
- `agent-guards/secret_scan.py` は本 box 未配置のため、投稿物（PR 本文・issue コメント）は同等のローカル秘密パターンスキャンで clean を確認してから投稿する（AGENTS.md §3.1 フォールバック）。
