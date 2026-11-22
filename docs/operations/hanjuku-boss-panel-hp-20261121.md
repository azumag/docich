# 半熟英雄: 待ち伏せボス戦のHP記録（v1296、2026-11-21）

## 症状

1. 2026-11-20 20:46、軌道待ち伏せ将軍への接触で**初のボス戦**が成立した
   （`hanjuku_month_audit` の `boss.battle_count = 1`）が、`boss_hp_seen` は
   None のままだった。ボスHPを削り切る進捗を測れない。
2. v1293 (#4844) は `month.chikujou_leftover` に `chikujou_refused` 記録を置いたが、
   実際の拒否プロンプト（「これいじょうのぞうちく…/どのしろを…」）は
   `orchestrator._is_chikujou_prompt` で先にB閉じされるため `chikujou_refused` が
   記録されずスキップが発火していなかった。

## 変更（PR #4825 / PR #4847 統合）

- `flows/battle.py`: 開戦パネルを読んだ直後に `_note_boss_panel_hp(mem, battle)`
  を呼ぶ。敵名が `chart.BOSSES` / `reference.BOSS_GENERALS` / ボス城名に一致する
  とき、`boss_hp_seen` と `boss_hp_history`（最大8件）へ記録する。
- `orchestrator.py`: `_is_chikujou_prompt` 分岐でも `mem['chikujou_refused']` を記録する。
- `BOT_VERSION` を `hanjuku-chart-v1296-boss-panel-hp` へ更新し、
  `collect_diagnostics.py` の hot-load 許可リストへ追加。

## 検証

- `tests/test_hanjuku_boss_panel_hp.py`（6件）
- `tests/test_hanjuku_chikujou_orch_refused.py`（1件）
- `tests/test_hanjuku_chart_bot.py`（関連回帰）
