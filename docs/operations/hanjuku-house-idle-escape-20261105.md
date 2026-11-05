# 半熟英雄: 家メニューの入力不要ホップに上限を置く (v399)

## 事象 (2026-11-05 実測)

`run-soren-live/runtimes/g1133-a3499a00/hanjuku_decisions.jsonl` で、
`house_input_free_hop` が 192 件 / 46.0 分 (t=1793813617.8〜1793816379.9) 出ていた。

- すべて `observed_metric = {"from": "roster_advance", "to": "roster"}` の 1 種類。
- 間隔はほぼ 1.5 秒 (= 1 観測)。
- bot_version 別の件数: v395 48 / v396 96 / v397 24 / v398 24。特定の版の退行ではない。

## 訂正 (2026-11-05, ラウンド10)

**初報の原因分析は誤りだった。** 同じ時刻の `action_plan` を突き合わせると、192 件の
ホップはすべて `down` を 1 回送る通常の名簿走査 (1 行 = 1 観測) であり、
「入力を 1 つも送らないまま観測だけを消費していた」という記述は事実ではない。
初報は、別の時間帯 (t≈1793814595) の `planned_actions: []` の行を同じ現象として
扱ってしまっていた。

決定ログには本当に入力なしの連続観測も存在する (63 分で 144 run、最長 13.4 秒、
いずれも `screen_kind: text`)。ただし中身は召喚獣メニューの描画待ち
(`monster_menu_drawing`) や読めない画面の判定保留 (`situation_held`) で、
ゲーム側のアニメーション待ちであり停止ではない。

## 変更 (残す判断)

初報の修正は「名簿走査の位相で入力なしが 10 観測続いたら閉じる」安全網として残す。
実機では 1 度も発火していない (`house_idle_escape` 0 件) が、ホップだけが進む観測を
上限で打ち切る挙動は、ログで否定できないケースに対する有界の防御になる。

- `HOUSE_IDLE_LIMIT = 10`、`HOUSE_IDLE_PHASES = {roster, roster_next, roster_advance, status}`。
- `step()` は入力を出さない観測を数え、名簿走査の位相で `screen.kind == "text"` のまま
  10 回続いたら `house_idle_escape` を記録し、`leave_roster` へ移して `b` を 1 回送る。
  読めた将軍 (`seen`) と未完了の印はそのまま残すので、既存の再走査上限で再試行される。
- 入力が出た観測・位相が名簿走査でない観測・テキスト以外の画面ではカウンタをリセットする。
  `open_roster` の age 3/6 再試行や travel は「マップ画面での正当な待ち」なので数えない。
- 記録項目: `observations`, `phases` (直近 4 位相), `screen`, `seen`。

## 検証

- `tests/test_hanjuku_house_idle_escape.py` (新規): 10 回で `b` を出して `leave_roster` へ移る、
  入力でカウンタが消える、読めた将軍が残る。
- `tests/test_hanjuku_house.py` / `test_hanjuku_issue_2367.py` / `test_hanjuku_fort_camp.py` /
  `test_hanjuku_goals.py` と併せて 191 passed (ローカル)。
- 実機: v399 稼働中の `house_idle_escape` は 0 件 (上記のとおり通常の名簿走査だったため)。

## 残件

- 入力なしの連続観測のうち、召喚獣メニュー描画待ち以外のものは未分類。
- 第 6 章ボスセル (818,184) と第 7〜12 章のボスセルは未検証のまま。
