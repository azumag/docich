# VERIFY-5795 — 築城可能なまま月一終了: ch3 7-10

検証日: 2026-11-28  
対象: azumag/docich#5795  
検証基準: 現行 main `108524742`（`hanjuku-chart-v1599-boss-move-retry`）。対象ランは `g1373-b4c4126f` / generation 1373 / `hanjuku-chart-v1593-unreadable-card-source`。本番 decision log はローテーション済みなので、issue 本文と現行コードのみで検証した。

## 結論

**(b) 設計どおり — 正当な発射確認待ちの enemy-directed retake が確認窓内にあり、築城探索の打ち切りは現行の意図どおり**

監査が見た事実（未試行かつ在城の候補ドンダム・パタゴーヌを残して月終了）は正しく、(c) 誤検知ではない。ただし月末の世界状態に、失城メガパタへの `purpose=retake` / `status=en_route` / `age_ticks=116` の live sortie `A:8ead60a1:Mfe0c7d` がある。`LAUNCH_PROBE_ROSTER_TICKS = 200` なので発射確認窓内であり、`chikujou_deferred_sortie` は month_done より約 9.0 秒前なので、打ち切り時点ではさらに若い。したがって #5764 と同じく、正当な「出撃開始から発射確認まで」への優先退避と判断できる。

`chikujou_deferred_sortie.observed_metric.step` は issue 本文の決定表投影に出ず、どの step が latch だったかは一意に特定できない。ただし本件には窓内の enemy-directed live sortie が独立証拠としてあるため、#5757/#5761/#5768 のような (d) には留めない。stale latch を裏付ける lost/settled 直後の age や解決済み出撃だけの状態は見えない。

## 1. fingerprint / occurrence の再計算

現行 `src/docich/hanjuku/month_audit.py` の chikujou 系 builder では、`_build_chikujou_incident` が incident_id を次式で作る（同ファイル 2277-2279 行）:

```text
sha256(f"{runtime_id}|{generation}|{kind}|{event.month}|{chapter}")[:20]
```

入力:

```text
runtime_id = g1373-b4c4126f
generation = 1373
kind       = chikujou_available_at_end
event.month= 7-10
chapter    = 3
```

連結文字列:

```text
g1373-b4c4126f|1373|chikujou_available_at_end|7-10|3
```

再計算結果:

```text
53a860bbf932f2030617
```

issue 本文の occurrence `53a860bbf932f2030617` と一致した。

fingerprint は `_fingerprint`（同ファイル 3543-3545 行）で、builder からは `_fingerprint(chapter, kind, [kind])` として作られる（2281 行）:

```text
sha256(f"{chapter}|{kind}|{'+'.join([kind])}")[:16]
```

連結文字列:

```text
3|chikujou_available_at_end|chikujou_available_at_end
```

再計算結果:

```text
34457dccccb2faf2
```

issue 本文の fingerprint `34457dccccb2faf2` と一致した。`month_summary.first_tick = 45585` なので、重複抑制 key `34457dccccb2faf2/7-10/45585` も本文と一致する。

## 2. 現行コードの終了経路

### 2(a). `chikujou_scroll` 後に未試行城が画面外のまま `chikujou_deferred_sortie` へ進む条件

`_month_chikujou` は当月未試行の候補 `names` が画面行から選べないとき `_chikujou_scroll(screen, mem, sub, names)` へ進む（`src/docich/hanjuku/flows/month.py` 1928-1948 行）。つまりドンダム・パタゴーヌのような未試行の所有城が 4 行一覧の外に残る状態そのものが scroll 経路である。

`_chikujou_scroll` では現行コードが次の条件で打ち切る（同 1484-1506 行）:

1. `active_sortie_launch_priority(mem)` が step を返す。
2. scroll 状態の `presses >= 1`（少なくとも 1 回一覧を送った後）。
3. 同月の `chikujou_defer.count < CHIKUJOU_DEFER_LIMIT`（上限 2、同 1411 行）。

このとき `sub['declined'] = True`、`sub['aborted'] = True` にし、`observed_metric` へ visible rows、scrolls、latch step、deferrals を入れて `chikujou_deferred_sortie` を記録し、B を返す。本件の決定列は `chikujou_scroll` 1 回の直後に `chikujou_deferred_sortie` なので、この分岐と一致する。deferral は 1 回だけなので、上限 2 は効かない。

### 2(b). deferral 後に `month_done` へ至る経路

`_finish_month_sub` は chikujou sub の終了時、verified upgrade がなく、`declined` かつ支払いなしなら、`aborted` なので `more=False` とし、`shop['chikujou'] = 'done' if upgrades else 'skipped'` にする（同 2190-2219 行）。本件は増築済み 0 件なので `skipped` になる。その後 month menu の終端で `_note_chikujou_skip_unexplained` を呼び、`month_done` を記録する（同 817 行、855-927 行）。

`active_sortie_launch_priority` は、latch を単なる永続フラグとしては信用せず、plan active、未中断の `sortie_attempt`、`departure_request`、または live sortie と一致する launch probe がある場合だけ step を返す（`src/docich/hanjuku/strategy/orders.py` 1120-1166 行）。#5765 で問題になった `interrupted` attempt は現行では in-flight 扱いしない。

### 2(c). `chikujou_skip_unexplained` の意味

対象ラン v1593 では、`chikujou_skip_unexplained` は「拒否・保留・読取不可・予約不足の記録が無い」という狭い意味であり、出撃デファーを説明済み終端として区別していなかった。したがって本件の `chikujou_skip_unexplained` は、原因が本当に無かった証拠ではなく、当時の診断分類が deferral を unexplained 側へ入れていたことを意味する。

現行 main では v1597 / PR #5806 により、同月の `chikujou_deferred_sortie` または同月の deferral 状態があれば `chikujou_skip_after_deferred_sortie` を記録し、`chikujou_skip_unexplained` にはしない（`flows/month.py` 899-911 行）。この現行差分は説明性の修正であり、築城を中断して発射確認へ譲る本体の設計は変えていない。

### 2(d). 増築済み 0 件の意味

#5768 は当月 2 件の増築済みがあり、終端で `shop_status=done` になる経路だった。本件は `upgrades=[]` なので同じ経路は当てはまらず、deferral による aborted/declined 終了の結果 `shop_status=skipped` になる。これは現行コードどおりであり、done/skipped の違いは築城実施件数の違いで説明できる。

## 3. 世界状態と latch 正当性

issue 本文の世界状態で enemy-directed かつ確認窓内なのは次の 1 件である。

```text
A:8ead60a1:Mfe0c7d
purpose = retake
source  = アルマムーン
target  = メガパタ
status  = en_route
age     = 116 tick
```

メガパタは失城一覧にあるため、retake は enemy-directed である。`LAUNCH_PRIORITY_PURPOSES` にも `retake` が含まれる（`strategy/orders.py` 1054 行）。`LAUNCH_PROBE_ROSTER_TICKS = 200`（`flows/map.py` 3124 行）に対し 116 tick は窓内であり、deferral は month_done より前なので打ち切り時点では 116 tick 未満だった。

他の候補は優先退避の独立証拠にならない:

- `A:a13393fe:Mb6017f` は `launched_unconfirmed` だが target パタゴーヌは奪取済みで、本文上 purpose は null / launched_orders では move。enemy-directed ではない。
- `A:a13393fe:Mdd0c77` は age 190 tick だが purpose move / target イーピヨルなので enemy-directed ではない。
- `A:74343789:M875e93` は attack だが `overdue` / age 984 tick で確認窓外。
- `A:a13393fe:M47d4bf` は attack ドンダムだが sortie_history が tick 45421 に won、月末には live sortie として残っていない。

したがって、正当な発射確認待ちの独立証拠はある。逆に、解決済み出撃だけが残る stale latch の構造は本文からは確認できない。`order_start/order_launched` が trailing 120 秒で 0 なのは、対象 retake が月の外側で開始された可能性と整合し、stale の直接証拠ではない。

## 4. 同型 issue との異同

| issue | 従来結論 | 本件との異同 |
|---|---|---|
| #5768 | (d) 判定不能 | 同じく scroll 1 回後に deferral。ただし #5768 は age 621/1260/2109 tick 級で窓内証拠がなく、当月 2 件増築で status done。本件は retake age 116 tick の窓内証拠があり、増築 0 件で status skipped。 |
| #5757 | (d) 判定不能 | #5757 は enemy-directed live sortie がなく、pre-commit sortie の可能性を排除できなかった。本件は retake/en_route/116 tick が本文にある。 |
| #5761 | (d) 判定不能 | #5761 は enemy-directed live sortie が age 924/1050 tick で窓外。本件は 116 tick で窓内。 |
| #5764 | (b) 設計どおり | 最も近い。#5764 は enemy attack age 145/190 tick が独立証拠。本件は enemy-directed retake age 116 tick が独立証拠で、同じ (b) の構造。 |
| #5765 | (a) 実欠陥 | #5765 は当時残っていた decision log により latch step `A:59dc871a:Md3216a` が 190 tick 前に lost 済みと特定できた。本件はそのような lost/settled 証拠がなく、反対に live retake がある。さらに #5765 の interrupted attempt を in-flight 扱いする欠陥は PR #5772 / v1587 で修正済みで、本件は v1593 の発生。 |

## 5. 残る限界

- issue 本文の決定表投影には `observed_metric` がなく、`chikujou_deferred_sortie` が返した latch step を一意には特定できない。表の `step` 列は chart_step であり latch step ではない。
- ラン `g1373-b4c4126f` の本番 decision log は残っていないため、latch step と `A:8ead60a1:Mfe0c7d` の同一性は直接照合できない。
- それでも、窓内の enemy-directed live retake が存在する以上、優先退避そのものは正当であり、(d) ではなく (b) と判断した。

## 検証範囲

read-only 検証のみ。コード修正なし、VERIFY だけの PR は作成しない。
