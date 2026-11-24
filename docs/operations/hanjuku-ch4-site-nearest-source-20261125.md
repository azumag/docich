# 半熟英雄: 第4話 四季王サイトの出撃元を最短距離優先にする（v1478、2026-11-25）

## 症状（本番 g1342 / v1470〜v1477 の実測）

第4話のボス段階で、四季王サイトへの出撃は成立するようになったが、

- 出撃頻度がまだ低い。
- ボスから遠い城から出撃する（例: アルカ→四季王城(南東) 1159 cells、
  フヌイヌム→四季王城(北西) 793 cells、ネクスドリア→四季王城(北東) 797 cells）。
  近い城（ネクスドリア→南東 343、アルカ→北西 377、フヌイヌム→北東 365）に
  適格な卵持ち将軍がいても、より強い将軍が遠い城にいるという理由で
  そちらが選ばれていた。

長距離行軍は月の時間を消費し、途中の戦闘・防衛・再出撃で頻度をさらに落とす。

## 原因

`strategy/orders.py` の `_boss_companion_source` は、四季王サイトについて
`max(best, key=(strength, -distance))` で「最強の代役」を選んでいた
（owner 2026-10-30「強い将軍から」）。distance は強さが同じ場合の
タイブレークにすぎず、owner 2026-11-05 の「ボス戦は卵が決め、強さでは
決まらない。同じ条件の出撃元では最短行軍を優先する」と整合していなかった。

さらに `_follow_general` は、計画の将軍が計画の出撃元にいると
`source_override` を更新せず、LLM/機械計画が遠い城を指したままにしていた。

## 変更（v1478）

- `_boss_companion_source`（第4話サイト）:
  - 最寄りの適格な卵持ち代役の距離を基準にし、`SEASON_SOURCE_NEAR_RATIO = 1.25`
    以内の出撃元だけを候補にする。その中では従来どおり強さ→距離で選ぶ。
  - これにより 16 cells 程度の差では強い将軍を維持しつつ、343 vs 1159 の
    ような遠距離出撃を防ぐ。
- `_follow_general`（第4話サイト）:
  - 計画の将軍が計画の出撃元にいても、最寄りの適格な代役の城へ
    `source_override` を更新する。サイト出撃は特定の将軍に固定されず、
    deploy がその城の適格者を選ぶ。
  - 適格な代役が記録に無いときは古い出撃元上書きを捨てて待機に戻す
    （`boss_source_override_cleared`）。
  - 出撃元を変えたときは `boss_companion_source` に距離を記録する。
- `version.py`: `hanjuku-chart-v1478-site-nearest-source`。
  `ops/vm_actions/collect_diagnostics.py` の許可リストへ追加。

## 検証

- `tests/test_hanjuku_season_site_general.py`:
  - 強いが遠い城より、近い同等能力の城を選ぶ。
  - 同等能力が無い場合も、遠い弱い城より近い適格城を選ぶ。
  - 計画が遠い城を指していても、近い適格城へ出撃元を上書きする。
  - 既存の「卵持ちを残す」「1人城から出さない」「主人公を出さない」規則は不変。
- `tests/test_hanjuku_hero_guard.py` / `test_hanjuku_boss_companion_wait_bound.py` /
  `test_hanjuku_boss_partial_garrison.py` / allowlist テストを含む 58 passed。
- ボス・季節サイト系 507 passed（変更前の同範囲）。

## 未確認・残件

- 実機で出撃頻度が増えるかは、デプロイ後の `order_launched`（四季王城向け）の
  間隔・出撃元距離・`boss.battle_count` で確認する。
- 近い弱い将軍を送る頻度が増えるため、敗北・再出撃の増加は実機で監視する。
  必要なら「近い城へ強い将軍を移動してから出す」staging を follow-up とする。
