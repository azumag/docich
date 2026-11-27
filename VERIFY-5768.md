# VERIFY-5768 — [半熟英雄] 築城可能なまま月一終了: ch8 7-10

- 対象: docich issue #5768 / 監査 `chikujou_available_at_end` (confidence: high)
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1575-ch9-cliff-camp-recall`
- 月: ch8 `7-10`（観測 68 回 / 14.1 秒 / max gap 0.3 秒）
- fingerprint `c79f3c63b7a8f78f` / occurrence（incident_id）`68d5f906fe8a603739da`
- 検証ベース: worktree `verify/5768`、HEAD `14487bab13035f5b301cad63a6fef9a3b302aaa4`。現行 worktree の `src/docich/hanjuku/month_audit.py` と関連フローを read-only で確認した。
- 検証範囲: read-only。コード修正・git の書き込み操作・`gh`・push・PR 作成は行っていない。issue 本文は公開 GitHub ページの read-only 取得で確認した。
- 正本 handoff: 明示参照はなく、環境確認コマンドも承認却下されたため **未読**。checkout 内のコピー作成・内容推測はしていない。
- 重要な証拠欠落: 依頼どおり、ラン `g1190-ch1v9` の本番 decision log はローテーション済みとして扱い、探していない。以下は issue 本文と現行コードだけによる検証である。

## 結論: **(d) 判定不能 — 築城監査の事実検出は正しいが、`chikujou_deferred_sortie` が正当な発射確認待ちだったかを issue 本文だけでは確定できない**

確定できること:

1. 「未試行かつ在城ありの築城候補を残して月が終わった」という監査の事実認識は成立する。issue 本文では gold 1527 / reserve 30 / affordable true / buildable true、未試行かつ在城ありの候補は `いちじょうだに`・`まつやま`、試行済み `あるまむーん`・`えど`・`おかざき`・`しらかわ`・`ながはま`、増築済み `えど`・`しらかわ`、レベル 5 確認 `あるまむーん`・`おかざき`・`ながはま` で、world state の在城とも矛盾しない。したがって **(c) 誤検知ではない**。
2. 実際の終了経路は、走査不能・名称不一致・一覧停滞ではなく、`chikujou_scroll` 1 回後の `chikujou_deferred_sortie` である。現行コードの launch priority 分岐と決定列が一致する。
3. しかし issue 本文の decision rows には `chikujou_deferred_sortie.observed_metric.step` が無く、decision log も残っていない。どの出撃 step が築城探索を止めたか特定できない。issue 表の `step` 列は `_record` が付ける `chart_step` であり、priority latch が返した step ではない。
4. #5764 のような「確認窓内の enemy-directed live sortie」という独立証拠はない。月末 snapshot の enemy-directed live sortie は、少なくとも `retake` age 621 tick、`attack` age 1260 tick、`launched_unconfirmed` age 2109 tick で、200 tick の roster 待ち窓をそのままでは示せない。`move` の age 387 tick は launch priority 対象外である。
5. 一方で、#5765 のような「latch step が特定でき、その出撃が lost 済みだった」という独立証拠もない。`flow: {}` も反証にならない。`month_audit.py` の `_summarize_flow` は `sortie_attempt` の `step` / `target_seen` / `confirmation_tick` / `interrupted` を拾わないため、pre-commit attempt が存在しても issue JSON では空に見える。

このため、次のどちらかを現物 evidence なしに選ぶことはできない。

- **正当な launch priority**: まだ commit 前の敵向け出撃、または commit 済みだが launch probe が未解決の敵向け出撃が有効だった。この場合は (b) 設計どおり。
- **stale / 不適格な latch**: read-only probe、既に確認・解決済みの行軍、または中断済みの `sortie_attempt` に対応しない古い latch / probe が残り、築城と家来探索を止めた。この場合は (a) 実欠陥。

後者の疑いはあるが、決定 step が無いため本 incident に確定適用はできない。詳細を下記に記す。

## fingerprint / occurrence の式と入力値

`python3` による read-only 再計算を試みたが承認却下されたため、本環境では SHA-256 の独立再計算は **未実施** である。一致を主張しない。検品ゲートが下記 raw から再計算すること。なお issue コメントには「fingerprint / occurrence は month_audit.py の式で再計算して一致確認済み」とあるが、これは本検証環境での独立再計算ではない。

現行の chikujou 系 incident builder は `src/docich/hanjuku/month_audit.py` L2153-L2261。他種別の式ではなく、この builder では次のとおり。

```python
raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
       f"{kind}|{event.get('month')}|{mem.get('chapter')}")
record['incident_id'] = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
record['fingerprint'] = _fingerprint(mem.get('chapter'), kind, [kind])
```

`_fingerprint` は同ファイル L3521-L3523。

```python
raw = f"{chapter}|{kind}|{'+'.join(signals)}"
return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
```

重要点: chikujou builder は issue JSON の expanded `signals`（`chikujou_candidate` / `battle_grind` を含む）を fingerprint に使わず、`[kind]` 1 件だけを渡す。generic `_incident_id`（`runtime_id|generation|chapter|month`）も chikujou では使わない。

本件の raw 文字列:

```text
fingerprint:
8|chikujou_available_at_end|chikujou_available_at_end

occurrence / incident_id:
g1190-ch1v9|1190|chikujou_available_at_end|7-10|8
```

issue の値:

```text
fingerprint: c79f3c63b7a8f78f
occurrence:  68d5f906fe8a603739da
```

式と入力値の対応は確認できたが、SHA-256 の再計算自体は **未実施**である。

## 現行コードでの打ち切り・終了経路

### 1. `chikujou_deferred_sortie` の発火条件

`src/docich/hanjuku/flows/month.py`:

- L1329: `CHIKUJOU_DEFER_LIMIT = 2`。
- L1332 以降の `_chikujou_scroll` は 4 行の築城一覧を上下へ探索する。
- L1402-L1424: `active_sortie_launch_priority(mem)` が step を返し、scroll press が 1 回以上、月内 deferral 数が上限未満なら、次を行う。
  - `chikujou_defer.count` を 1 増やす。
  - `sub['declined'] = True` と `sub['aborted'] = True` を立てる。
  - `chikujou_deferred_sortie` を記録する。実ログの `observed_metric` には `visible` / `scrolls` / `step` / `deferrals` が入る。
  - B を返して一覧を閉じる。
- 上限に達した後は同じ launch priority でも通常の scroll 探索へ進む。

issue の決定内訳では `chikujou_deferred_sortie` は **1 回**だけである。したがって、現行 main の上限 2 を適用してもこの 1 回目は防げない。ラン版 v1575 にはまだこの上限が無かったが、本件の回数だけを見る限り差は結果を変えない。

### 2. launch priority の意味

`src/docich/hanjuku/strategy/orders.py` L1005-L1066:

- priority 対象は `attack` / `retake` / `boss` / `invade` で、`move`、`roster_probe`、`source_probe` は除外される。
- `active_sortie_launch_priority` は latch を無条件に信用せず、次のいずれかで step を返す。
  1. 未解決で `kept` でない `launch_probe` がある。
  2. plan active / `sortie_attempt` / `departure_request` が同一步を示す。
- terminal (`done` / `failed` / `launched` / `launched_unconfirmed`) や kept march では latch を落とす。

`src/docich/hanjuku/flows/map.py` L3069-L3095 では、departure commit 時に `sorties[step].status='en_route'` と `launch_probe` を作り、`arm_sortie_launch_priority` する。L3175-L3267 の `_probe_launch_markers` は、marker 確認または roster 確認で probe を閉じ、非 boss / 非 portrait で blind 後 `LAUNCH_PROBE_ROSTER_TICKS = 200` を超えたものは rollback する。

したがって、正当な post-commit priority なら、少なくとも台帳上は対応する敵向け行軍が `en_route` / `launched_unconfirmed` として残っているはずである。ただし probe の年齢は sortie の launch からの age と同じではなく、world-map survey の回数と blind 化時刻に依存する。sortie age だけで即座に stale と確定はできない。

### 3. deferral 後に month_done へ至る経路（当月は shop status `done`）

`chikujou_deferred_sortie` は単なる探索スキップではなく、`sub['declined']` と `sub['aborted']` を立てて B で戻る終端操作である。

`src/docich/hanjuku/flows/month.py`:

- L1682-L1683: `sub['aborted']` の間は `_chikujou_step()` が B を返し続ける。
- L2078-L2107 `_finish_month_sub()`:
  - verified な増築では `chikujou_upgrades` へ城を追加し、追加候補があれば `shop['chikujou'] = 'check'` にする。
  - 支払いの無い declined 終端では、`cost_declined` があり `not aborted` で追加候補へ進める場合だけ `shop['chikujou'] = 'check'` へ戻る。
  - 本件の deferral は cost decline ではなく `aborted=True` なので `check` に戻らない。
  - ここで当月の増築が無ければ `skipped` になるが、本件は `しらかわ` 6G と `えど` 17G の実費照合つき増築が済んでいるため、`shop.get('chikujou_upgrades')` が非空となり、shop status は **`done`** になる。
- L2108-L2118: その後 `chikujou` が「築城を見送った／結果と支払いを照合できないため追加の増築は行わない」で記録される。
- L767-L776: 月メニュー close 時に `month_done` が記録される。

これは issue の列、`chikujou_deferred_sortie` → `house_deferred_sortie` → `chikujou`（見送り）→ `month_level_scan_wait` → `month_done` と一致する。`month_done` 自体は残候補の直接原因ではなく、直接の打ち切りは deferral である。

本件は shop status `done` なので、L787-L846 の `_note_chikujou_skip_unexplained()` は対象外である（同関数は `chikujou == 'skipped'` の月だけを見る）。それでも監査側 `_chikujou_event()` は `month_done` を ended として拾い、affordable・staffed candidate・refusal/cost decline から `buildable` を計算する（`src/docich/hanjuku/month_audit.py` L1976-L2120）。`_build_chikujou_incident()` が medium へ落とす条件は held / menu unreadable / reserve skipped であり、launch priority deferral は含まれない（同 L2153-L2261）。したがって本件が high confidence の `chikujou_available_at_end` になるのは現行監査どおりである。

なお現行実装は「1 件増築した月を完了」とせず、残金と未試行候補があれば `chikujou_continue` で次の城へ進む設計である。実際に 2 回の `chikujou_continue` がある。増築済みだから残候補を残してよいという設計にはなっていない。

## 世界状態と決定内訳から分かること・分からないこと

### enemy-directed live sortie

月末 tick は 94019。issue の `world.sorties` で enemy-directed と分かる live sortieは次である。

| step | purpose | source → target | status | 月末 age ticks |
| --- | --- | --- | --- | --- |
| `A:36215b1d:Mddc71d` | attack | しらかわ → ひめじ | en_route | 1260 |
| `A:780d0fb6:M9c7481` | live sortie では null / launched_orders では attack | あるまむーん → しまばら | launched_unconfirmed | 2109 |
| `A:85ee8015:Mfb5f3b` | retake | まつやま → おだわら | en_route | 621 |

`chikujou_deferred_sortie` は month_done より約 1.0 秒前で、この月の観測間隔から数 tick 前にすぎない。したがって deferral 時点でもこれらの age はほぼ 1255 / 2104 / 616 tick 程度である。少なくとも #5764 の age 145 / 190 tick のような、200 tick 確認窓内を直接示す行軍はない。`A:5066378d:Ma95b45` の age 387 tick は `move` であり priority 対象外である。

ただし、上述のとおり probe の live 期間は launch age そのものではなく survey 進行に依存するため、この表だけでは stale とも確定できない。`world_map_units` が trailing 120 秒に 2 回あるものの、どの step の probe を何回読んだかは issue 本文に出ない。

### `confirmed 1 / order_start 3 / order_launched 1` は latch の正当性を直接示さない

`confirmed: 1` は trailing counts ではなく、現行 `src/docich/hanjuku/flows/map.py` L3286-L3290 の `month_sorties` ledger である。purpose `move` を除く launch confirmation を当月 scopeで数えるため、少なくとも 1 件の非 move launch confirmation がこの月にあったことは分かる。一方 `order_start: 3` / `order_launched: 1` / `sortie_launch_confirmed: 1` / `sortie_launch_unverified: 1` / `sortie_departure_requested: 1` は trailing 120 秒の counts である。visible decision rows は最後の 30 行だけで、これらの sortie 系 decision は表に出ていない。表の先頭は 1795801943.295 で、`sortie_launch_confirmed` はそれより前（月開始後およそ 1.8 秒以内）に発生したことになる。

もしその confirmed launch と同じ step が latch だったなら、probe は confirmation で閉じられ、1954.528 の `chikujou_deferred_sortie` より前に priority は消えているはずである。したがって `confirmed: 1` は月初に実際の敵向け発射確認があった傍証にはなるが、deferral 時点の正当な未確認 launch を直接証明しない。

一方、`sortie_launch_unverified: 1` が enemy-directed sortie のもので、まだ roster 解決前だったなら、deferral 時点でも正当な post-commit priority でありうる。しかし issue rows には step も purpose も無く、同じ blind probe が `move` だった可能性もある。`move` は priority 対象外だが、launch probe 自体は作られるため、この counts だけでは区別できない。

さらに `house_deferred_sortie` は counts では 10 回だが visible rows では 1 回だけであり、少なくとも 9 回は表の先頭より前に起きている。priority が月次処理前から効いていたことは分かるが、それが同一 latch の継続か、複数の start/probe/attempt だったかは分からない。

### 現在の計画の attack は read-only probe の形をしている

issue の `plan.chart_plan.orders` には `A:5066378d:M6ecf31` があり、purpose `attack` / source `いちじょうだに` / target `おだわら` / general `—` / note 「暫定: 膠着のためいちじょうだにの将軍一覧を読んでからおだわらへ出撃する」である。

現行 `src/docich/hanjuku/strategy/replan.py` L2306-L2314 と L2381-L2384 では、この文言の order は `general=MOVE_ANY_GENERAL` かつ `source_probe: True` / `roster_probe: True` の read-only probe として作られる。`month_audit.py` の `_summarize_plan` はこれらの flag を issue JSON に出さないが、note と general `—` はその形に強く一致する。read-only probe は `_launch_priority_order` から除外されるため、この計画自体は `chikujou_deferred_sortie` の latch を作らない。

ただし trailing の `order_start` がこの probe だったのか、別の enemy-directed order だったのかは、step が無いため確定できない。`order_dispatch_starved` は pending 指示が `order_start` に届かない診断であり、発射確認中の証拠でも latch の原因でもない。

## 実欠陥の疑いがある点（ただし本 incident への確定適用は不可）

現行コードには、#5757 / #5761 / #5765 の検証でも問題になった stale priority が残りうる経路がある。

- `src/docich/hanjuku/flows/battle.py` L2119-L2143: 目的地での戦闘結果が出ると `_sorties(mem).pop(step, None)` して `sortie_history` と `sortie_outcome` を記録するが、`launch_probe` や `sortie_launch_pending` をここで明示的には落としていない。
- `src/docich/hanjuku/strategy/orders.py` L1047-L1052: `active_sortie_launch_priority` は `launch_probe` があれば、plan status や live sortie の確認へ進む前に step を返す。
- `src/docich/hanjuku/flows/map.py` L3175-L3179: `_probe_launch_markers` は sortie が無くなった probe を次の world-map probe 実行時に落とす。しかし戦闘終了そのものでは落ちないため、次の survey までの間は stale probe が priority を生かしうる。
- `src/docich/hanjuku/flows/map.py` L179-L181 / L230-L234 は `sortie_attempt` に `interrupted=True` を残す経路がある。一方 `active_sortie_launch_priority` は L1061-L1064 で `sortie_attempt.get('step') == step` を in-flight 証拠に使い、`interrupted` を見ていない。`strategy/execution.py` 側では interrupted attempt を別扱いにしているが、launch priority 側にはその区別がない。

本件では trailing 120 秒に `house_deferred_sortie` が 10 回あり、latch が月次処理前後に繰り返し効いていたことは分かる。また `sortie_launch_confirmed` が visible rows より前にある一方で、その後も deferral が続いているように見える点は stale の疑いと整合する。ただし、別の unverified enemy probe か pre-commit attempt が残っていた可能性を排除できない。したがって (a) とは確定しない。

## 同型 issue との異同

### 共通点

- いずれも `chikujou_available_at_end` / high で、未試行候補が残ったまま月が閉じる。
- 直前に `chikujou_scroll` が記録され、走査不能系 (`castle_list_unread` / `castle_scan_exhausted` / `castle_scroll_no_progress`) ではなく `chikujou_deferred_sortie` が出る。
- 本件の `chikujou_deferred_sortie` は当月 1 回だけなので、現行の上限 2 は発動しない。
- 増築済みがあっても残金と未試行候補があれば次候補を探す設計である。

### #5757（ch4 4-3、(d)）との異同

- #5757 は trailing の `order_start` / `order_launched` が 0 で、world state にも priority 対象の live sortie が無かった。それでも invisible pre-commit attempt を排除できず (d) だった。
- 本件は `order_start: 3` / `order_launched: 1` / `confirmed: 1` で、world state に enemy-directed live sortie があるため、正当な priority の証拠は #5757 より強い。
- ただし若い enemy-directed live sortie がなく、latch step もないため、やはり確定はできない。

### #5761（ch5 5-8、(d)）との異同

- #5761 は enemy-directed live sortie があっても age 924 / 1050 tick で、確認窓内とは言えなかった。active order も read-only roster probe らしかった。
- 本件も現在の chart plan の attack は note 上 read-only roster probe の形をしており、`order_start` をそのまま enemy launch とみなせない点は同じである。
- 本件の enemy-directed live sortie の最小 age は 621 tick で #5761 より若く、さらに `confirmed: 1` / `order_launched: 1` がある。そのため (b) 寄りの傍証は #5761 より強いが、step と purpose が無く逆転はしない。

### #5764（ch6 6-4、(b)）との異同

- #5764 は月末の世界状態に敵向け attack の live `en_route` があり、少なくとも age 145 / 190 tick が `LAUNCH_PROBE_ROSTER_TICKS = 200` の窓内だった。したがって latch step が未特定でも正当な発射確認待ちの独立証拠があった。
- 本件の enemy-directed live sortie は最小でも retake age 621 tick で、attack は 1260 tick、`launched_unconfirmed` は 2109 tickである。#5764 と同じ判定はできない。
- `sortie_launch_unverified` の blind 化が直近なら probe は有効でありうるが、それを示す step/purpose/tick が issue 本文にない。

### #5765（ch2 5-11、(a)）との異同

- #5765 は decision log が残り、`chikujou_deferred_sortie.observed_metric.step` が `A:59dc871a:Md3216a` と特定でき、さらにその出撃が deferral の 190 tick 前に `lost` と決着していた。したがって解決済み出撃の stale latch と判定できた。
- 本件は decision log がなく、issue 本文にも latch step がない。`world.sortie_history` の直近 8 件はすべて `won` で、#5765 のような直近 `lost` は見えないが、これは stale でないことの証明にはならない（issue 要約は末尾 8 件だけで、全履歴・step 対応ではない）。
- したがって本件を #5765 と同じ (a) にする根拠はない。

## 修正実施の有無

- コード修正: なし
- バージョン採番変更: なし
- テスト実行: なし（read-only 検証でコード変更がなく、かつ `python3` 実行が拒否されたため）
- commit / push / PR / GitHub 投稿: なし

## 残件

後日 decision log または同等の state snapshot が残っている別経路で確認する場合の分岐:

- `chikujou_deferred_sortie.observed_metric.step` が pre-commit の生きた `sortie_attempt` / `departure_request` / plan active、または未解決の正当な post-commit `launch_probe` に対応していた → **(b) 設計どおり**。
- その step が read-only probe、既に確認・解決済みの行軍、または戦闘終了・interrupted 済みの attempt で、live evidence なしに latch/probe だけ残っていた → **(a) 実欠陥**。修正候補は、戦闘終了・arrival/overdue 等で sortie が閉じた時点で対応する `launch_probe` / `sortie_launch_pending` を落とす、`active_sortie_launch_priority` が interrupted attempt や probe の live sortie を検査する、あるいはその両方である。
- `confirmed: 1` / `order_start: 3` / `order_launched: 1` は trailing 120 秒の集計であって、deferral 時点の latch と同一 stepとは限らない。特に `sortie_launch_confirmed` は probe 解消を意味するので、それだけを理由に正当な deferral と読んではいけない。
- 運用判断として、1 回の正当な deferral で築城候補が残る月を high issue にし続けるかは別論である。現行の上限 2 は「単発 deferral が当月の築城を終端化する」構造を変えない。さらに本件のように増築済みで shop status `done` になる月は `chikujou_skip_unexplained` の診断対象にもならない。
