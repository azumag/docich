# VERIFY-5397 — [半熟英雄] ボスフェーズでボス戦闘ゼロ: ch4 四季王

## 対象

- Issue: docich#5397 / fingerprint `a9cc3ee6ba8390a6` / occurrence `f147860d6f67f7468a91`
- 判定 `boss_phase_no_battle` (confidence high) / 月 `ch4 7-5`
- ラン `g1342-1fe0734c` / `hanjuku-chart-v1460-password-entered-field`（v1460）
- ボス: 四季王 / 対象: 四季王城(北東), (北西), (南東), (南西)
- 出撃: 確定 0 / 発射 0 / `order_start` 7 / `order_launched` 0 / 観測 658回 / 965.5秒
- ボスフェーズ継続 600.7秒 / 観測 417回 / ボス戦闘 0 / `battle_start` 全体 4 / ボスHP観測 False
- 検証 base: worktree HEAD `91f7f001d`（v1460 + #5393 verify）。`git fetch origin main` は
  環境で拒否され、追記の 190539e8（v1460→v1461, PR #5401）はローカル参照できない。
  PR #5401 は敵召喚獣ウゴカザルの戦闘中処理（白兵）の変更で、本件の出撃 dispatch／
  companion ゲート／監査パスとは非交差のため、base 差異は本結論に影響しない。

## 結論: (a) 実欠陥 — 計画層 `_ready` の四季王サイト companion 待ちが無記録・無制限

検出は正しい（誤検知ではない）: ボスフェーズでボス出撃 0・ボス戦闘 0 が実測どおり。
停滞の機構は #5275/#5276/#5307 と同じ「companion 待ち」系で、**残存する発火層は
`orders.py:816-822` の `_ready` ゲート** — #5310/#5308 の共有締切から四季王サイト
注文だけが明示的に除外されており、VERIFY-5307 が「残存する非カバーは ch4 召喚ボス
site のみ」と記録した未修復領域そのもの。

## 根拠となるコード箇所（現行 base `91f7f001d`）

### 1. 監査側（検出の正当性）

- `src/docich/hanjuku/month_audit.py:507-541` `_boss_phase`: ch4 は
  `chart.boss_sites(4)`（= `hanjuku_map_sources.SOURCE_BOSS_SITES[4]`、四季王城4地点）
  を残存ボス対象とし、通常城が全て自軍なら位相成立。件の観測と一致。
- 同 `:2590-2650` `_track_boss`: フェーズ開始から `BOSS_NO_BATTLE_SECONDS=600`
  （`:51`）経過かつ `_boss_battle_seen`（`:544-579`）/ `_rows_boss_battle`
  （`:582-603`）が空なら incident を1件発火。**継続 600.7秒は初回発火時刻であり、
  フェーズ全長ではない**（発火後もフェーズは継続する）。
- 発火は実測ベースで正当: 発射 0 なのでサイトへの行軍自体が存在せず、
  `boss_victories` / `boss_hp_*` / `sortie_history` / `sortie_outcome` の
  いずれにも証拠が残り得ない。
- 副次の監査ギャップ（本件の判定を左右しない）: `boss_names` が
  `SEASON_KINGS`（`hanjuku_map_sources.py:119` の スプリミョーネ/サマカンテ/
  フォーリシア/ウイナッツォ）を含まないため、サイト戦の `battle_start` は
  名称一致では拾えない（`:587, 624-626`）。`boss_victories` ・ `sortie_outcome`
  の経路で補完はされるが、進行中のサイト戦のみ `battle_start` が出たケースは
  盲点。加えて `boss_cell_missing` / `boss_pursuit_idle` は ch4 では構造的に
  常時発火する（`boss_cell` は `world.py:706` で ch4 を除外、`boss_pursuit` は
  ch9 専用）ため ch4 のシグナル列はノイズを含む。

### 2. `order_start 7` に対し発射 0 — 発火層の特定

- `order_start` は注文が active 化した時点で `flows/map.py:2664` が記録、
  `order_launched` は `map.py:2956` `_commit_departure` → `orders.py:2502`
  `_finish_order('launched')`（実記録は `:2521` の `'order_' + state`）のみが出す。7 回開始して 0 発射は、
  「開始後に pending へ戻る」が繰り返されたことを意味する。
- **計画層 `orders.py:816-822`**: `_ready` は `_is_boss_order` かつ
  `companion_source is None` かつ出撃元名簿既知で `return False` するが、
  `order.get('target') in chart.boss_sites(4)` が先に真になるため、
  `_boss_companion_source_wait_expired`（`:743-770`, 200 tick）に**到達しない**。
  短絡評価でタイマー自体 arm されず、`boss_companion_source_timeout` も
  一切記録されない — 無記録・無制限の待機。
- 同 `:823-832` の `_broken_hero_order`/`_boss_egg_depleted` ゲートの締切解放も
  `order.get('target') not in chart.boss_sites(4)` を要求し、サイト注文を除外。
- **deploy 層は有界化済み**: `deploy.py:2393-2405`（general_list 段の
  `boss_companion_required`）と `:2513-2519`（card_select/sortie_confirm 段）は
  共有 `_boss_companion_wait_expired`（200 tick）で有界。上限後は実一覧の
  主人公確認による単独 fallback、または `_finish_order('failed')`。
  ただし `_ready` が落ち続けるとこの有界段に到達しない。
- `order_start` が 7 回発生し得た経路: `_boss_companion_source`（`orders.py:170-195`）
  の home 未読ショートカット（`:190-193`）と `src/docich/hanjuku_policy.py:126-165`
  `boss_roster_reprobe`（停滞 site step 検出で本城名簿を 100 tick 間隔で破棄→再読）
  が「名簿未読」を作ると、`_ready` の「出撃元名簿既知」条件をすり抜けて注文が
  active 化 → deploy が実一覧を読み、卵持ち代役不在なら
  `boss_companion_required` で pending へ戻る → サイクル反復。
  この「reprobe 駆動の start→hold→pending」で dispatch 系の記録は動き続けるが
  発射には至らない、という観測形態と整合する。
- 別の成立しうる寄与: `battle_start` 4件の戦闘割り込みは実行中の出撃試行を
  pending へ戻して `order_start` を増やす。`monster_skill_stall`
  （`battle_menus.py:2902-2983`）は 1 戦闘あたり概ね有界だが、`return_risk`
  （`judgment/battle.py:1588-1599`、負傷将軍を召喚獣が遮蔽中）は選択維持が
  無制限であり、敵も召喚獣を倒せない組み合わせでは戦闘自体が終了しない
  可能性を残す — フェーズ時間を食う副次的機構として記録する。
- `order_dispatch_starved`（`orders.py:1879-1924`）は計測どおり動作する機構:
  pending 注文が 60 tick dispatch されないと1エピソード1回記録し、ボスフェーズ
  では繰り返し前景ワークを1 frame 譲る。`boss_only_phase`（`:1842-1860`）は
  `boss_sites(chapter)` 経由で ch4 も対象。滞留の可視化であり、滞留の原因ではない。

### 3. 四季王城×4 の gateway / `_boss_wave_pick` カバー範囲

- `gateway`: `hanjuku_chart.py:1285-1297` の GATEWAYS は第11話のみ。**ch4 非該当**。
- `_boss_wave_pick`（`replan.py:1169-1317`）: ボス波の機械的編成は `targets` に
  `chart.is_boss_castle` が載ったときだけ呼ばれるが、interim 候補は
  `replan.py:1096-1097` で `not chart.is_boss_castle(c)` を除外し、
  `boss_open`（`:1131-1139`）は `chart.boss_castle(chapter)`（ch4 では `'ボス'`、
  `BOSS_CASTLES[4]`）が城表に存在することを要求 — `APPROX_BOSS_CELLS` は
  ch4 を skip（`:468-469`）するため `'ボス' ∉ CASTLES[4]` で**常に False**。
  よって `_boss_wave_pick` は ch4 では一度も起動しない。波状攻撃の対象外は
  設計どおりだが、その分、サイト攻略はすべてチャート注文系に委ねられる。
- サイトの実際のカバー経路: チャート注文 `4-B1..4-B4`
  （`hanjuku_chart.py:700-715`、`after:'all_captured'`）+ `_season_site_ready` /
  `_season_site_held` / `_site_king_down`（`orders.py:478-482, 691-701, 1031-1047`）
  + `_boss_companion_source` のサイト枝（`orders.py:176-195`、卵持ち代役ゲート）
  + `boss_roster_reprobe`（`src/docich/hanjuku_policy.py:126-165`）+ `_rearm_phantom_site_steps`
  （`orders.py:1693-1726`）+ `_stale_boss_roster_probe`/`_boss_source_staffing_needed`
  （`replan.py:2042-2093`、サイトを明示対象に含む）+ `_mechanical_boss_plan` の
  `site_bosses` 枝（`replan.py:2455-2548`）。入口は揃っているが、合流する
  `_ready` の companion ゲートが無制限なため、卵持ち代役が全名簿から欠ける間は
  終了条件のない待機になる。

### 4. 同型過去 issue との異同

- **#5275**（ch9、`boss_month_zero_sortie`）・**#5276**（ch9、`boss_phase_no_battle`）:
  deploy 層 `general_list` 段の `boss_companion_required` 無制限保留 → (a) 実欠陥。
  #5279（map_target 段のみ有界化）では観測経路をカバーせず。本件は**一段前の
  計画層**で、ボス注文が deploy に到達すらしない型。
- **#5307**（ch10、`boss_month_zero_sortie`）: `_ready` 層 companion ゲート →
  (b) #5310（v1443）でカバー済み。ただし同検証が明記したとおり #5310 の
  「残存する非カバーは ch4 召喚ボス site のみ」であり、**本件はその残存領域の
  初観測**と読める。#5307 は `order_start` がボス注文に無かった型、本件は
  reprobe/未読すり抜け経由で `order_start` が出る型で、同一ゲートの別観測形。
- **#5342・#5372**: gh が環境で起動拒否され本文を取得できず、worktree 内にも
  VERIFY 文書・参照コミットが無い（`git log --grep` ヒットなし）。fingerprint は
  `chapter|kind|signals` の sha なので、それらが ch4 なら本件と同 fingerprint
  `a9cc3ee6ba8390a6`（= 同一 issue の再発コメントになる設計）、別章なら
  機構が章依存で異なる可能性がある。本文未取得のため異同は未確定と記録。
- 本件との対応関係: いずれの同型 issue でも「無制限の companion 待ち」が共通原因。
  サイト注文は卵持ち要件（owner 2026-10-30 一連の指示）で意図的に厳しくしているが、
  締切も記録も終了条件も無い待機は「定常的にボス城へ進行」「弱い将軍をたくさん
  送って少しずつ削る選択肢も狭めない」という owner 意図とも整合しない。

## 判定詳細

- **(a) 実欠陥で確定**: ボスフェーズ 600 秒超でボス出撃・ボス戦闘とも 0 は
  進行停止。原因層は `_ready` の四季王 companion ゲート（`orders.py:816-822`）で、
  無記録・無制限・タイマー未 arm の三点が既判例（#5275/#5276/#5307）と同型の
  欠陥条件を満たす。
- **(b) ではない**: #5279（map_target のみ）・#5310（サイト除外の共有締切）・
  #5308 はいずれも本件の発火層をカバーしない。deploy 層の有界化は到達しないと
  無意味で、残存欠陥が現存する。
- **(c) ではない**: 検出値（出撃 0 / ボス戦闘 0）は計器どおり実測と一致。
- **(d) 留保**: issue 本文の決定内訳（どの step が `order_start` したか、
  `boss_companion_required` / `order_dispatch_starved` / `monster_skill_stall`
  の出現数、`world.garrison` の卵持ち状況）を gh 不可で直接照合できていない。
  ただし取り得る全経路が「companion 系の無制限待機」か「有界だが集計的に
  無制限な start→hold 反復」のいずれかに帰着するため、判定は変わらない。

## 修正の方向（実装はしない）

- `orders.py:816-822`: サイト注文の短絡を外し、`_boss_companion_source_wait_expired`
  を適用する。締切後は既存の有界 deploy 経路（実一覧評価→代役/主人公 fallback/
  bounded fail）へ進ませる。owner がサイトに課した卵持ち要件は deploy 側の評価で
  維持されるため、`_ready` での永久ブロックだけを解消する改修で済む。
  併せて `:829-831` のサイト除外も同締切で解放するか検討。
- 待機の可視化: サイト注文が `_ready` でブロックされるとき `order_dispatch_starved`
  とは別に、どのゲートが効いているかの1回/エピソード記録を入れる
  （現状は無記録で監査の決定内訳から原因が読めない）。
- 全名簿が卵なしで持続する場合の終了条件（上限後に site 注文を bounded fail →
  卵回復/人員移動の interim 注文へ回す等）は owner 確認が要る設計変更。
- 監査側の副次改善: `_rows_boss_battle`/`boss_battles` の敵名集合に
  `SEASON_KINGS` を含める（進行中のサイト戦を拾う）、および ch4 では
  `boss_cell_missing`/`boss_pursuit_idle` をシグナルから外す。
- テストの方向性: `tests/test_hanjuku_boss_dispatch_starved.py`・
  `test_hanjuku_ch9_boss_companion_timeout` 系に倣い、「ch4・通常城全所有・
  既知名簿に適格卵持ちなし」で `_ready` が締切後に deploy へ進むこと、
  deploy 側が有界に失敗/fallback することを固定する回帰テスト。

## 手順上の注記

- read-only 検証。VERIFY-5397.md の作成以外の編集・本番 VM 操作・push/PR なし。
- `gh` は環境で起動拒否のため issue 本文・#5342/#5372 本文は未取得。
  issue の要点は起票メモ（prompt-5397.txt）の数値を根拠とした。
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）。
- テスト未実施（read-only のため）。コード照合のみで結論した。
