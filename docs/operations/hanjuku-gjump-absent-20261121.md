# 半熟英雄: 名簿に居ない将軍のSELECTジャンプを長時間スキップ（v1291、2026-11-21）

## 症状（本番 g1288 04:13〜05:11）

`general_jump_failed` が **50回以上**連続。すべて `target_seen: false`、
`scrolls: 40`（全将軍名簿を最後までスクロール済み）、`seen_count: 33`。
対象将軍はすでに国に居ない（月次戦闘で喪失）のに、200 tick のスキップで
**毎分同じジャンプを再試行**し、フィールドを占有していた。

## 変更

- `map._end_general_jump`: 名簿を最後までスクロールしても対象が見つからない場合、
  `general_jump_skip` に `absent: True` を記録。
- `general_jump_step` の再アーム判定: `absent` のスキップは **3600 tick** 有効
  （通常は 200 tick）。
- `BOT_VERSION` v1291、hot-load許可リストへ追加。

## 検証

- `tests/test_hanjuku_general_jump_absent.py`（1件）。
- 第9話野営帰還・台帳・マップ 39 passed。
