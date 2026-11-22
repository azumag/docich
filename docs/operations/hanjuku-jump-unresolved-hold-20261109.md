# 半熟英雄: アクティブ指示が解決できない間も移動を止めない（v619、2026-11-10）

## 症状（実測 07:01）

`order_start`（プラリネ→しまばら／出撃元 いちじょうだに）の**1秒後**に
`castle_jump_failed(why=order_changed, age=1)`。

## 原因

計画の差し替えで `mem['active']` は**文字列としては残る**ものの、そのIDに対応する
注文オブジェクトが新しい計画に無い状態（解決不能）があり、v618 の「空のとき待機」に
当たらず中断していました。

## 修正（v619 `hanjuku-chart-v619-jump-unresolved-hold`）

`active` が空 **または 解決できない** 場合は、最大4フレーム待機して指示の復帰を待ちます。

## 確認

- `tests/test_hanjuku_castle_jump.py` に1件追加（解決不能な active で待機）。
- ジャンプ・チャート・出撃優先度 461件 passed。
- 本番反映後: `castle_jump_failed(order_changed)` の減少を実測する。
