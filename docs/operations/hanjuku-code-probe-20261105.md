# 実行中のモジュール版をログに残す（v420、2026-11-05）

## 症状（実測）

v419 で「出撃元を諦める直前」に `source_giveup_probe` を記録するようにしたが、
**20分間で1件も出なかった**。同じ20分で `order_source_changed` は **199件**。

- 記録を出す `_give_up_source` は map.py から呼ばれており、その直前に probe がある。
- それでも probe が出ないので、**実行中のモジュールがチェックアウトと違う**可能性がある
  （`bot_version` だけ新しく、flows モジュールは古いまま）。

## 変更

- `flows/map.py` の `map_step` 冒頭で、モジュール自身の `BOT_VERSION` を
  `_tally(mem, f'module:{BOT_VERSION}')` として数える（判断ログには書かないので既存の記録列を乱さない）。
- ログ側の `bot_version`（ランタイムが刻む値）と `module_version` を突き合わせれば、
  コードが実際に読み込まれているかを確定できる。

## 検証

- 関連（`chart_bot` / `giveup_fallback` / 層）335 passed。
- 記録は版が変わるまで1回だけなので、ログを汚さない。

## 未確認・残件

- 実機で `code_probe` がどの版を示すかは未確認（次ラウンドで確認する）。
- 第6話ボス城への入城は依然として未実測。
