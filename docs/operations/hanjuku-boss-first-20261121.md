# 半熟英雄: ボス攻撃を暫定候補の先頭にする（v1335、2026-11-21）

## オーナー指示（2026-11-21）

> 1. ボスHPが低い（例 ≤16）ときだけでなく、そもそもボス攻撃を最優先にしたほうがいい
> 2. バルムンクはそもそもボス戦以外では使用禁止

## 変更（1）

- `strategy/replan.interim_candidates`: ボスが open のとき、候補リストの**末尾**に
  追加していたのを**先頭**へ移動（`targets = [boss, *targets]`）。
- これにより奪還・駐留補充より先に、ボスへの出撃が毎回候補の1番になる。
- `tests/test_hanjuku_boss_first_order.py` を追加。

## 確認（2）

- バルムンク（`reference.BOSS_ONLY_CARDS`）は**既にボス戦以外では使えない**実装です:
  - `strategy/boss_cards.py`: 補充札にしない
  - `strategy/orders.py`: 派生戦術から除外
  - `flows/battle.py`: ボス突入の実測前は planned/opening から除外
  - `tests/test_hanjuku_boss_only_carry.py`: 通常出撃に持ち込まれないことを検証
- 追加の変更は不要と判断しました（必要ならさらに厳格化します）。

## 検証

- ボス優先・priority moves・gather・boss-only carry・probe・台帳 19 passed。
- `BOT_VERSION` v1335、hot-load許可リストへ追加。
