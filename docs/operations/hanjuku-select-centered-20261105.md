# SELECT直後の現在地を確定する（v429、2026-11-05）

## 症状（実測）

policy の状態が **`uncertain: true` / `anchor: null` / `nav_last: null`** のまま
数時間動かず、現在地を確定できない状態が続いていました。

- `select_focus` は一定間隔で SELECT を押して主人公の位置へフォーカスするだけで、
  その結果を現在地に使っていませんでした（`uncertain` を立てるだけ）。
- 屋根アンカーが見つからない状況では、位置が確定せずナビゲーションが進みません。

## 変更

- `flows/map._apply_select_centered(mem)` を追加し、`map_step` の先頭で呼ぶ。
  SELECT を押した次の観測で、主人公の城（`_hero_castle`）のセルを現在地として採用し、
  `uncertain` を解除して `anchor` に設定する（`select_centered` を記録）。
- `select_focus` は SELECT を押した tick を `select_centered` に残す。

## 検証

- `tests/test_hanjuku_select_centered.py`（新規2件）: SELECT直後は主人公の城が現在地になる／
  押していなければ何も変えない。
- 関連（`chart_bot` / 層）336 passed。

## 未確認・残件

- 実機で `uncertain` が解消し、オケアナへ歩き出せるかは未確認。
- 主人公が行軍中（`_hero_castle` が None）のときは何もしない。
- 第6話ボス城への入城は依然として未実測。
