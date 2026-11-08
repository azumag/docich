# 半熟英雄: きりふだ一覧の探索上限が8回で、下段の札を毎回取りこぼす（v525、2026-11-08）

## 症状

出撃の「きりふだセレクト」で、計画した札が4行の窓の下にあると
`card_scroll` を8回で打ち切り、`situation_held` を5回繰り返してから
その札を `card_dropped` で捨てていた。捨てた札には
エンジェリン（ID 22）・マグネガキン（27）・ファバード（31）が並び、
いずれもボスダメージ列の強い札（マグネガキン80 / ファバード100）だった。

## 実測（g1163、第4話、2026-11-08 23:32・23:55-23:56 JST）

- 23:32:38〜47 と 23:55:59〜23:56:08 の `card_scroll` は 8回で止まり、
  最後に見えていた行は「カルゲンジー(6) / ピッグローラー(7) /
  ラピニアール(8) / ノリウツール(11)」だった。
- 一覧は札ID昇順で、その下に クースカン(13)・ゼンマイン(15)・ミックミー(17)・
  エンジェリン(22)・マグネガキン(27)・ファバード(31) が続く。
  23:56:15 の `card_dropped` では窓が進んで
  「ピッグローラー / ラピニアール / ノリウツール / クースカン(6)」が見えており、
  その下の札は読めていない。
- 同じ出撃の決定ログ（`hanjuku_decisions.previous.jsonl` + `hanjuku_decisions.jsonl`、
  23:14〜00:47 の1784判断）で:
  - `card_scroll` 196件（`scrolls` の最大10、8以上が19回）
  - `card_select` の `situation_held` 68件（同時刻の `unknown` 52件より多い）
  - `card_dropped` 16件（マグネガキン8・エンジェリン7・ファバード1）

## 原因

`CARD_SCROLL_LIMIT = 8` が「窓の下を見る下入力の回数」ではなく
「下入力の総数」に効いていた。カーソルは4行のうち1行にあり、窓を1行送るには
まずカーソルを4行目へ移す必要がある（最大3回）。そのため8回では
実質4行しか窓が進まず、当時の所持15種（ID 1〜31）の下段に届かなかった。

窓が下端に達したことは `_lower_card_boundary`（同じ行のまま下入力2回）で
分かるので、上限は「札カタログ全体」で足りる。札ID順の一覧なので、
目的のIDを通り過ぎた時点で `_card_proven_absent` が在庫なしを確定する。

## 修正（v525 `hanjuku-chart-v525-card-scroll-catalogue`）

- `strategy/kit.py`: `CARD_SCROLL_LIMIT = len(reference.ALL_CARD_IDS)`（32）。
  レア札探索の `RARE_SCAN_LIMIT` と同じ「カタログ全体」の上限にそろえた。
  下端の2回一致・ID境界・`CARD_MISS_LIMIT` の既存の打ち切りは変えない。
- `ops/vm_actions/collect_diagnostics.py`: 診断の `bot_version` 許可リストへ v525 を追加。
- 回帰テスト `tests/test_hanjuku_card_scroll_catalogue.py`:
  - 実際の15行の並びで、8回を超えて下へ進み エンジェリン を選ぶこと
  - 一覧の末尾にも無い札は、カタログ長＋読み取り上限の範囲で `card_dropped` になること
  - 上限がカタログ全体を覆うこと

## 確認（ローカル）

- `python3 -m pytest -q tests/test_hanjuku_card_scroll_catalogue.py` → 3 passed
- `tests/test_hanjuku_sortie_evidence.py` / `test_hanjuku_boss_card_proven_absent.py` /
  `test_hanjuku_boss_card_hold_deadline.py` / `test_hanjuku_boss_card_rebuy.py` /
  `test_hanjuku_cattlemyu.py` / `test_hanjuku_no_input_gate.py` /
  `test_hanjuku_hotload.py` / `test_hanjuku_overwhelming_egg_conservation.py` → passed

## 本番反映後に実測すること

- `hanjuku_decisions.jsonl` の `card_scroll` が、目的の札が見つかるまで
  8回を超えて続き、その直後に `card_pick` になること。
- エンジェリン/マグネガキン/ファバードの `card_dropped` が減ること。
- `card_select` の `situation_held` が同じ時間帯で減ること。
- まだ札を選べない実画面がある場合（OCRの行欠け等）は、`card_scroll` の
  行が進まないことを根拠に別Issueへ分ける。テストの成功を実機の成立とは扱わない。
