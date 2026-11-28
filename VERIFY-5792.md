# VERIFY-5792 — [半熟英雄] 野営放置: ch3 ヴィーナス (6ヶ月)

- 対象: azumag/docich#5792。kind/signature `camp_abandoned` / confidence `high`。
- ラン: `g1373-b4c4126f` / generation 1373 / `hanjuku-chart-v1591-clear-sortie-launch-latch`。
- 観測月: ch3 7-7（観測 166 回 / 241.2 秒 / max gap 2.2 秒）。現行 worktree の `BOT_VERSION` は `hanjuku-chart-v1597-chikujou-defer-reason`。
- issue 本文は公開 GitHub ページから読んだ（`gh` は実行しない指示どおり不使用）。本番 decision log はローテーション済み前提で参照していない。
- `DOCICH_HANDOFF_PATH` の確認を含む shell 実行が拒否されたため、handoff 正本は未読扱い。
- 本ファイル作成以外のコード変更・git 書き込み・PR 化はしていない。

## 結論: **(d) 判定不能**

ただし切り分けはかなり絞れる。

1. **現在野営していること自体は誤検知ではない。** ヴィーナスの本人状態は `camp_observed: true`、`HP 82/82`、卵 `スーパーエッグ`、`壊れ: false`、状態文「キャンプしています」で、監査側の `_camp_generals()` の集計条件（`location=='camp'`、`location_observed` が false でない、HP>0）を満たす。ただし `months=6` が実際に6ヶ月連続の同一野営だったかは、後述する `camp_status_seen` の古い first_tick 再利用問題により未証明である。
2. **設計上の野営放置 (b) でもない。** ch3 は ch9 corridor 機械行軍の「この章はキャンプ放置」経路ではなく、ch10 White House 経路でもない。城候補 `グリン` は ch3 の通常城で、ボス城 `ボス` ではないため、boss siege 免除・boss_move 免除にも入らない。HP は満タン、卵も残っており、戦力温存や破損修理待ちの根拠もない。現行設計では、月次処理がヴィーナスを通常の `recall` タスクへ積むべき対象である。
3. 一方で、**6ヶ月残った具体的な失敗段階は issue 本文だけでは一意に決められない。** `flow.recall_verification` は `await_dispatch / dispatched / steps=1 / reads=2` で、少なくとも何らかの帰還 dispatch が受理された形跡がある。しかし month_audit の flow 投影は `general`・`goal`・`camp`・tick を落とすため、それがヴィーナス本人の帰還だったか、いつの試行だったかを証明できない。ローテーション済みの decision log がないため、過去 6ヶ月の各月で「recall が積まれなかった」「積まれたが月末・戦闘・画面遷移で defer/interrupt された」「dispatch されたが到着しなかった」「到着後に再出撃して再度野営した」「実際は一度帰還したのに古い camp episode の first_tick が再利用された」のどれだったかを確定できない。

したがって、意図的な放置ではないことはかなり確実だが、実欠陥・監査の継続月数カウント誤り・有界な再試行の反復を区別する証拠がなく、結論は **(d) 判定不能** とする。

## 1. fingerprint / occurrence の式照合

現行 main `src/docich/hanjuku/month_audit.py` の camp_abandoned 固有の実装:

- `_build_camp_abandon_incident()` L1856-L1858:
  `incident_id = hashlib.sha256(f"{runtime_id}|{generation}|camp_abandoned|{general}|{first_tick}".encode("utf-8")).hexdigest()[:20]`
- 同 L1860-L1861 と `_fingerprint()` L3543-L3545:
  `fingerprint = hashlib.sha256(f"{chapter}|camp_abandoned|camp_abandoned".encode("utf-8")).hexdigest()[:16]`

本件の入力値:

| 項目 | raw 入力 | issue 値 | 再計算 |
|---|---|---|---|
| fingerprint | `3|camp_abandoned|camp_abandoned` | `b1d827c085ec56a2` | **未計算** |
| occurrence / incident_id | `g1373-b4c4126f|1373|camp_abandoned|ヴィーナス|40864` | `c3ee6606b40f504dc7b5` | **未計算** |

補足:

- fingerprint の `chapter` は JSON 本文どおり数値の `3` であり、文字列 `ch3` ではない。
- incident_id に入る `first_tick` は camp_stay の初回野営 tick `40864` であり、issue key `b1d827c085ec56a2/7-7/43878` の `43878`（month_summary の当月 first_tick）ではない。
- `python3` と `sha256sum` の実行はいずれも環境で拒否された。独立したハッシュ再計算は未実施なので、一致確認は検品ゲート側で再計算してほしい。式と入力値は上記のとおり特定済み。

## 2. 現行 main での通常 recall 経路

ヴィーナスの条件では、設計上は次の経路を通る。

1. 月次処理 `post_month_status.arm()` が将軍状態を読む。キャンプ行が古ければ `CAMP_ROW_STALE_TICKS=600` で再読対象になる。
2. 完全スキャン完了時 `_complete_scan()` L1469-L1473 が `_tasks()` を呼ぶ。
3. `_tasks()` L239-L282 は `location == 'camp'` の将軍を通常 `{'kind': 'recall'}` へ積む。例外は次のとおりだが、本件には該当しない:
   - `camp_stays`（移動ボスフェーズ。実質 ch9 限定）
   - `road`（ch9 のボス前機械行軍。オーナー指示で野営放置）
   - boss castle に隣接した siege camp 免除 / boss_move。`グリン` は `CASTLE_NAMES[3]` の通常城で、boss 名ではない
   - ch10 White House のボス行軍残骸
4. `process` → `task_menu` → `task_select` → `task_jump` で本人を SELECT し、画面の自軍テントまたはカーソル下のテントを確認すると、`flow.recall` を `task_recall: True` で作成する（`post_month_status.py` L2325-L2407）。
5. `_recall_goal()`（`strategy/orders.py` L3565-L3666）は測定済み camp world / origin から最寄りの自軍城を選ぶ。位置を束縛できなくても `task_recall` なら本城または少なくとも 1 つの自軍城へ fallback する。
6. dispatch が受理されると `recall_verification.status='dispatched'` となり、`_remember_camp_verify()` が当時野営中の将軍一覧を `camp_verify` に保持する。600 tick 後の再読でも野営なら `camp_recall_arrival_timeout` を記録し、`recall_goal_failures` を増やす（`recall.py` L1200-L1282, L3006-L3023）。

## 3. recall が発火しない・遅れる主な設計条件

現行コードで recall が始まらない、または翌月へ送られる条件は次のとおり。

- **章・免除条件**: ch9 の road / boss phase、ch10 White House、boss 隣接 siege camp。本件の ch3・通常城 `グリン` では不成立。
- **月末保護**: `_month_end_soon()` は推定月末 200 tick 以内の新規 recall を始めない。monthly task 側でも `recall_deferred_month_end` として次月へ送る（`post_month_status.py` L2364-L2372）。これが毎月 deterministically に繰り返される可能性はあるが、本件の月 tick 長や recall 開始位置が分からないため断定できない。
- **テント未確認**: SELECT 後にテントもカーソルも読めない、または SELECT 中心とテントが 12 px より遠い場合は `camp_cursor_unobserved` / `selected_general_camp_unconfirmed` で終了する。
- **前景割り込み**: 戦闘・イベントが帰還を中断すると、`RECALL_TASK_RETRIES=2` と `RECALL_INTERRUPT_GRACE=240` の範囲でだけ同月再キューされる（L2503-L2553）。
- **到着失敗**: dispatch 受理後に目的地へ入らなければ、600 tick 後の `camp_recall_arrival_timeout` が goal failure を記録する。それでも再出撃・再野営や地形失敗が続けば、監査の月次計数は増え続けうる。
- **月次 scan 自体の不成立・skip**: メニューが開かない月が続くと arm skip がある。ただし本件の当月は `post_month_status_complete` まで到達しているため、少なくとも当月の scan は完走した。

## 4. issue 本文から確定できる事実

### 4.1 当月の scan は完走しているが、recall 実行前に issue が切られている

issue JSON の最後の decision は `post_month_status_complete`（1795818818.8543）で、incident `created_at` は 1795818818.8664944。差は約 0.012 秒で、当月の `process` ステージが recall タスクを取り出す前に監査起票されている。

したがって、直前 decision 窓に `camp_recall_*` がないことは「当月 recall が動かなかった」証拠にならない。当月はまだ recall 実行前である。

### 4.2 `monthly_scan_hold` と出撃 0 は、この短い観測窓では因果証明にならない

- 当月は first tick 43878 → last tick 44043 の 165 tick / 241 秒しか経過していない。incident は月の完走後ではなく、月途中に camp threshold を検出した時点で作られている。
- その短い窓の大部分を月次 roster scan が使い、`post_month_status_complete` で終わった。`monthly_scan_hold` シグネチャはこの事実と一致する。
- 出撃 確定 0 / 発射 0 / order_start 0 も同じ窓の値であり、月次 scan が map を持っていたことだけで説明できる。加えて issue 時点の world には `en_route` の行軍が 3 件残っており、plan にも pending が複数ある。シグネチャに `no_orders` や `no_source` がないことも、単純な「出撃元ゼロ」ではないことを示す。
- よって「月次処理の占有 → order_start 0 → ヴィーナスが戻せない」という単線の因果は証明できない。むしろ月次 scan は recall を積むための正規経路でもある。

### 4.3 `flow.recall_verification` はあるが本人帰属できない

`flow.recall_verification` は `status: dispatched` で、少なくとも何らかの camp recall が picker close まで進んだことがある。ただし `month_audit._summarize_flow()` は汎用ループで `stage/kind/status/result/attempts/reads/steps` だけを残し、`general`・`goal`・`camp`・tick を落とす。したがって:

- ヴィーナス本人の試行かどうか分からない。
- いつの試行か分からない。
- dispatch 後に到着しなかったのか、到着後に再出撃して再び野営したのか分からない。

### 4.4 現在の滞留は設計上の例外ではない

ch3・通常城 `グリン` 隣・HP full・super egg・broken false で、野営を維持する意図的な免除は見つからない。現行設計どおりなら monthly recall の対象であり、6ヶ月残っているのは「正常な意図状態」ではない。ただし、その原因がコード欠陥なのか、地形・月末・戦闘割り込みへの有界な再試行が重なった結果なのかはログなしでは確定できない。

### 4.5 監査の `months=6` には古い camp episode を再利用する過大計数経路がある

現行実装では、`months` は必ずしも「6ヶ月連続して每月の再読で camp だった」ことを直接証明しない。

- `camp_recheck.observe_camp_status()` は actor を `camp_status_seen` に一度登録すると、以後の camp 再読では `if actor in actors: return` となり、first_tick を更新しない（`camp_recheck.py` L383-L386）。
- 城にいる行を読んだ場合は `castle_tick` を更新するだけで、episode を消さない（L367-L372）。actor entry を通常どおり消すのは、行軍への新しい出発を実観測した `observe_departure()` である（L260-L281）。
- 監査側 `_camp_age_months()` は `camp_status_seen` の古い `tick` から過去の month start を数える（`month_audit.py` L1762-L1771）。camp_watch entry がない月に新規作成されると、この age をそのまま初期月数へ使う（L2931-L2941）。

このため、仮にヴィーナスが一度城へ戻り、その後の行軍観測を取りこぼして再び野営した場合でも、最初の野営 tick `40864` から遡って `months` が大きく初期化されうる。issue JSON には `camp_status_seen` の `castle_tick` や episode 更新履歴がなく、この経路を実際に踏んだかは判定できない。したがって「現在 camp」は確かでも、「同一野営が6ヶ月継続」は未証明である。

## 5. #5789（ch10 グレイ 3ヶ月 / ラン g1190）との異同

同じ `camp_abandoned` 判定だが、機構上は別 family として扱うべきである。

| 項目 | #5789 グレイ | #5792 ヴィーナス |
|---|---|---|
| 章 | ch10 | ch3 |
| ラン | `g1190-ch1v9` / generation 1190 / v1590 | `g1373-b4c4126f` / generation 1373 / v1591 |
| 継続 | 3ヶ月 | 6ヶ月 |
| 城候補 | `ボス` | `グリン`（通常城） |
| 当月出撃 | 確定 3 / 発射 6 / order_start 5 / order_launched 6 | 全て 0（ただし scan 完走直後の短い窓） |
| シグネチャ | 野営放置 / 3ヶ月以上野営 / 戦闘が長引く | 野営放置 / 3ヶ月以上野営 / 月次処理がマップを占有 |
| 設計免除の可能性 | ch10 White House / boss 隣接 siege の条件を検討する必要がある | 通常 recall 対象。boss/ch10/ch9 の免除は不成立 |

したがって #5789 側が boss 関連の設計または boss 戦闘占有であっても、#5792 へその結論を適用できない。逆に #5792 は通常城隣の完全な戦力が戻っていない点で、より通常 recall / arrival 経路の問題に絞られる。

## 6. 未決の点（確定に必要な証拠）

本番 decision log が残っていないため、以下は未決である。

1. 過去 6ヶ月の各月で、ヴィーナスに対する `post_month_task_result`、`post_month_recall_requeued`、`camp_recall_dispatched`、`camp_recall_arrival_timeout`、`camp_recall_goal_failed` が発火したか。
2. `recall_verification.status='dispatched'` がヴィーナス本人のものか、いつ更新されたものか。
3. dispatch されていた場合、目的地はどの城で、`recall_goal_failures` がどこまで増えたか。
4. ヴィーナスが帰還後に再出撃して同じ場所へ戻ったのか、一度も帰還しなかったのか。
5. `camp_status_seen` に `castle_tick` があったか、つまり first_tick `40864` の後に一度は城内観測へ戻ったか。
6. 月末 defer / battle interrupt / camp unconfirmed / cliff escape のどの終端が繰り返されたか。

これらが分からない限り、(a) 実欠陥、(c) 継続月数の誤検知、有界な再試行が設計内で尽き続けた状態を区別できない。現行コード上は通常 recall 経路と到着 timeout 保険が存在する一方、監査の月数計算にも古い episode を引きずる経路があるため、issue 本文だけで特定コードの欠陥へ断定しない。

## 検品ゲート確認（Muse、2026-11-28 14:08 JST）

- fingerprint / occurrence を検品ゲートで再計算した。SHA-256("3|camp_abandoned|camp_abandoned")[:16] = b1d827c085ec56a2、SHA-256("g1373-b4c4126f|1373|camp_abandoned|ヴィーナス|40864")[:20] = c3ee6606b40f504dc7b5。いずれも issue の値と一致する。
- 結論 (d) 判定不能は、現在野営していること自体は誤検知ではなく、ch3・通常城グリン隣では設計上の免除も成立しない一方で、6ヶ月残った具体的な失敗段階を issue 本文だけでは一意に決められないことで裏付けられている。`camp_status_seen` の古い first_tick 再利用により `months=6` が過大計数されている可能性もあり、同一野営が6ヶ月継続したことも未証明である。#5789（ch10 グレイ・boss_move 再試行欠陥）とは機構上別 family である。
