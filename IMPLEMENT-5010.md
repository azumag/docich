# IMPLEMENT-5010 — [半熟英雄] キャンプ帰還失敗: ch2 将軍不明 (v1362)

- Issue: azumag/docich#5010 / 判定 `camp_recall_failed` (ch2、ラン g1190-ch1v5)
- Base: origin/main 1705fbd81
- バージョン: `hanjuku-chart-v1362-camp-recall-ch2`

## 原因

PR #4736 (2026-11-19) 以降、キャンプ帰還は無条件で `いどう` を選んでいた。
`いどう` はフィールドカーソル画面 (`screen.kind=='map'`) を開くが、`dest`
段階は `map` を `move_visible` 設定時 (回廊 ch9・ゲートウェイ ch11・中継)
にしか処理しない。`move_visible` の無い章 (ch2 を含む) では `dest` が
`map` を見た瞬間に帰還を破棄する (recall.py:1615-1621、`_flow(mem).pop('recall', None)`)。

## 修正

`_camp_recall_step` (flows/recall.py): `move_visible` が設定されている
場合のみ `いどう` を選び、無い場合は `きかん` の全島ピッカー (`map_target`)
を選ぶ。`dest` は `map_target` を全章で処理する。

## 変更ファイル

- `src/docich/hanjuku/flows/recall.py`: `move_visible` 有無による `いどう` / `きかん` の分岐。
- `src/docich/hanjuku/version.py`: v1362 へバンプ (v1361 は #5000 が使用中のため)。
- `ops/vm_actions/collect_diagnostics.py`: 許可リストへ v1362 を 1 行追加。
- `ops/vm_actions/tests/test_collect_diagnostics.py`: 固定値リストへ v1362 を追加。
- `tests/test_hanjuku_camp_recall_ch2.py` (新規): ch1/ch2/ch9 で `move_visible` 無し時に `きかん` へ下りること、`move_visible` 有り時に `いどう` を即確定することを固定。
- `tests/test_hanjuku_chart_bot.py`: `test_camp_recall_walks_cursor_menu_and_own_castle` を両分岐に対応 (move_visible 無し時は `きかん` へ下りる、有り時は `いどう` を即確定)。

## テスト

- ゲート (ローカル): 新規 4 件 + chart_bot の recall/camp 系 + recall_picker_native + recall_arrival_status + hotload 固定値テスト = 47 passed。
- 注意: 新規テストファイルの初版はメニュー画面の fixture が誤っていた (行インデックス 33 を渡し、`lines=[]` で TextLine 行が無かったため `menu_to` が行を見つけられず `[]` を返していた)。主担当が修正済み (行 0・TextLine 行あり)。

## 残課題

- CLINE が #5010 の read-only 原因切り分けを担当していたが、モデル daily limit で停止中。本 PR がその代替となる。
- v1359 マージ後の本番観測: ch2 でキャンプ帰還が成立することを確認すること。
