# 半熟英雄: 帰還Yジャンプの目的セル未解決でGカーソルが止まる (2026-11-24)

## 事象

ライブ `g1342-1fe0734c` の ch4 8-1、野営の **リーキ / キッシュ** を帰還させる
「いどう → 全体マップ（Y）」で、Yビューを開いたあとGカーソルが動かないまま
16 秒以上入力が送られなかった。

実測（`hanjuku_decisions.jsonl` / tmux の agent 出力）:

| 時刻 (JST) | できごと |
| --- | --- |
| 22:14:01 | `map_target` で `y_jump_open RECALL`（cursor null） |
| 22:14:01-02 | 次フレーム `unknown`、`y_jump_unknown_hold` |
| 22:14:32 | `defense_started`（入力ゼロのまま防衛が前面へ） |

agent ログには同じ例外が連続していた。

```
docich: 警告: brain が異常終了しました (hanjuku-hero, code=2):
hanjuku-bot: invalid observation TypeError('cannot unpack non-iterable NoneType object')
at navigation.py:642:_y_jump_step < map.py:580:world_map_step < ...
```

## 原因

- ch4 は通常城をすべて確保すると、ボスが4つの「四季王城」に分かれる。
  `chart.boss_castle(4)` は全章共通のラベル `'ボス'` を返すが、ch4 の
  `CASTLES` に `'ボス'` は無い（セル未定義）。
- 月次の `boss_move` タスクが goal を持たないため、`_boss_move_dest_step` が
  `chart.boss_castle(4) == 'ボス'` を goal にし、Yジャンプを開いていた。
- `_y_jump_step` は `gx, gy = boss_probe_cell(...)` を無条件に展開していた。
  `boss_probe_cell` は None を返し、毎観測 TypeError → brain が exit 2 →
  agent は入力を送らない。画面上は「Gカーソルが停止」に見える。

## 修正

1. `os_layer/navigation.py`: `_jump_goal_cell` で目的セルを検証し、未解決なら
   決定せず Y で閉じ、`y_jump_failed`（`resulting_event=goal_unresolved`）を
   記録する。`goal_cell` の None も同様に扱う（防御）。
2. `flows/post_month_status.py`: `boss_move` タスクに、月次名簿マーカーが実測
   した `castle_candidate`（実在セル）を `goal` として持たせる。実測サイトが
   無い ch4 ではボス至近の野営免除を維持し、`'ボス'` へのYジャンプを発行しない。
3. `flows/recall.py`: `_boss_move_goal` が goal を実在セルへ解決する。月次 goal が
   無い場合も、測定済みの `camp_world`/`origin` から最寄りのボスサイトを選ぶ。
   測定点が無ければ発明せず、既存の `boss_lost` 有限中断へ送る。

## 再発時の自動 issue 起票

`hanjuku/fault_issue.py` を追加し、既存の月次監査の起票機構
（`month_audit.record_incident` / `spawn_issue_filer` / `file_issue` の
fingerprint 重複排除・occurrence コメント・secret scan・`auto-generated`
ラベル）をそのまま再利用する。

- 同じ brain 例外署名（例外クラス + 最内フレーム）が 30 分以内に 3 回で
  1 件起票。以後は 6 時間のクールダウン。
- 帰還Yジャンプの `y_jump_failed`（`chart_step=RECALL`）も同じカウンタで
  `y_jump_stall` として起票する。
- カウンタは runtime の `hanjuku_faults.json`（24 署名まで、atomic write）。
  ゲーム入力・方針・再試行には影響しない。gh は従来どおり detached。

## 検証

- `tests/test_hanjuku_yjump_goal_resolution.py`: 未解決 goal で例外を出さず
  Y で閉じること、実在 ch4 サイトでは操舵を続けること。
- `tests/test_hanjuku_boss_move_goal_ch4.py`: 月次タスクが実測サイトを運ぶこと、
  未測定時に `'ボス'` を発行しないこと。
- `tests/test_hanjuku_fault_issue.py`: 3 回で 1 件、4 回目は増えない、クール
  ダウン後の再発、窓外は新しいエピソード、Yジャンプ署名、ファイル上限、
  既存 filer のラベル/本文。
- `tests/test_hanjuku_bot_exit_diagnostic.py`: brain 例外と `y_jump_failed` が
  再発カウンタへ渡ること。

## 未確認・残件

- 実際の ch4 ボス至近野営で、月次マーカーが4サイトのどれかを返すことと、
  そのサイトへのYジャンプが着地・確定することは、配備後の実観測で確認する。
- テスト成功は実機の帰還成功やGカーソルの移動を保証しない。
