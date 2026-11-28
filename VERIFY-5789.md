# VERIFY-5789 — [半熟英雄] 野営放置: ch10 グレイ (3ヶ月)

- 対象: azumag/docich#5789 / 判定 `camp_abandoned` (confidence `high`)
- fingerprint: `c54915bc85432c02` / occurrence(incident_id): `89bd51828738f4586516` / key: `c54915bc85432c02/15-3/156137`
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1590-evicted-boss-order-retired`
- 月: ch10 `15-3`（観測 1 回 / 0.0 秒） / 将軍: グレイ / 継続: 3ヶ月 / 初回: `15-2` (tick `154892`)
- 検証基盤: worktree `verify/5789` = `origin/main` `50580f17f`（`hanjuku-chart-v1597-chikujou-defer-reason`）
- 検証範囲: read-only。issue 本文・再発コメントと現行 main のコードのみ。本番 decision log はローテーション済みのため参照していない。git の書き込み操作・gh コマンド・コード修正・PR 化はなし。
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読（AGENTS.md §1）。

## 結論: **(a) 実欠陥**

監査の発火自体は契約どおりで、誤検知ではない。グレイが camp recall（帰還）されないこと自体には、ch10 White House 向けの明示的な設計がある。しかし現行 main でも、その設計が唯一残す解消経路である月次 `boss_move` は、戦闘・場面割り込みで前面を失うと同月内に再試行されない。ボス攻撃中は戦闘割り込みが常態化するため、健全な White House 野営が「recall には戻さないが boss_move も毎月途中で消える」状態へ落ち、複数月の野営放置になり得る。issue の記録はこの欠陥型と一致する。

ただし、ローテーション済みの decision log がないため、グレイ本人の `boss_move` が実際に何月・何段階まで進んだか、どの観測で割り込まれたかまでは証明できない。結論 (a) の根拠は、現行コードに存在する再現可能な経路と、issue に記録された戦闘過多・ボス攻撃中・健全なボス候補野営という条件の一致であり、グレイ個別の操作レシートを直接確認したという意味ではない。

## 1. fingerprint / occurrence の式と入力値

現行 main の camp_abandoned 系 builder は `src/docich/hanjuku/month_audit.py` の `_build_camp_abandon_incident`（L1774-L1863）。

### fingerprint

`_build_camp_abandon_incident` L1860-L1861 は次を呼ぶ。

```python
record['fingerprint'] = _fingerprint(mem.get('chapter'), 'camp_abandoned', ['camp_abandoned'])
```

`_fingerprint` L3543-L3545 の式は次のとおり。

```python
raw = f"{chapter}|{kind}|{'+'.join(signals)}"
hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
```

本件の入力値は、issue の embedded JSON より:

- chapter: `10`（int。文字列化され `10`）
- kind: `camp_abandoned`
- signals: `['camp_abandoned']`

したがって理論 raw は:

```text
10|camp_abandoned|camp_abandoned
```

期待値: `c54915bc85432c02`

### occurrence（incident_id）

`_build_camp_abandon_incident` L1856-L1858 の式は次のとおり。

```python
raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
       f"camp_abandoned|{stay.get('general')}|{stay.get('first_tick')}")
record['incident_id'] = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
```

issue の embedded JSON の対応値は:

- runtime_id: `g1190-ch1v9`
- generation: `1190`
- general: `グレイ`
- camp_stay.first_tick: `154892`

したがって理論 raw は:

```text
g1190-ch1v9|1190|camp_abandoned|グレイ|154892
```

期待値: `89bd51828738f4586516`

### 再計算の成否

この作業環境では `python3` の実行が 2 回とも承認却下された。したがって独立再計算は**未実施**である。式と入力値は上記のとおり現行 main から特定できたので、検品ゲート側で sha256 を再計算できる。なお issue の kanban 登録コメントには「fingerprint / occurrence の sha256 再計算一致」を登録時に裏取り済みとあるが、本 VERIFY ではそれを自分の再計算としては扱っていない。

key の `156137` は camp_stay.first_tick ではなく、`month_summary.first_tick` である。`_month_key` L3548-L3561 と `_month_key_marker` L3564-L3572 が `(fingerprint, month, month_summary.first_tick)` を使う。issue の `month_summary.first_tick` は `156137` で、key `c54915bc85432c02/15-3/156137` と一致する。

## 2. 監査発火は契約どおり（誤検知ではない）

- `CAMP_ABANDON_MONTHS = 3`（`month_audit.py` L61）。
- `_track_camp_abandon`（L2898-L2971）は `general_statuses` の `location='camp'`、`location_observed` 有効、HP>0 の将軍を月またぎで数え、`months >= 3` で `_build_camp_abandon_incident` を作る。
- `_camp_generals`（L1728-L1742）は camp 行の実測を拾い、`_camp_age_months`（L1762-L1771）は `camp_status_seen` の初回 tick を月開始 tick と突き合わせる。
- issue の embedded JSON は、グレイが `camp_observed: true` / `status_text: キャンプしています` / `months: 3` / `first_month: 15-2` / `first_tick: 154892` であり、この検出器の入力そのものである。静的雛形や他種別の流用ではない。
- 月 summary が「観測 1 回 / 0.0 秒」なのは、camp_abandoned が月末の完走監査ではなく月またぎ直後の連続野営監査だからである。`_build_camp_abandon_incident` L1790-L1798 は observed month が無ければ現在観測だけの summary を作る。

したがって「グレイが少なくとも監査状態の上で3ヶ月連続野営と数えられた」事実は成立する。問題は解消経路側である。

## 3. camp recall が発火しない条件と、グレイに該当する経路

### 3.1 通常の recall 条件

`post_month_status._tasks`（L225-L308）は、camp の将軍を原則 `{'kind': 'recall'}` にする。ただし次の条件で recall にならない、または recall が遅れる。

1. `road`（ch9 の boss 前フェーズ）では修理以外を行わない（L234-L238）。
2. `camp_stays`（移動ボスフェーズ）では camp を home recall しない（L239-L240）。
3. ボス城至近の野営で、ボスが unlocked、broken ではなく、免除月数が `BOSS_CAMP_EXEMPT_MONTHS = 3` 以下なら、home recall ではなく包囲継続 / `boss_move` 側へ進む（L254-L281）。
4. HP が max の半分超なら `_boss_move_ready` が真で、`boss_move` タスクを積む（L135-L146, L264-L277）。実在セルの goal がある場合だけで、ch4 の未測定 `'ボス'` ラベルには発行しない。
5. 免除月数が 3 を超えれば `_tasks` 自体は `recall` を積む（L282）。しかし ch10 White House では後述の再分類がある。
6. 月次 scan 自体が武装しない・完了しない・sortie へ譲る・月末尾で defer される場合は、recall/boss_move の実行は次月以降へ滑る。

### 3.2 グレイは boss-adjacent かつ healthy な ch10 White House camp

issue の値:

- chapter: `10`
- castle_candidate: `ボス`
- HP: `73/73`
- broken: `False`
- egg: `カラフルエッグ`

`_boss_camp_holds_siege`（`post_month_status.py` L116-L132）は `castle_candidate` が boss castle かつ `broken is not True` で真になる。`_boss_move_ready`（L135-L146）は HP が半分超なので真になる。ch10 では `chart.castles(10)` に `'ボス'` があるため、`_boss_move_goal`（L168-L188）も goal を解決できる。したがってグレイは、home recall 対象ではなく `boss_move` 対象という分岐に入る。

ボス unlocked かは issue の world 投影に `boss_cell` が無いため directly には読めない。ただし embedded JSON には、同月に複数の `target: ボス` 出撃が `launched` / `en_route` として残っている。ch10 の boss order は White House が開いていなければ実行されない（`orders._boss_path_captured` / `_whitehouse_open` L502-L537、`post_month_status._boss_unlocked` L206-L221）ので、少なくともこのランでは White House open 側の状態であったことが強く示唆される。

### 3.3 ch10 では home recall 自体が boss_move へ再分類される

`recall._whitehouse_camp`（`recall.py` L165-L231）は、ch10 で White House が開いている間、ring 内または boss wave に属する健全な camp を「expired boss march」として扱い、ordinary straggler とは見なさない。broken または HP半分以下なら外れるが、グレイは HP73/73・broken false なので残る。

さらに `camp_recall_step` では、月次タスクが ordinary recall として始まった場合でも、目的地選択前に `_whitehouse_camp` が真なら `state['boss_move']=True` / `goal='ボス'` へ再分類する（`recall.py` L1500-L1505, L1530-L1537, L1846-L1848）。この挙動は `tests/test_hanjuku_ch10_whitehouse_gate.py` L254-L266 `test_a_queued_recall_is_reclassified_into_a_boss_move` で固定されている。

したがって、仮に免除月数 3 を超えて `_tasks` が `recall` を積んだとしても、現行設計では健全な White House camp は home recall に戻らず、再び `boss_move` になる。グレイに camp recall が発火しないのは、この点だけを見れば設計どおりである。

## 4. 実欠陥: 唯一の解消経路 `boss_move` が同月再試行されない

`boss_move` は設計上の解消経路だが、現行 main の `post_month_status.wait_recall` の再試行条件は `job.get('kind') == 'recall'` に限られる（L2503-L2552）。`boss_move` の job はこの if に入らず、`_flow(mem).get('recall')` が消えた後は無条件に `_task_complete(..., result='recall_finished_or_deferred')` へ進む。

つまり:

1. 月次タスクは `boss_move` を開始する（`post_month_status.py` L2325-L2407）。
2. 戦闘・イベント・月次場面などが前面を取り、recall flow が中断・消去される。
3. ordinary `recall` なら `RECALL_TASK_RETRIES` / `RECALL_INTERRUPT_GRACE` に基づき同月内で再キューできる。
4. しかし `boss_move` にはこの再キューが無く、その月のタスクは終了扱いになる。
5. ch10 White House では home recall へも落ちないため、翌月また同じ `boss_move` を試すだけになる。戦闘が多い boss フェーズではこのループが長期化し得る。

issue の証拠はこの欠陥型と整合する:

- signals に `battle_grind` があり、直前120秒の decision counts でも battle 系が30件、`boss_companion_required` を含む。
- 同じ月に boss へ向かう複数の行軍が `en_route` で残り、ボス攻撃が進行中だった。
- グレイは HP73/73 のままなので、ボス戦へ入って消耗した形跡ではなく、ボスへ再移動できず camp に残った側の観測である。
- 再発コメント 5件も同じ ch10 ボス攻撃期の別将軍で、いずれも3ヶ月野営。単一将軍の偶然ではなく、boss wave / boss_move パイプライン側の滞留を示唆する。

`recall_verification` の投影には `stage: await_dispatch` / `status: dispatched` / `steps: 1` が残るが、`_summarize_flow`（`month_audit.py` L529-L569）が `general` や `input_sent` を投影しないため、これがグレイ本人の dispatch か別将軍・別砦の dispatch かは判断できない。この値を「グレイの boss_move が成功した」証拠には使えない。

## 5. 再発コメント 5件の意味（#5721 / PR #5723 との関係）

再発コメントはすべて ch10 `15-5`、別将軍・別 incident_id だが同じ fingerprint である。

- ベーグル `3a7ebed4220f39c970a1`
- バゲット `cd659686f872fb5826fb`
- リーキ `0f9035ea631794ca55e0`
- ナストリーニ `388b43adafdbd132f53e`
- アルファルファ `4f4b6e5a7f92c21a072a`

これは重複起票抑制の破綻ではない。現行 `file_issue` の動作は次のとおり。

1. まず open issue から同じ fingerprint を探す（`_find_issue` L5616-L5621）。
2. 見つかれば、同じ occurrence が既に body/comment にあるかだけ確認し（`_occurrence_present` L5624-L5626, L5656-L5659）、なければ再発コメントを投稿する。
3. `duplicate_month` 抑制は open issue が無かった場合だけ、closed/all issue の exact `(fingerprint, month, month_summary.first_tick)` を探す経路である（L5670-L5678）。これが #5721 / PR #5723 の抑制である。
4. したがって抑制対象は「同じ月 instance の issue 再起票」であって、open issue への再発コメントではない。実際、テスト `test_file_still_comments_on_open_issue_with_same_month_key`（`tests/test_hanjuku_month_audit.py` L2545-L2555）は open issue + same month key でも `commented` を期待する。

また fingerprint は chapter と kind と基本 signal だけなので、将軍・月・ランを含まない。ch10 の camp_abandoned は将軍が違っても同じ fingerprint になる。再発コメント 5件は「同じ検出型が同じ open issue に集約された」ことを意味し、同一 occurrence の重複投稿ではない。

ただし month key にも将軍や incident_id は含まれない。open issue が無い状態で同じ月 instance の別将軍が後から来ると、現行の exact month key では区別できず `duplicate_month` 側へ寄る可能性はある。今回の5件は open issue へのコメントなのでその経路の直接証拠ではないが、設計上の注意点として残る。

## 6. #5792（ヴィーナス 6ヶ月・ch3・別ラン）との異同

#5792 は `[半熟英雄] 野営放置: ch3 ヴィーナス (6ヶ月)`、ラン `g1373-b4c4126f` / generation `1373` / v1591。

相違点:

- 章: #5792 は ch3、本件は ch10。
- 城候補: #5792 は `グリン`（boss castle ではない）、本件は `ボス`。
- 月の観測: #5792 は 166回 / 241.2秒、本件は月初の1回 / 0.0秒。
- 出撃: #5792 は確定0 / 発射0 / order_start 0 / order_launched 0、本件は確定3 / 発射6 / order_start 5 / order_launched 6。
- #5792 は `monthly_scan_hold` を伴い、post-month scan の決定が本文に多い。本件は `battle_grind` を伴い、boss 攻撃と戦闘が前面にある。
- ch3 `グリン` の camp には boss-adjacent 免除も White House boss_move 再分類も適用されない。したがって #5792 は ordinary recall が期待される型で、本件より「なぜ recall されないのか」が強い問題になる。
- 本件は ch10 White House の設計で home recall が意図的に抑止・再分類される型なので、同じ `camp_abandoned` でも原因層を共有しない。

共通点:

- どちらも camp_abandoned は検出契約どおりに発火し得る。
- どちらも月次処理が関与するが、incident だけでは実際の recall/boss_move の操作レシートを確定できない。
- fingerprint は章×kind 粒度なので、同じ章では将軍が違っても同じになる（ch10 と ch3 で値は別）。

## 7. 残件・未決

- python3 実行が承認却下されたため、fingerprint / occurrence の独立再計算は未実施。式と入力値は §1 に記録した。
- 本番 decision log がないため、グレイの `boss_move` が実際に開始・中断・dispatch のどこまで進んだかは未確認。結論 (a) は現行 main に残る再試行欠落と、issue の条件一致に基づく。
- `recall_verification` の月次監査投影には general / input_sent が残らない。投影だけでは本人の操作を追えないため、今後の監査改善候補ではあるが、本 VERIFY ではコード変更していない。
- `#5721` の closed-issue exact month key は将軍を含まない。open issue が無い場合の別将軍・同月 instance の扱いは別途確認価値がある。

## 検品ゲート確認（Muse、2026-11-28 13:31 JST）

- fingerprint / occurrence を検品ゲートで再計算した。SHA-256("10|camp_abandoned|camp_abandoned")[:16] = c54915bc85432c02、SHA-256("g1190-ch1v9|1190|camp_abandoned|グレイ|154892")[:20] = 89bd51828738f4586516。いずれも issue の値と一致する。
- 結論 (a) 実欠陥は、月次 `boss_move` の再試行条件が `kind == 'recall'` に限られ、戦闘・場面割り込みで recall flow を失うと同月内に再試行されない現行コードの経路で裏付けられている。ch10 White House では home recall へも落ちないため、ボス攻撃期にこのループが長期化し得る。再発コメント 5件（別将軍・同じ ch10 ボス攻撃期・3ヶ月野営）は単一将軍の偶然ではなくパイプライン側の滞留を示唆する。
