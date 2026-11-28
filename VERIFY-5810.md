# VERIFY-5810 — ch12 サフラン野営放置（3ヶ月）

## 結論

**(d) 判定不能**。

「サフランが現在 camp である」こと自体は、HP 64/64・卵カラフルエッグ・壊れ false・状態文「キャンプしています」で監査の入力条件を満たし、誤検知とは断定できない。一方で、ch12・城候補アファニアの camp は現行設計では通常の月次 recall 対象であり、3ヶ月放置を許容する明示設計も見つからない。ただし issue 本文と現行 main のコードだけでは、(1) 本当に同一野営が3ヶ月継続したのか、(2) 過去月の月次 recall が発火しなかったのか、発火して失敗/中断/未到着だったのか、(3) 発火済みだが到着確認前なのかを一意に決められない。本番 decision log はローテーション済みのため、以上から結論は判定不能とする。

## fingerprint / occurrence の式と入力値

python3 の実行承認は2回却下されたため、本検証では sha256 を再計算していない。検品ゲートで再計算するため、現行 main の式と入力値を以下へ残す。

### fingerprint

現行 main `src/docich/hanjuku/month_audit.py`:

- `_build_camp_abandon_incident` は `_fingerprint(mem.get('chapter'), 'camp_abandoned', ['camp_abandoned'])` を使う。
- `_fingerprint(chapter, kind, signals)` は `raw = f"{chapter}|{kind}|{'+'.join(signals)}"` を UTF-8 で sha256 し、先頭16 hex を返す。

issue JSON の `chapter` は数値の `12` なので、Python の f-string 評価後の入力は:

```text
12|camp_abandoned|camp_abandoned
```

期待値（issue 本文）: `29609957117c9004`。**未計算**。

### occurrence / incident_id

`_build_camp_abandon_incident` は次の raw を UTF-8 で sha256 し、先頭20 hexを `incident_id` とする:

```python
raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
       f"camp_abandoned|{stay.get('general')}|{stay.get('first_tick')}")
```

issue JSON の値を代入した入力は:

```text
g1190-ch1v9|1190|camp_abandoned|サフラン|233254
```

期待値（issue 本文）: `da2667ad3875d28d143a`。**未計算**。

なお issue キー `29609957117c9004/18-11/233842` の末尾は camp の `first_tick` ではなく、`_month_key` が使う `month_summary.first_tick`（issue JSONでは 233842）である。key 形式は `fingerprint/month/month_summary.first_tick`。

## 現行 main での recall 発火条件

現行 main では、月次メニューの終了確認（`うむッ!`）後に `post_month_status.arm(mem)` が呼ばれる。issue の直前決定は `month_seen` と当月の購入/募集計画であり、まだ月次メニュー終了後のフィールド処理へ進んでいない。したがって 18-11 の最初の観測時点では、当月の月次処理が recall を実行する機会自体が来ていない。issue の month_summary も観測1回・0.0秒で、発火時点は月初めの `month_menu` 直後である。

`post_month_status._tasks` は `location == 'camp'` の将軍を原則 `{'kind': 'recall'}` へ積む。例外は主に次の通り。

1. ch9 moving-boss phase の `camp_stays` が true の場合、home recall しない。
2. boss城に隣接する健全な camp で、boss が解放済み、かつ免除月数が `BOSS_CAMP_EXEMPT_MONTHS = 3` 以下なら、home recall ではなく `boss_move` または siege 免除になる。
3. ch9 の road 機械行軍フェーズでは locate/recall を省略し repair のみ残る。
4. `boss_unlocked` が false の間は boss 免除/boss_move を適用せず通常 recall へ戻る。

本件は ch12 であり ch9 の例外は不適用。城候補は `アファニア` であり、boss城/四季王サイトではない。world ではアファニアを含む通常城が奪取済みで、アファニアにはアテナが在城している。したがって `_boss_camp_holds_siege` は成立せず、現行設計上は通常 recall タスクへ積まれるべき対象である。ch12 には boss staging / homeKeep3 の出撃制御があるが、これらは boss 出撃元を制約するもので、camp recall を免除する規定ではない。

recall が始まっても、現行 main には次の中断・延期経路がある。

- 戦闘・場面割り込みで recall flow が dispatch 前に失われた場合、`wait_recall` は同月内に job を再キューする。battle interrupt は `RECALL_INTERRUPT_GRACE = 240` 内なら retry budget を消費しない。それ以外は `RECALL_TASK_RETRIES = 2` まで。
- recall A の送信済み receipt がある場合は二重送信を避けて再キューしない。
- 月末が近い場合、recall は入力せず `recall_deferred_month_end` で次月へ延期される。
- 月次 scan は、敵向け出撃が未成立で開始可能な order がある間は sortie へマップを譲る。現行 main には idle yield 3回で scan を取り戻す #5000 対策があるが、戦闘が前景を取り続ける場合は scan/recall の進行自体が遅れ得る。

issue 直前の決定内訳は戦闘系が多く、signals にも `battle_grind` がある。したがって「ボス戦・防衛戦の割り込みで月次 recall が繰り返し遅れた」経路は issue と矛盾しない。しかし、どの月にどこまで recall が進んだかを示すログは残っていない。

## months=3 の信頼性に関する制約

`month_audit._track_camp_abandon` は現在の `general_statuses` で camp かつ HP>0 の将軍を数える。月数は watch entry の月またぎ加算だけでなく、`_camp_age_months` による初期化/上方補正を受ける。`_camp_age_months` は `camp_status_seen.actors[general].tick` を野営 episode の最初の tick とみなし、`month_start_tick` と `month_durations` から月境界数を数える。

`camp_status_seen` は camp status を一度見た actor を抑制台帳へ入れ、本人の新しい出発を実観測した場合などにだけ解除する。城にいることを読んだ場合は `castle_tick` を記録し、その後の行軍観測で episode を終える構造だが、行軍観測を取りこぼすと古い tick が残る。同じ actor が再び camp になっても、`actor in actors` なら tick は更新されない。したがって「現在 camp」は正しくても、古い episode の first_tick から月数を数え、同一野営の継続月数を過大計上する経路が現行 main に残っている。

本件でも `camp_stay.first_tick` は 233254 だが、`camp_stay.last_tick` は 233225 で、last が first より29 tick古い。`row.tick` と `camp_status_seen.tick` の意味は同一ではないものの、少なくとも issue 内の時系列だけでは episode の連続性を独立に裏付けられない。また `first_month` は first_tick から逆算された実際の開始月ではなく、watch entry を作った月でありうる。

## flow.recall_verification の制約

issue JSON の `flow` には `recall_verification: {stage: await_dispatch, status: dispatched, steps: 1}` がある。これは active な recall flow ではなく、recall.py が picker close と input receipt を確認した後に残す terminal receipt である。`dispatched` は「帰還Aが受理され、到着確認だけが残る」意味で、`_flow['recall']` 自体は終端処理で消える。

ただし month_audit の `_summarize_flow` は `recall_verification` の general / goal / camp / requested_tick / identity を投影しない。実装上は underlying state に general が残るが、issue JSON からは落ちている。`post_month_status` や active `recall` も issue JSON に出ていないため、これがサフラン本人の帰還だったのか、別将軍または過去月の receipt なのかを判定できない。

dispatched がサフラン本人なら、少なくとも recall は発火しており、未到着/到着未観測、または監査が古い camp status を見た可能性に分類すべきである。逆に別人なら、サフランの recall がまだ開始されていないだけかもしれない。issue 本文だけでは区別できない。

## ラン時版と現行 main

- ラン時版: `hanjuku-chart-v1595-boss-order-recovery`
- 検証した worktree の現行 main 相当: `hanjuku-chart-v1605-boss-egg-unread`

allowlist 上、v1595 の後に `v1598/v1599-boss-move-retry` が入っている。これは #5789 family（boss隣接 camp を boss へ送り直す boss_move の retry）に関する後続修正であり、城候補アファニアの本件へそのまま適用できるものではない。v1596-v1605 の全差分をここで個別 bisect したわけではないが、本検証の recall 発火条件・再キュー・month_audit の式は現行 worktree のコードを直接読んだものである。

## #5789 / #5792 との異同

### #5789（ch10 グレイ、城候補ボス、同ラン）

#5789 は ch10 White House/boss に隣接する健全な camp だった。現行設計では home recall ではなく boss_move へ再分類される明示的な例外があり、検証結論は、その唯一の解消経路 boss_move が戦闘割り込み後に再試行されない実欠陥だった。現行 main には `boss_move` も再キューする修正とテスト（`test_boss_move_task_is_requeued_within_the_bounded_budget`）がある。

本件は城候補がボスではなく自城側の通常城アファニアなので、boss siege 免除/boss_move family には入らない。#5789 の結論をそのまま適用できない。

### #5792（ch3 ヴィーナス、6ヶ月、別ラン g1373）

#5792 は通常城グリン隣の ordinary recall 型で、検証結論は (d) 判定不能だった。理由は、flow 投影が general を落とすため dispatched receipt の帰属を証明できない点と、`camp_status_seen` の古い episode 再利用で months が過大計上され得る点である。本件も同じ2つの制約を持つ。

相違点は、本件が ch12 で同ラン g1190-ch1v9、bot version v1595、城候補アファニアであり、ch12 boss staging/homeKeep3 の出撃制御下にあることだ。ただし現行コード上、これらは camp recall の免除条件ではない。帰還先・在城状況の違いは通常 recall を禁止しないため、原因 family を #5792 から明確に分ける根拠にはならない。

## #5721 / PR #5723 との関係

#5721 / PR #5723 の抑制対象は、同一 `(fingerprint, month, month_summary.first_tick)` の issue 再起票だけである。OPEN issue への再発コメントは抑制しない。現行 `file_issue` も、同一 fingerprint の OPEN issue があれば従来どおり再発コメントを投稿し、OPEN issue がない場合だけ state=all の同一 key を duplicate_month として suppress する。

本件は新規 issue だが、これは「初めての異常」を意味しない。camp_abandoned の fingerprint には general も month も入らず、ch12 の同種判定は同じ fingerprint になる。OPEN の #5810 がある限り、後続の別将軍・別 occurrence は再発コメントになる。実際、issue には 19-7 ポワソン、20-7 アマンディーヌの再発コメントがある。これは #5721 抑制の穴ではなく、設計どおりの追跡経路である。

## 残りの未決事項

- fingerprint / occurrence の sha256 は python3 実行承認が却下されたため未計算。上記の raw 入力で検品ゲートが再計算する必要がある。
- サフランの recall が 18-9/18-10/18-11 のどの月に、どの段階（未スキャン、SELECT未到達、camp menu未到達、dispatch済み、arrival未確認、month-end延期）まで進んだかは、ローテーション済みの decision log がない限り特定できない。
- `camp_status_seen` の episode が一度でも城帰還・再出発を跨いだかどうかは issue JSONだけでは分からない。months=3 が同一野営の実継続か、過大計上かは未証明。
- もし今後同様事象を検証可能にするには、month_audit の flow 投影に recall_verification の general / requested_tick / chapter / identity を bounded 追加するか、camp episode の reset/refresh receipt を残す必要がある。

## 参照した現行 main の主な箇所

- `src/docich/hanjuku/month_audit.py` lines 1730-1773: camp対象抽出と月数計算
- `src/docich/hanjuku/month_audit.py` lines 1776-1865: `_build_camp_abandon_incident`、incident_id/fingerprint
- `src/docich/hanjuku/month_audit.py` lines 2900-2973: `_track_camp_abandon`
- `src/docich/hanjuku/month_audit.py` lines 3545-3574: `_fingerprint` / `_month_key`
- `src/docich/hanjuku/month_audit.py` lines 5631-5695: OPEN issueへの再発コメントと duplicate_month 抑制
- `src/docich/hanjuku/flows/month.py` lines 645-652: 月次メニュー終了後に post_month_status を arm
- `src/docich/hanjuku/flows/post_month_status.py` lines 116-188, 206-282: boss/camp例外と `_tasks`
- `src/docich/hanjuku/flows/post_month_status.py` lines 1467-1478: scan完了後の tasks 再計算と general_statuses commit
- `src/docich/hanjuku/flows/post_month_status.py` lines 2245-2407: process から recall/boss_move 起動
- `src/docich/hanjuku/flows/post_month_status.py` lines 2503-2552: recall中断時の再キュー
- `src/docich/hanjuku/flows/camp_recheck.py` lines 327-402: `camp_status_seen` episode 登録と抑制
- `src/docich/hanjuku/strategy/orders.py` lines 868-893: ch12 boss staging / homeKeep3 は出撃制御であり recall免除ではない

## handoff

`DOCICH_HANDOFF_PATH` の絶対参照はこのセッションに明示されていない。shell 実行承認が却下されたため環境変数の実値も確認できず、非公開 handoff は未読として扱った。検証は GitHub issue 本文と worktree の現行コードのみに基づく。

## 検品ゲート確認（Muse、2026-11-28 19:02 JST、現行 main e0c0a442）

- fingerprint / occurrence を検品ゲートで再計算した。SHA-256("12|camp_abandoned|camp_abandoned")[:16] = 29609957117c9004、SHA-256("g1190-ch1v9|1190|camp_abandoned|サフラン|233254")[:20] = da2667ad3875d28d143a。いずれも issue の値と一致する。
- 結論 (d) 判定不能を維持する。現行 main のコードで次の3点を直接確認した。
  - `_build_camp_abandon_incident` は fingerprint を `_fingerprint(chapter, 'camp_abandoned', ['camp_abandoned'])`、incident_id を `runtime_id|generation|camp_abandoned|general|first_tick` から作る。式は検証記録のとおりである。
  - `_camp_age_months` と `camp_status_seen` の episode 抑制台帳が現行 main にあり、行軍観測を取りこぼすと古い episode の first_tick が残り、同一野営の継続月数を過大計上する経路が残っている。
  - 月次監査の flow 投影は `recall_verification` の general を落とすため、issue JSON からは `dispatched` の receipt がサフラン本人のものか別将軍・過去月のものかを判定できない。
- issue 本文からも裏付けた。サフランの `first_tick` は 233254 だが `last_tick` は 233225 で last が first より29 tick 古く、issue 内の時系列だけでは episode の連続性を独立に裏付けられない。
- 城候補アファニアは通常城であり、ch12 の boss staging / homeKeep3 は出撃制御であって camp recall の免除条件ではない。現行設計上は通常 recall 対象である。ただし recall が過去3ヶ月に発火したか、発火して失敗・中断・未到着だったかは、本番 decision log がローテーション済みのため issue 本文とコードだけでは一意に決められない。修正 PR は割り当てない。
