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

**例外**: boss への再出撃 (`boss` / `boss_move`) は `move_visible` が無くても
`いどう` のまま。#4736 (owner 2026-11-19) で測定済みのボスへの経路で、
ボスへの Y ジャンプはフィールドカーソルを要するため。

## CI 修正 (2026-11-22 06:40)

初回 CI は full-suite 3 shard 全滅。原因は #4736 の「常にいどう」を
pin していた既存テスト群が、通常帰還のメニュー目標を `いどう` (0行目)
としていたこと。#5010 は通常帰還で `いどう` が `dest` で帰還を破棄する
ことを本番で示しているため、通常帰還のテストを `きかん` (3行目) へ
更新した。ボス系のテスト (chapter9 の boss redirect、boss_camp_remarch
の boss_move) は例外により `いどう` のままで green。更新したテスト:

- `test_hanjuku_chapter9_camp_boss_redirect.py`: 通常帰還は きかん へ
  (`test_the_ordinary_recall_also_confirms_idou` を
  `test_the_ordinary_recall_uses_kikan_without_a_visible_target` へ改名)、
  既存の home 帰還 2件は下向きへ。
- `test_hanjuku_boss_camp_remarch.py`: 通常帰還は きかん へ。
- `test_hanjuku_camp_receipts.py`: 観測カーソルのテストを きかん へ
  (意図はカーソル観測のまま)、繰り返し同一カーソルのテストは
  row 0 に固定して下向きへ。
- `test_hanjuku_recall_month_scene.py`: 月初中断の復帰テストを
  きかん へ (元々のコメント意図どおり)。
- `test_hanjuku_return_status_close.py`: 2件を きかん へ。
- `test_hanjuku_camp_recall_ch2.py` (新規): boss 例外のテストを追加。

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
