# 半熟英雄: 生ピクセル(256x224)を有効化（v629、2026-11-10）

## 背景（実測 10:20〜10:30）

出撃の**変換率が約3%**（`order_start` 40件/時に対し `order_launched` 1件）で、原因は
**切り札一覧・出撃確認の文字が読めない**ことでした（`card_list_unreadable_depart` 34件/時、
`出撃確認を読み取れない` 12件/時）。

ライブの取得は **299x224（4:3）**、リポジトリの **256x224 フィクスチャは全文読める**ため、
横ストレッチがOCR失敗の主因と判断しました。

## 変更（v629 `hanjuku-chart-v629-native-pixels`）

`config/games/hanjuku-hero.toml` の `[retroarch]` に **`force_aspect = false`** を追加。
Snes9x の生ピクセル **256x224** で描画し、8pxグリフの照合を成立させます。

- 既定動作（4:3）は他ゲームでは不変。
- 反映には**ゲームの再起動**が必要（設定は起動時に読まれます）。
- 再起動の復帰点は **10:27 のセーブステート**（`SAVE_STATE` を送信して更新済み）。

## 確認

- `tests/test_config.py` / `tests/test_retroarch_adapter.py` 44件 passed（設定の妥当性）。
- **再起動後に実測する項目**: `card_list_unreadable_depart` の減少、`card_pick` の成立、
  `order_launched` の増加（変換率）、配信の見た目（縦長になる）。
