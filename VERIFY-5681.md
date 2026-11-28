# VERIFY-5681 — [半熟英雄] ボスフェーズで月内ボス出撃ゼロ: ch8 ノブナーガ

- 対象: [docich#5681](https://github.com/azumag/docich/issues/5681)
- 判定: `boss_month_zero_sortie` / confidence `high`
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1525-sortie-cards-eligible`
- 月: ch8 `7-9`（965 観測 / 207.6 秒 / max gap 0.7 秒）
- 検証 base: 現行 `main` `305d799eb`（`hanjuku-chart-v1552` 系）
- 検証範囲: read-only。issue 本文、現行/観測版コード、関連 issue/PR の静的照合。本番 VM・ゲーム入力・コード変更・push・PR 作成なし。
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読（AGENTS.md §1）。

## 結論: **(a) 実欠陥**

月内ボス出撃ゼロは実在する停滞で、監査の誤検知ではない。直接の機構は、**ボスフェーズ中に戦闘・月末/月次処理がフィールドと計画実行を占有し、開始した注文が発射まで到達しないまま、月後 5 分の回復窓でも月次イベント列が終わらず出撃へ復帰できなかった**ことである。少なくとも issue 証拠の範囲で、#5675 の owned-target ループや #5137 の月末開始フェーズ誤発火とは別型であり、現行 main でも同じ占有―復帰不能の構造は残る。

同一 fingerprint の先行 issue #5155（ch8 ノブナーガ、2026-11-22）も read-only 検証で **(a) 実欠陥**と判定され、「月次処理・着弾戦闘・野営/将軍ジャンプ系がフィールドを占有し、`order_dispatch_starved` がカスケード上位 owner に遮られる」構造が指摘済みである。#5681 は占有の内訳が野営中心ではなく戦闘・月次イベント中心で、`order_start` が 3 件ある点は異なるが、ボス発射へ到らず翌 5 分も回復しない結果構造は同じで、修正済みとはいえない再観測である。

## fingerprint / occurrence の再計算

issue 証拠の値から式どおり再計算した。どちらも一致した。

| 項目 | 入力 | sha256 前方一致 | issue の値 | 判定 |
| --- | --- | --- | --- | --- |
| fingerprint | `8\|boss_month_zero_sortie\|boss_month_zero_sortie` | `b74a8e86236758d9` | `b74a8e86236758d9` | **一致** |
| occurrence / incident_id | `g1190-ch1v9\|1190\|8\|7-9` | `8be27640b6ffbbfb6339` | `8be27640b6ffbbfb6339` | **一致** |

根拠コード: 現行 `src/docich/hanjuku/month_audit.py` L3383-L3390（`_incident_id` / `_fingerprint`）、L3489-L3493（`boss_month_zero_sortie` の primary signal を使った record への設定）。

## 監査発火の正当性

- `_boss_departures`（`month_audit.py` L3204-L3224）は `LAUNCH_DECISIONS` のうち `purpose == 'boss'` または boss site target のみを数え、`purpose == 'move'` を除く。本件の月内一般出撃 2 はボス target/purpose を持たないため、一般出撃の存在と「月内ボス出撃 0」は矛盾しない。
- ボスフェーズ開始 `1795677828.327` は月初 `1795677995.138` より約 166.8 秒早い。月内フェーズ時間はほぼ月全体（207.6 秒）で 965 観測あり、`MIN_BOSS_PHASE_SECONDS=90` / `MIN_BOSS_PHASE_OBSERVATIONS=20` を大きく超える。
- したがって #5137 / v1551 が追加した `_boss_month_coverage` による `short_boss_phase` 格下げ（`month_audit.py` L3872-L3909, L3968-L3978）は本件に適用されない。現行 main で同じ入力なら `phase_sufficient=True` となり、`boss_month_zero_sortie` high のままである。
- 監査回帰テスト: `tests/test_hanjuku_month_audit.py` L1729-L1751（十分な月内フェーズでは high の `boss_month_zero_sortie`）、L1782-L1813（月末開始・境界初見のみ low/unverified）。

## 機構の切り分け

### 1. 月内は戦闘と月次/月末処理が決定列の大部分を占有した

issue 証拠の月内 `battles.decisions` は 134 で、決定総数 351 の約 38% である。さらに `month_sub_step` 29、`post_month_status_close_probe` 6、`post_month_roster_marker_mapped` 6、その他 `post_month_*`・`month_*` が並ぶ。月末近くの決定表も、最後は `field_encounter_observed` → `battle_start` → 複数の `battle_melee` → `battle_result` で埋まっている。

この間の出撃系は `order_start` 3 / `order_launched` 0 であり、少なくとも注文選択には 3 回到達したが、発射確定まで完走しなかった。`sortie_launch_confirmed` 2 / `sortie_launch_unverified` 2 はボス以外の出撃または観測側の発射証拠であり、ボス向け `LAUNCH_DECISIONS` は 0 のままである。

### 2. 翌 5 分の回復窓は月次イベント列に占有され、出撃へ戻れなかった

`after.complete=true` の 300 秒窓で 194 決定・出撃 0・回復 no。内訳は `event_text_advance` 44、`month_sub_step` 29、`post_month_*` 群、`no_input_fallback` 10 で、直近行は同一文面の会話進行と yes/no/text の no-input fallback が約 20 秒周期で続いている。`plan_starved_rearm` は 2 回記録されるが、これは戦闘/月次中の再計画を促すだけで、フィールド map owner を取り戻して `order_start` へ進める保証ではない。実際に after 窓では `order_start` も発射も 0 だった。

現行コードでもこの非対称は残る:

- `orchestrator.py` L642-L754 `plan_starved_rearm`: battle/menu 中に再計画するが、`PLAN_STARVE_TICKS=900`、live march があると返り、再計画後の発射は別途 map/deploy が必要。
- `orchestrator.py` L649-L699: map が foreground に戻ったときだけ mechanical plan の先頭 pending step を active 化する。月次 text/yes_no が占有している間はこの経路に入らない。
- `flows/map.py` L2613-L2682 付近: `order_start` は map/deploy 側へ到達した時点で記録される。after 窓に `order_start` がないことは、再計画の有無に関わらず発射パイプラインへ復帰していないことを示す。

### 3. 行軍・戦闘の長期化は副次的な占有・兵力拘束因だが、ボス出撃ゼロの直接証拠ではない

月末の行軍は `A:8db4d2de:M498cfd:en_route(760t)` と `I:da59c2:7e56fdfa:en_route(771t)` が live で、`arrived` の古い receipt も複数残る。現行 `_signals` は `arrived` を live 行軍として扱わない（`month_audit.py` L3302-L3312）ため、本件の signals に `stuck_march` はない。`battle_grind` は battle 系決定数の閾値を満たす正しいシグナルだが、それだけでは因果確定ではない。因果として強いのは、月内末と after 窓の両方で月次/戦闘 owner が継続し、発射へ復帰しなかった時系列である。

### 4. #5675 との異同

#5675（結論 b、コメント `7184010693`）は `A:b2b5a37d:M4983c0` が already-owned target を攻撃し続け、`plan_map_activate` → `sortie_route_unverified` → pending 戻しを繰り返して計画再構築を飢餓させる型だった。v1529 / PR #5645 は `_settle_owned_target` で done へ閉じ、`plan_map_activate` / `plan_starved_rearm` / `_plan_pending.live()` に owned-target 述語を入れてカバーした。

#5681 では `sortie_route_unverified` も `plan_map_activate` の繰り返しも観測されていない。`sortie_held_source_defender` は 1 件だけで、6 回連続の owned-target cancel ループではない。また boss target は owned-target 判定の対象外で、ボス発射ゼロを直接説明しない。したがって #5675 の修正ではカバーされない。

### 5. #5137 との異同

#5137 / v1551 は「月末に始まったばかりのボスフェーズで出撃機会がなかった」誤発火を `_boss_month_coverage` で low/unverified へ落とす監査修正である。本件はフェーズが月初前から継続し、月内 207.6 秒・965 観測の十分な機会があったため、この修正の対象外である。現行 main でも同じ月は high のまま起票される。

### 6. その他の後続修正

- v1530 / PR #5645 系の bounded reopen は、実測終了した `launched_unconfirmed` のゾンビ化を止める。本件でも古い arrived receipt はあるが、live な en_route は 760/771 tick で、ボス向け発射ゼロの主証拠ではない。
- v1546 / PR #5670 は camp recall の input receipt 証明枯渇を bounded stale wait へ落とす。本件 after 窓の主占有は recall ではなく `event_text_advance` / `month_sub_step` / post-month sequence であり、カバーしない。
- ch11 gateway 側の boss wave 修正（#5673系）は ch8 ノブナーガの source/target 制約と無関係である。

## 現行 main での再現可否

同一の観測列を現行 main に入力した場合、監査分類は再現すると判定する。理由は次の通り。

1. `_boss_month_coverage` の除外条件を満たさず、`boss_month_zero_sortie` high のまま。
2. `plan_starved_rearm` は依然として再計画の保全であって、月次 text/yes_no owner を強制的に中断して map/deploy へ返す機構ではない。
3. `order_start` 済み注文が月次/戦闘割り込みで発射まで完走しない形は、`order_dispatch_starved`（pending 注文の dispatch 待ち）では直接計測されない。
4. #5675 の owned-target close、v1530 の zombie launched_unconfirmed reopen、v1546 の recall receipt bound はいずれも本件の支配的な after 窓の占有列を解消しない。

したがって「現行 main でも同じ外部条件（月末イベント列が長引き、戦闘が連続し、boss phase の月）なら再発し得る」。ただし本検証は read-only であり、実ランの `hanjuku_decisions.jsonl` 全時系列を取得していないため、`order_start` 3 件の個別 step とどの画面で発射が途切れたかの確定までは行わない。

## 根拠コードと関連テスト

- `src/docich/hanjuku/month_audit.py` L115-L122: boss 出撃として数える `LAUNCH_DECISIONS`。
- 同 L3185-L3224: `_departures` / `_boss_departures`、move 除外と boss target/purpose 判定。
- 同 L3266-L3322: `monthly_scan_hold` / `battle_grind` の signal 判定。
- 同 L3872-L3909, L3968-L3978: `_boss_month_coverage` と short phase downgrade。
- 同 L3985-L3993: boss phase 中の boss departure 0 を `boss_month_zero_sortie` / unverified へ分類。
- `src/docich/hanjuku/orchestrator.py` L639-L754: `PLAN_STARVE_TICKS` / `plan_starved_rearm` と live march guard。
- 同 L649-L699: map foreground で mechanical boss plan の先頭 pending step を active 化する経路。
- `src/docich/hanjuku/strategy/replan.py` L1214-L1238: boss open 時に boss を targets 先頭へ置く owner ルール。
- 同 L1268-L1433: `_boss_wave_pick` の source/general 条件。
- `src/docich/hanjuku/flows/deploy.py` L1928-L1934: owned-target cancel を `_settle_owned_target` で閉じる #5675 側修正。
- `tests/test_hanjuku_month_audit.py` L1729-L1751: 十分な boss phase 月は high で起票する回帰。
- 同 L1782-L1813: 月末開始/境界初見の boss phase は unverified へ落とす #5137 系回帰。
- `tests/test_hanjuku_mechanical_boss_plan.py` L75-L100 付近: `plan_starved_rearm` が map/field のみ activate し、event では activate しない仕様。
- `tests/test_hanjuku_plan_starve_rearm.py`: battle/menu 中の再計画と live guard の回帰。

## 修正方向（実装時の確定版）

本検証で残った構造は「ボスフェーズの pending 注文があっても、月次処理・戦闘・復帰系 owner が dispatch 判定そのものより先に画面を取り続ける」こと、および「読めない募集費 yes/no が fail-closed のまま事実上無期限に monthly owner を保持する」ことである。実修正では次を共通対応とし、#5155 の general jump/recall 優先型と #5681 の月次イベント占有型の両方をカバーする。

1. `order_dispatch_starved` を orchestrator で foreground owner より先に毎観測評価し、月次/戦闘中に判定自体が止まらないようにする。
2. boss-only phase で starved の間、post-month roster/repair scan と新規 general jump は次の free map を出撃計画へ明け渡す。
3. 読めない募集費支払 yes/no は blind confirm せず、12 観測で bounded abort して月次占有を終える。

これは boss order の生成条件を緩める修正ではない。source/egg/HP 不備で `next_order` が開始不能と判断した場合は従来どおり starvation が reset され、月次処理や探索へ戻る。

## 残件（結論を変えない限定）

- issue 本文の JSON は rendering 上限 24000 字で `world.launched_orders` 途中で切れている。`order_start` 3 件の step/時刻と、発射直前の画面単位の中断点を確定するには runtime `g1190-ch1v9` の `hanjuku_decisions.jsonl` / previous JSONL が必要である。
- ただし after 窓 300 秒に発射 0 で、月次イベント列が継続している事実、boss phase が月全体を覆う事実、fingerprint の同型再発（#5155）だけでも、(b) 既存修正済み・(c) 誤検知・(d) 判定不能とはしない。

## 検証メモ

- 実施: issue #5681/#5675/#5155 照合、fingerprint/occurrence の Python sha256 再計算、v1525 と現行 main の `month_audit.py` / `orders.py` / `replan.py` / `orchestrator.py` / `deploy.py` 静的照合、関連テスト確認。
- ドキュメントのみのローカル成果物のためテストは実行しない。
- 秘密情報・実値トークンは含めない。数値は issue に既出の合成/要約値のみ。
