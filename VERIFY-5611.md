# VERIFY-5611 — [半熟英雄] 月内出撃ゼロ: ch1 月次処理がマップを占有

- 対象: azumag/docich#5611。kind `zero_sortie` / signature `monthly_scan` / confidence `high`。
- 観測: ラン `g1190-ch1v9` / generation 1190 / `hanjuku-chart-v1514-ch4-site-orders-steady`、ch1 1-6（観測 626 回 / 125.7 秒 / max gap 0.7 秒）。
- 出撃: 確定 0 / 発射 0 / `order_start` 0 / `order_launched` 0。翌5分も departure 0 / launch event 0 / `recovered: false`。
- 検証 base: 現行 main `212c24314275739693c8968afa18afe9b9fbd736`（`hanjuku-chart-v1573-recall-preempt`）。
- 検証範囲: read-only。本番 SSH・VM 操作・ゲーム入力なし。run 本体の `hanjuku_decisions.jsonl` / frames にはアクセスせず、issue 内包 JSON・関連 issue/PR・現行 main のコードを照合した。
- `DOCICH_HANDOFF_PATH` 未設定のため handoff 正本は未読と記録する（AGENTS.md §1）。

## 結論: **(b) 設計どおり — 出撃可能な注文がなく、月次処理・防衛戦が優先された。既存の zero_sortie 修正で追加対応は要らない**

監査の「月内出撃ゼロ」自体は正しい測定だが、占有の下位には #5675/#5719 のような実行可能な出撃を阻害するループがない。月末時点で全保有城の在城が1将軍だけで、ch1 は guard-free 章ではないため、現行仕様では通常の attack/move はどの城からも開始できない。つまり月次処理や戦闘が「実行できたはずの出撃」を阻害したのではなく、そもそも dispatch 可能な注文がない状態で、月次の編成・募集処理と防衛戦が優先された。

## 1. ハッシュと監査値の照合

`src/docich/hanjuku/month_audit.py` の式 `_fingerprint(chapter, kind, [primary])` / `_incident_id(identity, chapter, month)`（L3423-L3430）から再計算し、issue 本文と一致した。

- fingerprint: `sha256("1|zero_sortie|monthly_scan")[:16]` = `f29ac36b2bedefde`
- occurrence: `sha256("g1190-ch1v9|1190|1|1-6")[:20]` = `373337d296496fb10d41`

`zero_sortie` の起票条件（清浄な月開始・confidence high・departure/confirmed 0）も issue 内包値と一致する。したがって出撃ゼロの測定は誤検知ではない。

## 2. 出撃不能だった直接根拠

issue 内包 JSON の世界状態:

- 保有: アルマムーン、ナキューメラ、キカンドン、ジョンリギ。
- 在城: アルマムーン `どうし` 1人 / キカンドン `ゼウス` 1人 / ジョンリギ `ココット` 1人 / ナキューメラ `ヴィーナス` 1人。
- `world.sorties` は空、`launched_orders` も空。行軍中・発射未確認の例外対象はない。
- `plan.active` は null、`chart_plan.orders` は空。旧計画の status には pending が3件残るが、詳細な target/source が投影されていない残余であって、active な attack ではない。

現行 main の判定:

- `hanjuku_chart.py` の `GUARD_FREE_CHAPTERS` は `{9}` だけで、ch1 は guard-free ではない（L1350-L1368）。
- `orders.py::_source_spare_missing` は、probe でも既に active な step でもない通常注文について、出撃元の利用可能将軍が2人未満なら True を返す（L2747-L2787）。
- `next_order` は `_source(order) in owned` などに加えて `not _source_spare_missing(...)` を要求する（L2349-L2365）。

この3点を当てはめると、4城とも利用可能将軍1人なので、通常の attack/move はどの城からも `next_order` を通らない。行軍中・`launched_unconfirmed` もないため、reopen/reconcile 系の例外も発生しない。

翌5分に `plan_starved_rearm` が1回記録されている点も同じ方向の証拠である。現行コードでこの記録は、pending があっても `ready`・source 在城・owned-target 除外・`_source_spare_missing` 否定を全て通る runnable な注文がない場合にだけ再計画へ進む（`orchestrator.py` L749-L779）。実行可能な注文を activation が見落とし続けた #5675/#5719 型とは逆の状態である。

## 3. 月次処理・戦闘の評価

月内 counts では `month_plan` / `month_sub_open` / `post_month_status_*` / `recruit_priority_plan` などの月次処理と、`battle_start` 2・`monster_menu_choice` 14を含む戦闘系 40 件が見える。監査はこれらから `monthly_scan_hold` と `battle_grind` を出した。

ただし、§2 のとおりこの月に開始できる通常出撃はない。月次処理は将軍数・兵士数の確認や募集優先計画を含む通常処理であり、1将軍城しかない状態では編成改善の必要な処理である。戦闘も `defense_observed` を伴う防衛側の応答で、放棄できる探索行動ではない。したがって「占有」は監査シグナルとしては正しいが、実行可能な出撃を阻害した実欠陥とは判定しない。

## 4. #5675 / #5719 との異同

**同型ではない。** #5675/#5719 はどちらも owned-target stale attack ループで、結論 (b) となったのは PR #5645 がそのループを直接修正したためである。本件にはその構成要素がない。

| 観点 | #5611 ch1 1-6 | #5675 ch6 / #5719 ch7 |
| --- | --- | --- |
| 先頭の問題 | 全保有城が1将軍で dispatch 可能な通常注文なし | 奪取済み城への stale attack が残る |
| `plan_map_activate` | 月内 counts に無し | 繰り返し出現（#5675 は activate 7/取消6、#5719 は activate 5/取消4） |
| `sortie_route_unverified` | 無し | activate と対で繰り返す |
| 対象 step | `plan.active` なし、`chart_plan.orders` 空 | 具体的な attack step と target がある |
| 行軍/発射未確認 | `sorties`・`launched_orders` とも空 | 補助要因として古い行軍や launched_unconfirmed が残る |
| 正しい修正 | なし（現行の no-empty-castle 方針どおり） | PR #5645 で owned-target を done/settle し activation から除外 |

#5719 にあった `post_month_sortie_floor` や boss phase の文脈も本件にはない（`boss_month` は空）。章・城が違うだけでなく、発火している分岐とstep の状態遷移が別である。

## 5. 既存修正のカバー有無

| 修正 | 本件への適用 |
| --- | --- |
| PR #5645 / v1529（owned-target settle） | **非適用**。`plan_map_activate`→`sortie_route_unverified` のループ、奪取済み target、active な stale attack がいずれも無い。 |
| PR #5647 / v1530（launched_unconfirmed bounded reopen） | **非適用**。`launched_orders` と `world.sorties` が空で、zombie 行軍は無い。 |
| PR #5689 / v1556（boss dispatch recovery） | **非適用**。`boss_month` は空で boss-only phase ではなく、`order_dispatch_starved` も月内 counts に無い。 |
| PR #5608 / v1515（召喚獣メニュー合計 cap） | **本件の包括的カバーとは判断しない**。現行 main では `MONSTER_MENU_TOTAL_CHOICES = 40`（`battle_menus.py` L3666-L3670）を超えた1戦闘だけ `b` で閉じる（L3863-L3870）。ところが counter は `monster_menu_choice` を記録する同じ分岐で増え（L4175-L4180, L4290-L4310）、#5611 の月内合計は14件、先行 #5592 でも22件しかない。したがって issue 証拠からは cap が発火したとはいえず、#5592 と同一 fingerprint だから修正済みという従来トリアージは根拠不足である。もっとも本件では §2 の出撃不能が主条件なので、この cap の実効性を修正しなくても結論は変わらない。 |

## 6. なぜ (a)(c)(d) ではないか

- **(a) 実欠陥ではない**: 出撃ゼロは事実だが、現行仕様では1将軍城から通常注文を出さない no-empty-castle 方針に明示的に従っている。月次処理と防衛戦が実行可能な出撃を横取りした経路は確認できない。1将軍でも出す speedrun 方針へ変えるかは設計変更であって、今回の証拠だけでは実装欠陥とはいえない。
- **(c) 誤検知ではない**: 626観測で departure/order_start/order_launched が全て0で、翌5分も0。監査値とハッシュも一致する。
- **(d) 判定不能ではない**: raw jsonl がなくても、issue 内包の garrison/sorties/plan と現行 `next_order` の gate から、通常出撃が開始不可能だったことを特定できる。

## 7. 残件・注意

- `monthly_scan` は primary signal の優先順位で題名へ出るが、本件では `no_source` が出撃不能の構造的条件である。題名だけから #5675/#5719 と同型と扱わないこと。
- `monster_menu_choice` の戦闘時間は speedrun 上の改善余地ではあるが、#5608 の cap がこの種の月を実際に救うかは別検証が必要。必要なら cap 実効性だけの follow-up に分離すべきで、本 issue の出撃ゼロ経路への必須修正ではない。
