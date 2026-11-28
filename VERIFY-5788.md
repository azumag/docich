# VERIFY-5788 — 帰還Yジャンプ失敗が再発: ch10 y_jump_failed:RECALL:residual

## 結論

**(a) 実欠陥**。

ただし限定がある。issue 本文だけでは「永久に停止した」ことや、3回とも residual が完全同一だったことは証明できない。実欠陥と判断したのは、現行コードが RECALL の Y-route で「同じ residual 失敗を unreachable として学ぶ既存機構」と「別城へ切り替える既存機構」の両方へ進まず、同じ pre-dispatch 失敗を再試行し続けられる構造になっているためである。オーナー指示どおり「きかんへ戻らない」こと自体は設計だが、その範囲内でも unreachable 記憶の利用・別城/別経路の選択は可能であり、現行コードにはその逃げ道がない。

## 検証範囲と制約

- 本番 decision log はローテーション済みのため参照していない。issue 本文と現行 worktree のコードだけで検証した。
- `gh` コマンド禁止の指示に従い、issue 本文は GitHub の公開ページを read-only に確認した。#5783 / #5791 も同様。
- git の書き込み操作、commit、push、PR 化作業はしていない。
- `python3` によるハッシュ再計算は承認却下された。**したがって独立した再計算は未実施**で、検品ゲートでの再計算が必要。

## fingerprint / occurrence の確認

`y_jump_stall` は `month_audit.py` の通常の月次 incident 生成ではなく、`src/docich/hanjuku/fault_issue.py` が作る。

現行コードの式:

```python
digest = hashlib.sha1(f'{kind}:{signature}'.encode('utf-8')).hexdigest()[:16]
incident_id = f'{digest}-{int(now)}-{count}'
```

今回の入力:

```text
kind      = y_jump_stall
signature = y_jump_failed:RECALL:residual
now       = 1795817876.11249   # int(now) は 1795817876
count     = 3
```

したがって期待される形は:

```text
fingerprint  = sha1("y_jump_stall:y_jump_failed:RECALL:residual")[:16]
incident_id  = <同じdigest>-1795817876-3
```

issue 本文の値:

```text
fingerprint  = 57c16f175899da3e
incident_id  = 57c16f175899da3e-1795817876-3
```

接頭辞・timestamp・count の構造は現行式と一致する。ただし `python3` 実行が却下されたため、`sha1(...)` の先頭16桁が `57c16f175899da3e` へ実際に一致するかは**未計算**。

なお `month_audit._fingerprint` は

```python
raw = f"{chapter}|{kind}|{'+'.join(signals)}"
sha256(raw)[:16]
```

だが、これは `fault_issue` が作る `y_jump_stall` には使われていない。この種別の再計算に month_audit の sha256 式を流用すると誤る。

## residual [0.0, 6.0] の意味と判定条件

`src/docich/hanjuku/os_layer/navigation.py` の `_y_jump_step` では、目的セルを Yビュー座標へ変換し、

```python
dx, dy = gx / 8 + ox - cursor[0], gy / 8 + oy - cursor[1]
```

を residual として使う。chapter 10 の offset は `(ox, oy) = (63.0, 47.5)`。通常城なら移動上限は `Y_JUMP_MOVES = 8`、最終許容は `Y_JUMP_FINAL_TOL = 3.0` ビューpx。

移動上限へ達した時点でどちらかの軸の残差が 3.0 を超えると、A で決定せず Y を閉じ、

```text
decision        = y_jump_failed
resulting_event = residual
observed_metric = {world_cursor, residual, moves}
reason          = 全体マップのカーソルが目的の城に合わないため決定せず閉じる
```

を記録する。issue の文言・`moves: 8`・`residual: [0.0, 6.0]` はこの経路と一致する。

`residual [0.0, 6.0]` の意味は、X 方向は目的ビュー座標へ一致し、Y 方向だけ目的がカーソルの 6 ビューpx下に残っていること。`world_cursor [119.5, 98.0]` なら目的ビュー座標は `[119.5, 104.0]`。

chapter 10 では RECALL の非ボス帰還でも `ch10_whitehouse_recall_leg` が掛かる。終端カーソルをそのままワールド座標へ逆算すると `(452, 404)` だが、これは初回 origin の直接証拠ではない。重要なのは、目的ビュー座標 `[119.5, 104.0]` がワールドセル `(452, 452)` に対応し、これが `ch10_whitehouse_recall_leg` の南側凹部からアルマムーン方面へ向かう分岐が返しうる中間地点と一致することである。これを Yビュー座標へ変換すると

```text
x = 452/8 + 63.0 = 119.5
y = 452/8 + 47.5 = 104.0
```

となり、issue の `[cursor + residual] = [119.5, 104.0]` と正確に一致する。つまり今回の residual はアルマムーン城セルそのものではなく、ホワイトハウス迂回の中間セルへのジャンプ失敗として読める。

`month_audit._bounded_jump_evidence` は camp incident 用の evidence を bounded 化するだけで、`attempt/active/seen/moves/wait/age/last_result` しか保持しない。`residual` や `world_cursor` を評価する場所ではない。今回の issue の `observed_metric` は `fault_issue._sample` が decision record からそのまま抜き出したもので、判定自体は navigation.py の上記条件で行われる。

## 3回再発しても復帰・再試行が進まない構造

現行コードには、同じ residual で繰り返し閉じた目標を覚える `_note_unreachable_goal` がある。2回同じ（または近い）residual なら `y_jump_unreachable` を記録し、その城への Y 経路を一定時間休止して別経路へ切り替える意図がコメントにある。

しかし RECALL 経路では次の問題がある。

1. `_y_route_step` は `screen.kind in ('map', 'world_map')` なら無条件に `_start_y_jump(..., 'RECALL', ..., 'target')` を呼ぶ。`y_jump_unreachable` を参照しない。
2. `y_jump_unreachable` を参照しているのは通常の出撃 target jump 側であり、RECALL の `_y_route_step` ではない。
3. `_y_route_step` はオーナー指示により「きかんへ戻らず Y を再試行する」。これ自体は設計だが、unreachable 判定後に別城・別経路・別の迂回避け方へ進む実装がない。
4. `RECALL:target` の budget を見る箇所は picker delegation / dest picker 側にはあるが、いったん `stage == 'y_route'` へ入ると `_y_route_step` 自身は budget や unreachable を enforcement しない。`_start_y_jump` はカウンタを増やすだけで停止しない。
5. `recall_goal_failures` / `_goal_after_failures` は「帰還Aを送った後に到着確認できなかった」post-dispatch 失敗を数える仕組みである。今回の `y_jump_failed` は A を送る前の pre-dispatch 失敗なので、この失敗カウントに入らず、別城への切替えも起きない。

このため、同じ camp・同じ goal・同じ White House 迂回点に対して、学習済み失敗を回復経路へ反映せず、同じ Yジャンプを再開できる。3回で filing されるのは設計どおりの検知だが、検知対象の再発から別経路へ進む実装がないのは実装上の欠陥である。

なお issue JSON の `decisions.before` には最後の1件しか残っていないため、3回とも residual が完全同一で `_note_unreachable_goal` の count=2 条件を必ず満たしたかは証明できない。ただし、仮に residual が揺れていても、pre-dispatch 失敗が goal failure へ繋がらず無別経路で再試行される構造は同じである。

## #5783 / #5791 との異同

#5783「キャンプ帰還未確認: ch9 キャロット」と #5791「キャンプ帰還未確認: ch10 ベーグル」は、どちらも `camp_recall_unconfirmed` で、少なくとも帰還A送信/input receipt 後の `await_dispatch` 段階に達している。`resulting_event` は `arrival_unconfirmed` で、month_audit の camp incident 経路である。

今回 #5788 は逆に、Yビュー上で目的位置へカーソルを合わせられず、A決定前に閉じる pre-dispatch の `y_jump_failed` である。集計も month_audit の `_camp_event` ではなく、`fault_issue.note_decision_faults` が `decision == 'y_jump_failed'` かつ `chart_step == 'RECALL'` を数えて `y_jump_stall` にする別経路である。したがって #5783/#5791 と同じ失敗ではなく、特に「A送信後に到着しなかった」証拠には使えない。

一方で関係もある。#5788 の失敗は post-dispatch の `arrival_unconfirmed` へ進まないため、#5783/#5791 型の到着未確認としても観測されない。帰還シーケンスのより前段で止まっている。

## 最終判定の根拠まとめ

- 誤検知ではない: issue の文言・moves・residual・RECALL は現行 `_y_jump_step` の residual 終端と正確に一致する。
- 設計どおりだけではない: 「きかん禁止・Y再試行」は設計だが、unreachable 記憶を RECALL が参照しない点、budget enforcement が y_route 本体へない点、pre-dispatch 失敗が別城切替へ繋がらない点は、再発しても回復しない実装欠陥である。
- 判定不能な残り: 本番ログ不在のため、3件すべての residual 履歴、実際に unreachable mark が作られたか、その後に同じ帰還が成功したかは不明。

## ハッシュ再計算の成否

**未計算**。理由: `python3` 実行が承認却下されたため。式と入力値は上記のとおりであり、検品ゲート側で再計算が必要。

## 検品ゲート確認（Muse、2026-11-28 13:11 JST）

- fingerprint / incident_id を検品ゲートで再計算した。SHA-1("y_jump_stall:y_jump_failed:RECALL:residual")[:16] = 57c16f175899da3e、incident_id = 57c16f175899da3e-1795817876-3。いずれも issue の値と一致する。
- 結論 (a) 実欠陥は、RECALL の `_y_route_step` が `y_jump_unreachable` を参照しないこと、y_route 段階に budget enforcement がないこと、pre-dispatch 失敗が `recall_goal_failures` へ繋がらず別城切替が起きないこと、の3点で裏付けられている。「きかんへ戻らない」こと自体は owner 指示の設計であり、本件の欠陥はその範囲内での回復経路の欠落である。
