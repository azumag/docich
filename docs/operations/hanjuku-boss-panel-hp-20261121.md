# 半熟英雄: 待ち伏せボス戦のHPを記録する（v1276、2026-11-21）

## 症状

2026-11-20 20:46、軌道待ち伏せ将軍への接触で**初のボス戦**が成立した
（`hanjuku_month_audit` の `boss.battle_count = 1`）が、`boss_hp_seen` は
None のままだった。ボスHP500を削り切る進捗を測れない。

## 原因

既存の `battle_menus.note_boss_monster_hp` は
`plan.attack.castle` がボス城であることを条件にしており、待ち伏せ（チャート
出撃ではない）経路では `attack` 計画が無いため一度も記録されない。

## 変更

- `flows/battle.py`: 開戦パネルを読んだ直後に `_note_boss_panel_hp(mem, battle)`
  を呼ぶ。敵名が `chart.BOSSES` / `reference.BOSS_GENERALS` / ボス城名に一致する
  とき、`boss_hp_seen` と `boss_hp_history`（最大8件）へ記録する。
- `BOT_VERSION` を v1276 へ、hot-load許可リストへ追加。

## 検証

- `tests/test_hanjuku_boss_panel_hp.py`（3件）: ボスパネルでHP履歴が伸びる／
  通常敵は記録しない／不正HPは無視。
- 戦闘パネル・通常戦メニュー・結果持ち越し・キー台帳 14 passed。

## 未確認

- 実機の次のボス戦で `boss_hp_seen` が入り、HPの減少を追えるか。
