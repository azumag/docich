# 召喚獣戦コマンド箱のスクロールを2押しずつまとめる (v1348)

## 症状

守備側の召喚獣戦で、自軍の卵・切り札が灰色のとき「おくのて」行は
たまごをつかう（灰色）の下に隠れており、3〜4 回下げないと読めない。
従来は 1 観測につき 1 回しか down を送らなかったため、おくのて を選ぶ
たびに 3〜4 観測（1 観測 1.5 秒で 4.5〜6 秒）をスクロールだけに使っていた。

## 実測 (g1288, 2026-11-22 02:09–02:11)

ドロー1回の周期は次の 13 観測だった。

| 時刻 | screen | 決定 |
|---|---|---|
| 02:09:36 | egg_battle_menu | `battle_okunote_combat_return` |
| 02:09:36–41 | egg_battle_menu | `battle_okunote_summon_scroll` × 3 |
| 02:09:41 | battle_menu | `battle_okunote_summon_unconfirmed` |
| 02:09:44 | battle_menu | `battle_okunote_summon_preempt` |
| 02:09:45–47 | okunote_menu | 選択 A |
| 02:09:48–55 | text | 告知ページの自動送り × 6 |

24 時間の決定内訳でも `battle_okunote_summon_scroll` が 68 回で最多だった
（`battle_okunote_select` 42 回 = ドローごとに 4 回スクロールしていた）。

## 変更

- `SUMMON_SCROLL_BATCH = 2` を追加し、有界スクロールの 1 観測で 2 回まで
  down をまとめて送る。残りが 1 回なら 1 回に縮める（上限を超えない）。
- 次の観測でラベルを読み直し、おくのて が見えていれば既存の
  `_menu_move_batch` で合わせる。**未読行の確定はしない**という既存の
  不変条件は変えない。
- 1 ドローあたりのスクロール観測が 4 → 2 になる（約 3 秒/ドロー短縮）。

## 変えないこと

- `DEFENDER_SUMMON_SCROLL_LIMIT = 4`（有界性）と、おくのて 以外を A で
  確定しない規則。
- B を使わないこと（この箱では B が効かない）。

## 検証

- `tests/test_hanjuku_summon_preempt.py`（38 passed。2 押しの記録・残り 1 回で
  縮むこと・上限 4 に達したら旧来の応戦へ戻ることを確認）。
- `test_hanjuku_summon_box_pace.py`, `test_hanjuku_generic_summon_stall.py`,
  `test_hanjuku_okunote_only_box.py`, `test_hanjuku_okunote_redraw.py` と合わせて 67 passed。
