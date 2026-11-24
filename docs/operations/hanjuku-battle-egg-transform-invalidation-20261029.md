# 半熟英雄: 敵の卵使用・変身後の卵温存失効（#2912）

## 概要

開幕の `battle_egg_conserve`（白兵優位のため卵を温存）判断が戦闘全体にラッチされ、
敵の卵使用・変身（ガルバンゾー HP30 → ダークエルフ HP240）後も再評価されず、
卵を持ったまま戦死した事例（ライム戦、g1075）への対応。

## トリガー

- **A 敵変身検出**: 敵名が開幕 scope と不一致、敵 HP が開幕 watermark を超えて回復、
  または敵技メニューに「たまごにもどれ」を観測 → `battle_enemy_transform_observed` を記録し、
  `conserving_melee` / `conserved_labels` を失効。以後は新鮮な観測で
  `_healthy_melee_margin_evidence` が成立した時だけ温存を再許可する。
  ボス戦ではトリガー記録のみ。
- **B HP 再観測**: 読めるフレームすべてから HP watermark を更新。温存の前提
  （非 critical、losing trade 否定）が崩れたら即失効。
- **C フェイルセーフ**: unread/hold 連続時の有界なメニュー再確認（戦闘あたり 2 エピソード）。

## 壊さないもの

#2863 の開幕温存、#2882/#2831 の再突入・rearm、#2896/#2864 回帰。
"""