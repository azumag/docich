# 半熟英雄: エミュレータの生ピクセル指定（force_aspect）を追加（2026-11-10）

## 背景（実測）

保留・出撃キャンセルの根本原因として、**エミュレータ出力の横方向ストレッチ**が疑われます。

- ライブの取得フレームは **299x224**（Snes9x の 4:3 ウィンドウ）。
- リポジトリ内の **256x224 フィクスチャ**（`tests/fixtures/hanjuku/g706-resume-card-select.png`）は
  `parse()` で全文が読めますが、ライブの 299x224 は **spans が空**（`text_len: 0`）。
- 299/256 = 1.168 倍の横伸びで、8px グリフのテンプレート照合が外れます。

## 追加（コード）

`[retroarch] force_aspect = false` を指定すると、生成する `retroarch.cfg` に
`video_force_aspect = "false"` を出し、**コア本来の 256x224 ピクセル**で描画します
（真偽値以外は `AdapterError`）。

- 既定は従来どおり（4:3・299x224）。設定した場合だけ切り替わります。
- 半熟の `[hanjuku]` 分岐でも上書きを維持します。

## 使い方（オーナー判断）

`config/games/hanjuku-hero.toml` の `[retroarch]` に

```toml
force_aspect = false
```

を追加し、**ゲームを再起動**すると 256x224 になります（配信の見た目は縦長になります）。
OCR が回復するかは再起動後に実測が必要です。

## 確認

- `tests/test_retroarch_adapter.py` に1件追加（false の反映と非boolの拒否）。
- アダプタ系 34件 passed。
