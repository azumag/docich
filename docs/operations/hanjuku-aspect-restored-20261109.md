# 半熟英雄: 画面比率を4:3に戻す（v630、2026-11-10 オーナー指摘）

## 指摘（オーナー）

「画面レイアウトの画像比率が変わってしまっている」

## 原因

v629 で `[retroarch] force_aspect = false` を入れて**生ピクセル256x224**にしたため、
配信の投影が縦長（8:7）になりました。これは
AGENTS「ゲーム切替時の描画サイズと配信レイアウト」の
**ゲームは標準ウィンドウ寸法で動かす**という規則に反します。

## 復旧（実測済み）

1. ランタイム `retroarch.cfg` を `video_force_aspect = "true"` に戻す
   （バックアップ `retroarch.cfg.bak-1103` を取得済み）。
2. ゲーム窓を再起動（コーディネータが自動で再生成）→ **presentation 299x224 / ffplay 721x540** に復帰。
3. `LOAD_STATE` で **10:58 のセーブ**から復帰 → **第8話・月18-6・8城占領・失陥0** を維持。
4. エージェントを再起動（観測160,083・戦闘613で稼働）。

## リポジトリ側（v630 `hanjuku-chart-v630-aspect-restored`）

- `config/games/hanjuku-hero.toml` から `force_aspect = false` を**削除**し、
  「4:3を維持する（OCRは取得画像の扱いで改善する）」とコメントを残す。
- `tests/test_hanjuku_retro_registration.py` を元の期待値へ戻す。

## 次の一手

OCR改善は**比率を変えずに**行います（`Frame.resized()` の補間・しきい値を見直し、
切り札一覧・出撃確認の読み取りを改善する）。
