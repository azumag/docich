# 半熟英雄: 記録済みの城は許容範囲を少し超えても採用する（v600、2026-11-10）

## 症状（実測・v599 反映後）

月次のマーカー内訳が取れるようになりました（00:35〜00:38）:

| 結果 | 件数 |
|---|---|
| `out_of_tolerance` | **6** |
| `mapped` | 2 |
| `army_marker_fallback` | 1 |

v599 の探索窓拡大で**三角自体は検出できています**が、今度は
「マーカーの位置が最寄りの城から 96（許容）より遠い」で落ちています。
第7〜12話の城セルは source map の**推定値**なので、地図全体が少しずれて
全マーカーが範囲外になり得ます。

## 修正（v600 `hanjuku-chart-v600-marker-tolerance-hint`）

許容範囲を超えた場合でも、**その将軍の記録済みの城**（hint）が
`MAP_CASTLE_TOLERANCE * 1.5` 以内なら**その城で採用**します
（理由: `out_of_tolerance_hint`）。

- hint が無い、または 1.5 倍を超える場合は従来どおり `out_of_tolerance`（保留）。
- 記録済みの城は「その将軍が直前にいた城」という実測に基づく事前情報です。

## 確認

- `tests/test_hanjuku_marker_hint.py` に1件追加: 許容96を超えるが1.5倍以内の hint で採用。
- 月次・マーカー系 92件 passed。
- 本番反映後: `out_of_tolerance` が減り、`mapped`／`out_of_tolerance_hint` が増えることを
  実測する。
