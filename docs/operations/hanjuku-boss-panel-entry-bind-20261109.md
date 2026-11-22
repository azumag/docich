# 半熟英雄: ボスの戦闘パネルを読めたら突入として在途出撃を結び付ける（v559、2026-11-09）

## 症状

g1163 第6話 2026-11-09 13:36:33 JST。ヘーゼルがボス（ハードロボ HP152）へ突入し、
`boss_hp_seen` には ハードロボ 152 が記録されましたが、**`attack_observed` が一度も
出ませんでした**（突入文「〜がボスじょうにせめこんだ!!」を読めないフレームで失った）。

その結果、その出撃は `en_route` のまま残り、14:00:04 に
`boss_sortie_overdue`（「突入後に勝敗の実測が残らず上限を超えた」）として
**同じ波を再送**。ボスHPは152のまま動きませんでした。

## 修正（v559 `hanjuku-chart-v559-boss-panel-entry-bind`）

`note_boss_monster_hp`（ボスの戦闘パネルから敵モンスターのHPを読む箇所）で、
`attack['step']` が未確定なら `_match_sortie` → `_bind_sortie` を呼び、
**在途の出撃を突入済みとして結び付けます**（`boss_entry_bound_by_panel` を記録）。

- 一致した sortie が1件だけ（`matched`）のときだけ結び付け、曖昧なら何もしない。
- すでに step が確定している場合は何もしない（従来どおり）。
- ボス城以外のパネルでは従来どおり `boss_hp_seen` も更新しない。

## 確認

- `tests/test_hanjuku_boss_entry_panel_bind.py`（新規3件）: パネルで在途出撃が
  arrived になる／確定済みは不変／ボス以外では結び付けない。
- ボス・HPノート・波・ファントムstep・出撃 120件 passed。
- 本番反映後: ボス突入時に `boss_entry_bound_by_panel` が出て、
  `boss_sortie_overdue` の再送が減り、ボスHPが実際に減ることを実測する。
