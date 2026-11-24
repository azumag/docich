# 敵召喚獣ウゴカザルは卵を使わず白兵で倒す（v1461、2026-11-24）

## Owner 指示

> 敵召喚獣がウゴカザルの時は、卵使わずに「攻撃」だけで倒せるのでそうしてほしい

## 根拠

`hanjuku_reference.MONSTER_SKILLS` のウゴカザルは2技とも
`('なぐれっ！', 0, 0, ...)` / `('かきむしれ！', 0, 0, ...)` で**ダメージ0**。
将軍を倒す即死・自爆の技能を持たず、`こうげき` を続けるだけで処理できる。
既存のゾンビ（ゾンビパンチ0ダメージ・ケアル自己回復）と同じ扱いにする。

## 変更（v1461 `hanjuku-chart-v1461-melee-only-ugokazaru`）

- `MELEE_ONLY_SUMMONS` に `ウゴカザル` を追加（`ゾンビ` と同格）。
- 実画面の敵召喚獣が1体だけ読め、名前が完全一致したときだけ適用する
  （未知・複数・未読へは広げない）。
- 卵・おくのてを消費せず `こうげき` を続ける。記録の rule は
  `melee_only_summon`、variant は `egg_battle_melee_only` に統一し、
  判断理由と実況は**実際の召喚獣名**を出す（ゾンビ固定文言を廃止）。

## 検証

- 追加 `tests/test_hanjuku_ugokazaru_melee.py`: 卵なしで攻撃／巨大HPでも白兵／
  おくのて抽選を開かない／実況がウゴカザルと表示する。
- 既存 `tests/test_hanjuku_zombie_melee.py` / `test_hanjuku_summon_preempt.py` を
  新しい rule/variant 名へ更新。
- 関連 66 passed。実機はデプロイ後に、ウゴカザル召喚で `egg_battle_attack` と
  卵残数が減らないことを確認する（本ドキュメント時点では未デプロイ）。
