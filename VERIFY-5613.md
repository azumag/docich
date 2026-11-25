# VERIFY-5613 — [半熟英雄] 切り札在庫があるのに非携行で出撃: ch2 トレビス

- 対象: [docich#5613](https://github.com/azumag/docich/issues/5613) / 監査 `sortie_without_cards`（confidence `high`）
- fingerprint `43937629bb056205` / occurrence `c451ce5bc2e50627bfe2`
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1516-melee-dialogue-advance`
- ch2 / 月 2-10 / 将軍 トレビス / 出撃元 アルマムーン / 対象 ウラノポリス / purpose `attack`
- 在庫: イッテツーン×1, フットバース×2 / 携行: なし（実測空）/ 計画cards: `[]`
- 観測版の契約コミット: `a01fa19831414164c7a3fb436e18500f68602579`（v1516）
- 検証ベース: `origin/main` = `90fb53a6bbce69fa6b9d19651db7faf5ad34729b`（`BOT_VERSION` `hanjuku-chart-v1525-sortie-cards-eligible`）
- 検証範囲: read-only。本ファイルの追加以外のコード変更なし。本番 SSH・VM 操作・ゲーム入力なし。
- ラン成果物（`hanjuku_decisions.jsonl` / `hanjuku_events.jsonl` / `hanjuku_frames/`）は VM 側のため直接参照せず、issue embedded JSON・git オブジェクト・現行コード・静的再構成・回帰テストを照合した。

## 結論: **(c) 誤検知 — 出撃動作は現行設計どおりで、v1516 の監査だけが携行可能でない在庫を「携行できたのに空」と誤判定した**

本件の出撃自体は、v1448 以降の自動携行設計に従っている。計画が空のとき自動携行されるのは `egg_drop == 0` の安全札だけであり、本件の在庫はイッテツーン（`egg_drop=8`）とフットバース（`egg_drop=5`）だけなので、自動携行の適格在庫はゼロだった。したがって空 kit で出撃したことは戦略実装の取りこぼしではない。

一方、観測版 v1516 の月次監査は、通常出撃で boss-only 札を除いた後、正数在庫が残っていれば `egg_drop` を見ずに適格在庫とみなしていた。そのため、方針上自動携行できない2種だけを理由に `sortie_without_cards` / confidence `high` を起票した。これは監査側の誤検知である。

現行 main では [PR #5636](https://github.com/azumag/docich/pull/5636) / squash commit `05f05c0506a213e908ad076a1e8450406882d074`（v1525）がマージ済みで、計画が空の場合は `egg_drop == 0` の札だけを監査上の携行可能在庫として数える。本件と同じ在庫構成を使う回帰テストでも起票しないことを確認した。したがって新規の未修正欠陥はなく、修正 PR は作成しない。

## 1. fingerprint / occurrence の照合

issue embedded JSON の fingerprint と incident_id は、現行 main の式でも再計算できた。監査出力は本件の観測に対応しており、別 issue や別 occurrence の転用ではない。

- fingerprint raw: `2|sortie_without_cards|sortie_without_cards`
  - sha256先頭16: `43937629bb056205` — issue と一致
- incident raw: `g1190-ch1v9|1190|sortie_without_cards|A:c3037179:Mb1cbfe|トレビス|1795617641`
  - sha256先頭20: `c451ce5bc2e50627bfe2` — issue の occurrence / incident_id と一致
- `created_at` は `1795617641.747373` で、決定列の `order_launched`（14:40:41Z）と同じ秒に整合する。

## 2. 携行なしは実測であり、#5258 の証拠欠落型ではない

embedded JSON の `sortie_event` は次の通りで、携行未観測ではなく実測空である。

- `carried: []`
- `carried_observed: true`
- `planned_cards: []`
- `decision: order_launched`

現行 `flows/deploy.py` L2887-L2908 では、出撃確認で `want` と `got` が一致した場合に `plan.order_context[step].observed_metric.cards = got` を保存し、`sortie_confirm` を記録する。現行 `month_audit.py` L2509-L2535 の `_sortie_carried_cards` は、この `order_context`、出撃 binding、同一 batch の `sortie_confirm` を実測携行の証拠として読む。これは PR #5258 が入れた measured receipt 経路である。

issue embedded JSON の `plan` summary に `order_context` が見えないのは、`month_audit.py` L376-L399 の `_summarize_plan` が `active`・`orders`・`chart_plan` だけを incident へ写し、`order_context` を写さないためである。summary に無いことは、runtime state に receipt が無かったことの証拠ではない。実際 `carried_observed=true` なので、監査は measured empty receipt を読めていた。

したがって本件は #5258 が対象にした「実際は携行していたのに監査が receipt を読めず空と誤認した」型ではない。携行なし自体は実測どおりで、問題は「その空携行が欠陥か」の適格性判定に限定される。

## 3. v1448 自動携行の適格在庫はゼロだった

観測版 v1516 `strategy/kit.py` L108-L146 と現行 main 同箇所は、計画が空のとき `_auto_carry_safe_card` を呼ぶ。同関数の適格条件は次の通りである。

- 正数在庫がある
- この出撃で消費・破棄していない
- `reference.CARDS[name]['egg_drop'] == 0`
- 携行枠と ID 合計の安全弁を通る

本件の在庫値は v1516・現行 main の両方で同じである。

| 札 | 在庫 | egg_drop | 自動携行の適格性 |
| --- | ---: | ---: | --- |
| イッテツーン | 1 | 8 | 不適格 |
| フットバース | 2 | 5 | 不適格 |

静的再構成でも、`card_stock={'イッテツーン': 1, 'フットバース': 2}`・step `A:c3037179:Mb1cbfe` を `_auto_carry_safe_card` に渡すと `None` が返り、`cards_autocarried` も記録されない。つまり v1516 当時の戦略層でも、この在庫から自動携行される札はなかった。

`flows/deploy.py` L912-L920 の empty-kit guard は `bool(want) and carried == []` なので、適格札がなく `want=[]` となった本件では発火しない。`want=[]` / `got=[]` は一致し、出撃確認から `order_launched` へ進む。これは計画空・適格在庫ゼロの通常経路である。

## 4. v1516 の監査が誤判定した直接原因

観測版 v1516 `month_audit.py` L2545-L2556 では、通常出撃では `BOSS_ONLY_CARDS` を在庫候補から除く一方、その後は正数在庫が残っているかだけを見ていた。`egg_drop` による絞り込みは無かった。

本件ではイッテツーン・フットバースが正数なので、v1516 の監査は `stock` 非空と判定し、実測 `carried=[]` と合わせて `sortie_without_cards` / confidence `high` を構築した。しかし戦略層が自動携行できるのは `egg_drop=0` だけなので、この `stock` は「携行可能在庫」ではなかった。これが誤検知の直接原因である。

現行 main `month_audit.py` L2564-L2573 では、計画が空の場合に限り `egg_drop == 0` の札だけを在庫候補へ残す。計画が札を名指しする場合は従来どおり全在庫で判定する。ボス専用札の除外も従来どおり維持されている。

## 5. PR #5636 / v1525 のカバー確認

[PR #5636](https://github.com/azumag/docich/pull/5636) は現行 `origin/main` の祖先であり、merge 済みである。差分は監査の適格在庫判定と回帰テストに限定される。

- 追加条件: `if not _card_names(order.get('cards')):` のとき、`egg_drop == 0` 以外を `stock` から除外
- 否定回帰: `tests/test_hanjuku_month_audit.py` L2668-L2675 は、本件と同じ `{'イッテツーン': 1, 'フットバース': 2}` で incident が出ないことを確認
- 肯定回帰: L2678-L2684 は、同2種に加えて `egg_drop=0` のデッドガンがある場合は従来どおり `sortie_without_cards` を起票することを確認
- 自動携行側の回帰: `tests/test_hanjuku_auto_carry_safe_card.py` は `egg_drop>0` の札を自動携行しないこと、安全札だけを1枚選ぶことを確認

ローカル実行結果:

- `PYTHONPATH=src:$HOME/py311shim python3 -m pytest -q tests/test_hanjuku_month_audit.py -k 'stock_without_an_eligible_card or stock_with_a_safe_card or sortie_without_cards_despite_stock or non_boss_sortie_ignores_boss_only_stock or boss_sortie_still_counts_boss_only_stock' tests/test_hanjuku_auto_carry_safe_card.py` → 5 passed
- `PYTHONPATH=src:$HOME/py311shim python3 -m pytest -q tests/test_hanjuku_auto_carry_safe_card.py` → 4 passed

なお v1516 から現行 main までの `strategy/kit.py` 差分は、台帳保持上限・購入優先度まわりであり、`_auto_carry_safe_card` の `egg_drop == 0` 条件を変えていない。`hanjuku_reference.py` に差分はあるが、イッテツーン=8・フットバース=5 の値は v1516 と現行 main で同じである。

## 6. #2829 との関係

#2829 は「切り札を持っているのに携行しない／資金があっても購入・備蓄しない」という広い運用症状の元 issue である。本件は表面的には第1症状に似るが、今回の在庫2種は現行設計の自動携行対象ではないため、#2829 の「携行すべきものを携行しなかった」実例としては使えない。

したがって本件は #2829 を closed にする根拠にも、#2829 の実欠陥を直接証明する根拠にもならない。#2829 の購入・備蓄側や、計画が札を名指ししていたのに空になった事例は別途個別に検証する必要がある。

## 7. 設計判断の問い（検証結論から分離）

「計画が空の場合に自動携行すべきか」という問いは、本件の検証結論とは分離する。

現行のオーナー設計は v1448 で明確に入っており、答えは「する。ただし `egg_drop=0` の安全札だけを1枚まで」である。理由はコード内コメントにある通り、資源を使い切る一方で卵落としは避けるためである。この設計の下では、本件のイッテツーン・フットバースを自動携行しないことは欠陥ではない。

もし「卵落リスクがあっても攻撃性能の高い札を自動携行すべき」「plan-empty でも卵落札を候補に入れるべき」という方針へ変えるなら、それは監査修正ではなく戦略設計の変更である。敵卵使用・卵落とし・切り札ID合計48未満・ボス専用札・購入優先度へ影響するため、別途オーナー判断と受入条件が必要である。本検証はその設計変更の可否を決めない。

## 8. 未検証の範囲

- 本番 VM の生 runtime ファイルと frame 画像は参照していない。issue embedded JSON の measured receipt、hash、コード差分、回帰テストが揃っているため、結論には足りる。
- v1525 以降の実機で同型誤起票が止まるかは、次回以降の月次監査データで確認する運用検証である。今回の read-only 検証では実機再発確認を行っていない。
- compact 再発コメントがある場合でも、本検証の対象は issue 本文の occurrence `c451ce5bc2e50627bfe2` に限定する。
