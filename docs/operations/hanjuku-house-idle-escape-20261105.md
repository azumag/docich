# 半熟英雄: 家メニューで入力ゼロの観測が続く停止を打ち切る (v399)

## 事象 (2026-11-05 実測)

`run-soren-live/runtimes/g1133-a3499a00/hanjuku_decisions.jsonl` で、
`house_input_free_hop` が 192 件 / 46.0 分 (t=1793813617.8〜1793816379.9) 出ていた。

- すべて `observed_metric = {"from": "roster_advance", "to": "roster"}` の 1 種類。
- 間隔はほぼ 1.5 秒 (= 1 観測)。
- 同じ時刻の `action_plan` は `planned_actions: []`、`screen_kind: text`、
  `reason_decisions: []`。つまり**入力を 1 つも送らないまま観測だけを消費**していた。
- bot_version 別の件数: v395 48 / v396 96 / v397 24 / v398 24。特定の版の退行ではなく、
  入力不要ホップ (Issue #3194 (18)) の抜け道が残っていた。
- この間 `roster_complete: false` のままで、月次の名簿走査が完了しなかった。

原因の形は、`flows/house.py` の `step()` が「入力を伴わない位相ホップ」を
`INPUT_FREE_HOPS` の範囲で追うだけで、**ホップし続けても入力が出ない状態**を
検出する上限を持っていなかったこと。`roster_advance` → `roster` → `roster_advance` の
往復は各観測で 1 件のホップとして記録され、`step()` は `[]` を返し続けた。

## 変更

- `HOUSE_IDLE_LIMIT = 10`、`HOUSE_IDLE_PHASES = {roster, roster_next, roster_advance, status}`。
- `step()` は入力を出さない観測を数え、名簿走査の位相で `screen.kind == "text"` のまま
  10 回続いたら `house_idle_escape` を記録し、`leave_roster` へ移して `b` を 1 回送る。
  読めた将軍 (`seen`) と未完了の印はそのまま残すので、既存の再走査上限で再試行される。
- 入力が出た観測・位相が名簿走査でない観測・テキスト以外の画面ではカウンタをリセットする。
  `open_roster` の age 3/6 再試行や travel は「マップ画面での正当な待ち」なので数えない。
- 記録項目: `observations`, `phases` (直近 4 位相), `screen`, `seen`。次回はこの記録で
  どの位相が詰まったかを特定できる。

## 検証

- `tests/test_hanjuku_house_idle_escape.py` (新規): 10 回で `b` を出して `leave_roster` へ移る、
  入力でカウンタが消える、読めた将軍が残る。
- `tests/test_hanjuku_house.py` / `test_hanjuku_issue_2367.py` / `test_hanjuku_fort_camp.py` /
  `test_hanjuku_goals.py` と併せて 191 passed (ローカル)。
- 実機での効果確認は未実施。v399 が live に載った後、`house_idle_escape` の出現と
  `house_input_free_hop` の減少を次ラウンドで確認する。

## 残件

- 位相が往復する根本原因 (roster 画面が `roster()` として読めない時間帯がある) は未特定。
  `house_idle_escape` の `phases` / `screen` で次回特定する。
- 第 6 章ボスセル (818,184) と第 7〜12 章のボスセルは未検証のまま。
