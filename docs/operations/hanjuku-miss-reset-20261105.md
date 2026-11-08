# 出撃元ミスの回数を代入でリセットする（v422、2026-11-05）

## 症状（実測）

`source_miss` がボス指示で **5のまま1時間以上**動かず、`_give_up_source` が毎観測走っていた。
そのため注文は「歩き出す前」に毎回諦め処理へ入り、城メニューを開くことすらできなかった。

- v419 の probe 224件はすべて `override: null` / `source_miss: 5` / `source: オケアナ` / `home_garrison: []`。
- 一方 `tally` や `orders` など**代入による書き込みは残っている**（v420 の `module:` タリーが立つ）。
- `_give_up_source` は `mem['source_miss'].pop(step)` で戻していたが、この pop が永続状態に届いていなかった。

## 変更

- `_give_up_source`: `pop` ではなく **0 を代入**して戻す（`miss_map[step] = 0`）。
- これで、状態が残る環境でも残らない環境でも「諦めた回数」が積み上がり続けない。

## 検証

- `tests/test_hanjuku_miss_reset.py`（新規2件）: 諦め後に `source_miss[step] == 0`（キーは残る）／2回目も同様。
- 関連（`off_chart` / `boss_sortie_overdue` / `source_mismatch` / `giveup_fallback` / `castle_panel`）126 passed。

## 未確認・残件

- 実機で `source_miss` が 0 に戻り、注文が歩き出せるかは未確認。
- なお本城（アルマムーン）の将軍が空のため、フォールバック先としても出撃できない。
- 第6話ボス城への入城は依然として未実測。
