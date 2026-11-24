# 半熟英雄: 配信の左余白カードへミニマップを移植（v570、2026-11-09）

## 症状（オーナー報告）

「ミニマップ表示まだ見えないよ」

## 原因

配信で実際に使われるオーバーレイは `games/soviet_now/overlays/direct_broadcast_overlay.html`
（VM の `/home/ubuntu/soren/overlays/…` に投影される版）でした。

- 2026-11-07 の docich #3624 は **docich 側の複製にだけ**ミニマップ
  （`HANJUKU_MINIMAP_COORDS` / `gapMinimap`）を追加していました。
- VM の実ファイルは 2026-11-06 の内容のままで、`HANJUKU_MINIMAP_COORDS` は **0件**。
  そのため、データ供給（v566 の live-status 修正）を直しても描画側にコードが無く、
  ミニマップは出ませんでした。

## 対応

1. **soviet_now PR #827**（`feat/hanjuku-gap-minimap-overlay`）で、docich 版の
   ミニマップ（座標表・`gapSvg`/`gapRouteKind`/`gapMinimap`・関連CSS）を配信版へ移植。
   既存レイアウト（出撃チャート・ニュース帯・進行フロー）は変更せず、SVG を上に追加。
   再描画シグネチャに `chapter` と `recallRoute` を追加。CI（contracts / test）合格・main へマージ。
2. docich の `games/soviet_now` 参照を **bb44f60c0** へ更新（このPR）。

## 確認

- 移植版: `<script>` 2ブロックの構文チェック OK、`HANJUKU_MINIMAP_COORDS` 2件。
- 本番デプロイ後: VM の `/home/ubuntu/soren/overlays/direct_broadcast_overlay.html` に
  ミニマップのコードが入り、配信の左余白カードに城配置と経路が出ることを実測する。
