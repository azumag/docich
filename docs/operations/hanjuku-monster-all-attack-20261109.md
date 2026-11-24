# 半熟英雄: 召喚獣戦で相手もモンスターなら「全体攻撃」を選ぶ（v588、2026-11-09）

## 指摘（オーナー）

「召喚獣戦では、相手もモンスターなら、『全体攻撃』を選ぶように作ったはずだが、
そうなっていないのはなぜだ。修正して」

## 原因

`judgment/events._score_monster_attack` の全体攻撃ボーナス（+100000）は
**将軍戦（`opponent_kind == 'general'`）かつ敵兵士が残っているとき（`multiple`）**
にしか付いていませんでした。

```
if opponent_kind == 'general' and multiple and is_all:
    score += 100000
```

召喚獣戦（`opponent_kind == 'monster'`）では 対モ威力×回数 のみで比較していたため、
単体技の数値が大きいと「全体攻撃」が選ばれませんでした。

## 修正（v588 `hanjuku-chart-v588-monster-all-attack`）

```
if is_all and (opponent_kind == 'monster' or (opponent_kind == 'general' and multiple)):
    score += 100000
```

- **相手がモンスターのときは全体攻撃を最優先**（敵モンスターの護衛もまとめて処理できるため）。
- 全体攻撃同士は従来どおり **対モ威力×攻撃回数** で順位付け。
- 将軍戦のルール（敵兵士が残っているときのみ全体攻撃を優先）は不変。
- 即死技の扱い（将軍単体には最優先、モンスターには確率扱い）も不変（
  `return 10**6` は全体攻撃ボーナスより前）。

## 確認

- `tests/test_hanjuku_monster_attack_scoring.py` に1件追加:
  モンスター相手に全体攻撃（威力5）が単体（威力20）に勝つ／全体同士は威力順。
- 召喚獣攻撃・戦闘系 69件 passed。
- 本番反映後: 召喚獣戦で相手がモンスターのとき `monster_menu_choice` が
  全体攻撃の行を選ぶことを実測する。
