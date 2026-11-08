# 半熟英雄: 主人公の城の読取注文も守備人数で弾く（v533、2026-11-09）

## 症状

g1163 第5話 04:38:54〜04:39:07 JST。調整チャートが
「暫定: しょうぐんでカルーンを攻撃」を採用し、出撃元アルマムーン
（どうし + フェデリーニ + ポワソン）で将軍一覧を開いたところ、
実行側の `_keep_hero_guard` が `sortie_held_hero_guard` で取り消した。

この注文は `source_probe`（将軍名を確定しない読取注文）だったため、
計画採用時の検証 `chart_adjust.startable` が無条件に通していた。

## 原因

`startable` は source_probe を「情報取得だけ」として素通しし、
実行側の `_keep_hero_guard` は source_probe でも「一覧から選ぶ余剰」を
出す前提で守備人数を判定して取り消す。両者の扱いが食い違っていた。

## 修正（v533 `hanjuku-chart-v533-hero-probe-guard`）

`chart_adjust.startable` の source_probe 分岐に、実行側と同じ判定を追加。

- 出撃元が主人公の城で、記録済みの在城一覧に主人公以外がいて、
  `hero_guard_short` に該当する場合は `hero_castle_guard` を返す。
- 主人公だけの城は読取のみなので従来どおり許可（実行側も同じ）。
- 他の城の source_probe は従来どおり読み取り専用として許可。

## 確認

- `tests/test_hanjuku_hero_castle_guard.py` に3件追加（主人公+2で拒否／
  主人公+3で許可／主人公だけなら許可／他城の probe は不変）。
- チャート調整・出撃元・経路まわり 238件 passed。
- 本番反映後: 実行で取り消される `source_probe` 注文が計画に残らず、
  `sortie_held_hero_guard` が減ることを実測する。
