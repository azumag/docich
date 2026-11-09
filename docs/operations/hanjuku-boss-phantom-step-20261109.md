# 半熟英雄: 行軍記録が消えたボスstepを開き直す（v555、2026-11-09）

## 症状

g1163 第6話 2026-11-09 12:47。全11城を制圧してボスへ波状攻撃を始めたところ、
ボス注文 `A:bf8c3dee:M256898` が **`launched` のまま行軍記録（sorties）を持たない**
状態になりました。

`_relaunch_overdue_boss`（上限超過の出し直し）も `_expire_spent_sorties`（期限切れ）も
**sortie レコードを前提**にしているため、この step は永久に完了せず、ボスという
ターゲットを予約したまま他の波を待たせます。

## 修正（v555 `hanjuku-chart-v555-boss-phantom-step`）

`_rearm_phantom_boss_steps(mem)` を追加し、`next_order` の先頭で
`_rearm_phantom_site_steps` の直後に呼びます。

- 対象: `launched` かつ `chart.is_boss_castle(target)` かつ **sortie レコードなし**・
  ライブ戦闘なしの step。
- `order_start_tick` から `BOSS_PHANTOM_TICKS`（2 × BOSS_ARRIVAL_LIMIT = 1600）を
  超えたら `pending` に戻し、`boss_step_rearmed` を記録。
- `BOSS_STEP_REARM_COOLDOWN`（300）で同一 step の連続再アームを抑止。
- **第4話は対象外**（四季王の4地点は既存の `_rearm_phantom_site_steps` と
  撃破・閉鎖の規則が扱う）。
- 章替わりで `boss_step_rearm` を消す（`CHAPTER_SCOPE_KEYS` に追加）。

## 確認

- `tests/test_hanjuku_boss_phantom_step.py`（新規4件）: 記録なしで開き直す、
  ライブ行軍・新しい step は触らない、クールダウンで連続再アームしない、
  通常城の step は対象外。
- ボス・四季王・章スコープ・出撃・行軍 166件 passed。
- 本番反映後: 第6話ボスで `boss_step_rearmed` が出て、波が次の候補へ進むことを実測する。
