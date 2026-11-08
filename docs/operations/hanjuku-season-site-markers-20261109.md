# 半熟英雄: 撃破済みの四季王地点に毎回出撃してしまう（v527、2026-11-09）

## 症状（オーナー指摘 2026-11-09 01:4x JST）

> 四話で。クリア済みのボス城、北西、北東、南西クリア済みなのにそこに出撃してしまってるよ

第4話で四季王を3体倒したあとも、ボットが同じ地点へ出撃し続けていた。

## 実測（g1163、2026-11-09 JST）

- 01:47 の本番 Y ビュー（全体マップ）: 北西・北東・南西が**オレンジの旗**
  （255,181,0）、南東が**ドクロ**（(164,165,164) / (255,254,255)）。
  オーナー指摘の3地点と一致する。
- 戦闘記録: 00:58:50 北西 対 フォーリシア（107）、01:27:05 北東 対 サマカンテ（614）。
  01:29:32 の `battle_result` は `enemy_hp: 1` / `outcome: unclassified`。
- `hanjuku_bot.json` の `boss_victories` は null のまま。そのため v526 の
  `_site_king_down`（`boss_victories`/`boss_defeated` を見る）は一度も真にならず、
  01:30・01:40 に北東、01:44 に北西へ再出撃していた。

## 原因

撃破の証拠を「戦闘パネルで敵HP 0を実測」だけに限っていた。四季王戦は最後の
パネルが台詞（「かもん つくしたち!!」等）に替わってから地図へ戻るため 0 を読めず、
勝っていても `boss_victories` に入らない。結果として撃破済みの地点が
「未撃破」のまま残り、再出撃を繰り返す。

## 修正（v527 `hanjuku-chart-v527-season-site-markers`）

Y ビューの地点マーカーを直接読む（`os_layer/readers.season_sites`）。

- 撃破済み: 旗（255,181,0）。未撃破: ドクロの明色（(164,165,164)/(255,254,255)）。
- マーカーの位置は第3話ボスのドクロ (101,63) を基準に、地点セルの差分×8で求める
  （`tests/test_hanjuku_season_king_sites.py` が固定している関係と同じ）。
- Y カーソルの下の地点は読まない（金色カーソルが旗と同色のため）。
- 2回連続で同じ読みになったときだけ `season_sites.<地点>` を更新し、
  `season_site_closed` / `season_site_open` を記録する。読めない地点は前の状態を保つ。
- 「4地点すべて撃破済みなのに章が終わっていない」読みは矛盾として拒否し、
  `season_sites_all_closed_refused` を残す（誤読で章全体が止まるのを防ぐ）。
- `_site_king_down` がこの記録も見るため、v526 の「撃破済み地点へ出し直さない」
  経路がそのまま効く。章が変わると `season_sites` はリセットされる（`campaign.py`）。

## 確認（ローカル）

- `tests/test_hanjuku_season_site_markers.py`（新規8件）:
  2026-10-30 の実測フィクスチャは4地点とも「未撃破」、旗を描いた地点だけ
  「撃破済み」、カーソル下は読まない、2回一致で確定、ドクロに戻れば再開、
  全4地点撃破の読みは拒否、オーナー指摘の3地点だけが閉じる。
- `test_hanjuku_season_king_sites.py` / `test_hanjuku_completed_season_sites.py` /
  `test_hanjuku_season_site_general.py` / `test_hanjuku_season_site_card_kit.py` /
  `test_hanjuku_layers.py` / `test_hanjuku_chart_bot.py` ほか → passed
  （関連 570 件）。

## 本番反映後に実測すること

- `hanjuku_decisions.jsonl` に `season_site_closed`（北西・北東・南西）が出ること。
- そのあと `order_start` が3地点に出ず、南東（ドクロ）へ向かうこと。
- 誤読時は `season_site_open` で戻ること。`season_sites_all_closed_refused` が
  出続ける場合はマーカー位置の校正をやり直す。
- テストの成功を実機の成立とは扱わない。
