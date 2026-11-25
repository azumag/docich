# 半熟英雄: 第4話 四季王サイトのボス待機を上限付きにする（v1470、2026-11-24）

## 症状（本番 g1342 / v1460〜v1467 の実測）

第4話のボス段階（通常城6城を制圧済み、残りは四季王城4地点）で、ボスへの出撃が
ほとんど成立しなかった。

- ch4 7-5: `boss_phase` 600.7秒 / 417観測でボス戦闘 0、`order_start` 7 /
  `order_launched` 0（docich#5397）。
- ch4 7-8 / 7-10: 月内ボス出撃 0 が再発（docich#5399）。
- 7-11 の実測では、計画 `A:9a554cd3:S1/S3/S4` が
  `boss_companion_timeout` の後 `order_failed`
  「代役待ち上限後も主人公が出撃元にいない」で終了した。
  出撃元の実一覧は ココット/カシュー、ポセイドン/ブカティーニ で、
  卵持ちの代役が同城に残れず、主人公も出撃元にいなかった。

## 原因

`strategy/orders.py` の `_ready` は、ボス注文の出撃元名簿が既知で適格な代役が
記録されていない場合、`_boss_companion_source_wait_expired` の200 tick 締切で
待つ。しかし第4話の四季王サイト（`chart.boss_sites(4)`）だけは短絡評価で
`return False` しており、タイマーを arm せず、`boss_companion_source_timeout` も
記録しなかった。そのため注文は無記録・無制限に待機し、deploy 層の有界な
代役探索・主人公単独 fallback・bounded fail に到達しなかった
（VERIFY-5397.md、PR #5406）。

## 変更（v1470）

- `orders.py`:
  - 四季王サイト注文の短絡を外し、他のボス注文と同じ
    `_boss_companion_source_wait_expired`（200 tick）を通す。締切後は既存の
    deploy 有界経路へ進む。
  - 怪我・卵消耗した主人公のゲートからもサイト除外を外し、同じ
    `_boss_companion_wait_expired` で有界化する。
  - 待機開始時に `boss_companion_source_wait` を1回記録し、監査の決定内訳から
    どのゲートで止まっているかを読めるようにする。
- `month_audit.py`: 第4話のサイト戦の敵（`SEASON_KINGS`）をボス戦証拠として
  数える。これまでは `battle_start` の敵名が四季王でないため、サイト戦が
  「ボス戦闘なし」に見えていた。
- `version.py`: `hanjuku-chart-v1470-ch4-site-bounded-wait`。
  `ops/vm_actions/collect_diagnostics.py` の許可リストへ追加。

## 検証

- `tests/test_hanjuku_hero_guard.py`:
  - 第4話サイトで、名簿既知・適格代役なしのとき、200 tick まで `_ready` が
    False、締切で True、`boss_companion_source_wait` と
    `boss_companion_source_timeout` が各1回、再評価で再武装しないこと。
- `tests/test_hanjuku_month_audit.py`: 第4話の `SEASON_KINGS` 戦をボス証拠として
  検出し、他章では検出しないこと。
- 関連: `test_hanjuku_hero_guard.py` / `test_hanjuku_boss_companion_wait_bound.py` /
  `test_hanjuku_boss_dispatch_starved.py` / `test_hanjuku_month_audit.py` 187 passed。
  `tests/test_hanjuku_boss*.py` / `test_hanjuku_season*.py` /
  `test_hanjuku_completed_season_sites.py` 501 passed。

## 未確認・残件

- 実機でボス出撃が増えるかは、デプロイ後の `order_launched`（四季王城向け）と
  `boss.battle_count` の実測で確認する。テスト成功を実機成立とは扱わない。
- `boss_cell_missing` / `boss_pursuit_idle` は第4話では構造的に常時発火する
  監査ノイズとして残る（fingerprint を変えないため本変更では触らない）。
