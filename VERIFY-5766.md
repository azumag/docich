# VERIFY-5766 — [半熟英雄] 築城可能なまま月一終了: ch7 7-1

- 対象: docich#5766 / 監査 `chikujou_available_at_end` (confidence: high)
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1575-ch9-cliff-camp-recall`
- 月: ch7 `7-1`（観測 711 回 / 136.2 秒 / max gap 0.3 秒）
- fingerprint `a8f7e626304eab2a` / occurrence（incident_id）`0e45e1cce8432815f0d3`
- 検証ベース: worktree `verify/5766`、HEAD `393e7bafdb0dfc21068c96e9789e758f76540545`、現行 `hanjuku-chart-v1585-card-row-unread`
- 検証範囲: read-only。コード修正・git の書き込み操作・`gh`・push・PR 作成は行っていない。issue 本文は公開 GitHub ページの read-only 取得で確認した。
- 正本 handoff: 明示参照・環境参照を確認できず、**未読**。checkout 内のコピー作成・内容推測はしていない。
- 重要な証拠欠落: 依頼どおり、ラン `g1190-ch1v9` の本番 decision log はローテーション済みとして扱い、探していない。以下は issue 本文と現行 main のコードだけによる検証である。

## 結論: **(d) 判定不能 — 築城監査の事実検出は正しいが、`chikujou_deferred_sortie` が正当な発射確認待ちだったかを issue 本文だけでは確定できない**

確定できること:

1. 「ハシオクラム・フィロメラを残して築城可能なまま月が終わった」という監査の事実認識は成立する。issue 本文では gold 151 / reserve 80 / affordable true / buildable true、両候補は未試行かつ在城ありである。したがって **(c) 誤検知ではない**。
2. 当月 4 件（ベンゴディ・マンシー・リデンブロック・レオナール）の増築済みで shop status が `done` になることと、未試行候補が残ることは矛盾しない。現行実装は 1 件増築した時点で築城を終えるのではなく、実費照合後に残金と未試行候補があれば `chikujou_continue` で次の城へ進む。実際に本件でも 4 回の `chikujou_continue` がある。
3. 実際の終了経路は、走査不能・名称不一致・一覧停滞ではなく、`chikujou_castle_unavailable` の直後に出た `chikujou_deferred_sortie` である。現行コードの launch priority 分岐と決定列が一致する。
4. しかし issue 本文には `chikujou_deferred_sortie.observed_metric.step` が無く、decision log も残っていないため、どの出撃 step が築城探索を止めたか特定できない。
5. #5733 や #5764 のような「確認窓内の enemy-directed live sortie」という独立証拠はない。本件には enemy-directed live sortie `A:59db4923:M73131a`（attack / アルマムーン→ロキュータ / `en_route`）があるが、月末時点で age 841 tick であり、`LAUNCH_PROBE_ROSTER_TICKS = 200` の確認窓内とは言えない。
6. かといって、見えない pre-commit `sortie_attempt` や、map survey が遅れて未解決のまま残った post-commit `launch_probe` がなかったことも証明できない。

このため、次のどちらかを現物 evidence なしに選ぶことはできない。

- **正当な launch priority**: pre-commit の敵向け出撃、または commit 済みだが launch probe が未解決の敵向け出撃が有効だった。この場合は (b) 設計どおり。
- **stale な post-commit probe / latch**: 既に戦闘終了・到着済み・確認済みの出撃に対応する probe だけが残り、築城と家来探索を止めた。この場合は (a) 実欠陥。

後者の疑いはあるが、決定 step が無いため本 incident に確定適用はできない。詳細を下記に記す。

## 現行 main での打ち切り・終了経路

### 1. `chikujou_castle_unavailable` の意味

`src/docich/hanjuku/flows/month.py` L1754-L1785:

- 「これいじょう」または「しょうぐんがおりませなんだ」を読むと、選択中の城を `rows_tried` に入れ、`chosen` と `quoted_cost` を外す。
- その城について `chikujou_castle_unavailable` を記録し、今回の候補から外して次を探す。
- 選択主体を失った拒否文では城を推測しない。

issue 本文の `unavailable: ['プルートー']` と `tried` にプルートーが入っていることはこの経路と一致する。これはプルートーに対する説明済み終端であり、監査側でも `month_audit.py` L2035-L2038 が `chikujou_castle_unavailable` の castle を候補から除外する。

ただし、これは残る未試行候補ハシオクラム・フィロメラの説明にはならない。両城は `unavailable`・`tried`・`upgrades`・`maxed` のいずれにも入っていない。

### 2. `chikujou_deferred_sortie` の発火条件

`src/docich/hanjuku/flows/month.py` L1329-L1480:

- `CHIKUJOU_DEFER_LIMIT = 2`（現行 main。owner 2026-11-27 #5733 による上限）。
- `_chikujou_scroll` は 4 行の築城一覧を上下へ探索する。
- L1402-L1424 で `active_sortie_launch_priority(mem)` が step を返し、scroll press が 1 回以上、月内 deferral 数が上限未満なら次を行う。
  - `chikujou_defer.count` を 1 増やす。
  - `sub['declined'] = True` と `sub['aborted'] = True` を立てる。
  - `chikujou_deferred_sortie` を記録する（`visible` / `scrolls` / `step` / `deferrals`）。
  - B を返して一覧を閉じる。
- 上限に達した後は同じ launch priority でも通常の scroll 探索へ進む。

本件では `chikujou_scroll` が 2 回記録され、2 回目の scroll のあとにプルートーへの `chikujou_castle_unavailable` が出ている。`build_scroll.presses` は既に 1 回以上なので、プルートーを外して残候補を探す次の評価では、追加の scroll を待たずに launch priority 分岐へ入れる。issue の列どおり、`chikujou_castle_unavailable` の直後に `chikujou_deferred_sortie` が出る構造である。

issue の決定内訳では `chikujou_deferred_sortie` は **1 回**だけである。したがって、現行 main の上限 2 を適用してもこの 1 回目は防げない。ラン版 v1575 にはまだこの上限が無かったが、本件の回数だけを見る限り v1578 の差は結果を変えない。

### 3. deferral 後に month_done へ至る経路

`chikujou_deferred_sortie` は単なる探索スキップではなく、`sub['declined']` と `sub['aborted']` を立てて B で戻る終端操作である。

`src/docich/hanjuku/flows/month.py` L2078-L2118 `_finish_month_sub`:

- `verified`（支払いと増築結果の照合）ではない。
- `sub['declined']` で残金不変でも、`cost_declined` かつ `not aborted` で追加候補へ進める場合だけ `shop['chikujou'] = 'check'` に戻る。
- 本件の deferral は cost decline ではなく `aborted=True` なので、`check` に戻らない。
- 当月の `chikujou_upgrades` が既にあるため、shop status は `done` になる（増築が無ければ `skipped`）。
- その後 `chikujou` が「築城を見送った／結果と支払いを照合できないため追加の増築は行わない」で記録される。

これは issue の列、`chikujou_deferred_sortie` → `goal_queued` → `chikujou`（見送り）→ `month_level_scan_wait` → `month_done` と一致する。`month_done` 自体は `month.py` L767-L776 で月メニュー close 時に記録される終端であり、残候補の直接原因ではない。直接の打ち切りは deferral である。

shop status `done` は「全候補を処理した」意味ではない。`_chikujou_snapshot`（`src/docich/hanjuku/month_audit.py` L2013-L2092）は、tried / upgrades / maxed / unavailable を除外した上で affordable かつ staffed candidate が残れば `buildable` を立てる。本件では `done` でもハシオクラム・フィロメラが残るため、監査が ended + buildable を拾うのは現行ロジックどおりである。

なお `_note_chikujou_skip_unexplained`（`src/docich/hanjuku/flows/month.py` L787-L846）は `shop['chikujou'] == 'skipped'` の月だけに入る。本件は 4 件の増築で `done` なので、この診断が出ないのは deferral を説明済みとみなしたからではない。監査側 `_build_chikujou_incident`（`src/docich/hanjuku/month_audit.py` L2175-L2193）が medium へ落とす条件も held / menu unreadable / reserve skipped だけで、launch priority deferral は含まれない。したがって high confidence であること自体は、(a)/(b) のどちらにも傾かない現行監査の帰結である。

### 4. launch priority の意味

`src/docich/hanjuku/strategy/orders.py` L1005-L1066:

- priority 対象は `attack` / `retake` / `boss` / `invade` で、`move` と read-only probe は除外される。
- `active_sortie_launch_priority` は latch を無条件に信用せず、次のいずれかで step を返す。
  1. 未解決で `kept` でない `launch_probe` がある。
  2. plan active / `sortie_attempt` / `departure_request` が同一步を示す。
- probe の確認は plan status の terminal 判定より先に行われる。したがって plan status が `launched` でも、probe が残っていれば priority は返る。probe が無ければ terminal (`done` / `failed` / `launched` / `launched_unconfirmed`) や kept portrait march で latch を落とす。

`src/docich/hanjuku/flows/map.py` L3069-L3095 では、departure commit 時に world ledger を `status='en_route'` にし、同時に `launch_probe` を作って `arm_sortie_launch_priority` する。したがって **`en_route` は発射確認済みの意味ではない**。probe は marker または出撃元 roster で確認されるまで残り、blind 化後の roster 待ち上限は `LAUNCH_PROBE_ROSTER_TICKS = 200`（同 L3111-L3118, L3152-L3267）である。

house 側も同じ priority を見る。`src/docich/hanjuku/flows/house.py` L1471-L1478 では、priority が返る間、家来・状態探索を `house_deferred_sortie` で見送る。本件では trailing 120 秒に `house_deferred_sortie` が 7 回あり、同じ latch が月次処理中に繰り返し効いていた可能性は高い。ただし house 側の `observed_metric.step` も issue の rows には出ない。

## 世界状態から分かること・分からないこと

issue の world state には、次の行軍が残る。

| step | purpose | source → target | status | age ticks |
| --- | --- | --- | --- | --- |
| `A:21e5a67b:Mbbf30b` | attack | アルマムーン → マンシー | arrived | 7761 |
| `A:59db4923:M73131a` | attack | アルマムーン → ロキュータ | en_route | 841 |
| `A:aab7c136:M69f304` | move | アルマムーン → レオナール | arrived | 1148 |

priority 対象かつ live なのは `A:59db4923:M73131a` だけである。しかし、本章は ch7 で `PORTRAIT_MARCH_CHAPTERS`（ch9）に入らず、target ロキュータは boss castle ではない。marker を読めない blind probe が 200 tick を超えた場合、boss / portrait のように kept へ進むのではなく rollback 側へ進む設計である。少なくとも「age 145 / 190 tick だから確認窓内」と言えた #5764 とは違い、age 841 tick の行軍を当然に確認中とみなすことはできない。

打ち切りは month close より約 1.3 秒前なので、打ち切り時点でも age はおおむね 830 tick 台だったと考えられる。したがって本件には #5733・#5764 型の独立証拠はない。

一方で、この age だけから stale と確定もできない。

- `launch_probe` は commit から固定 200 tick で切れるのではなく、blind 化してから roster 待ち 200 tick を数える。blind 化は world map survey の読み進行に依存する。
- world map survey は `map.py` L2622-L2631 で `order is None` のときだけ開く。月次メニューや active order が続く状況では、probe の確認 survey 自体が遅れうる。
- issue JSON には `launch_probe` の有無・`tick`・`reads`・`blind` が出ない。`en_route` age 841 だけでは、probe が既に解決済みだったのか、survey 不足で未解決のままだったのか分からない。

`sorties.confirmed = 1` も決め手にならない。どの step の launch confirmation を数えたかが issue から読めず、もし `A:59db4923:M73131a` の確認ならその step の発射待ちは既に終わっている。別 step の確認でも、deferral の対象 step が正当だったかは示せない。

## latch step が特定できない理由

issue の決定表では、築城 rows の `chart_step` が `A:59db4923:M7873af` になっている。しかし、これは priority latch が返した step とは限らない。

- `src/docich/hanjuku/decision_log.py` L11-L18 の `_record` は、明示指定が無ければ現在の `plan.active` を `chart_step` にする。
- issue の plan active は `A:59db4923:M7873af` で、chart_plan 上はマスカットをリデンブロックからレオナールへ移す `purpose: move` の order である。move は launch priority 対象外である。
- `src/docich/hanjuku/month_audit.py` L3370-L3384 の `_decision_rows` は `observed_metric` を出力しない。`chikujou_deferred_sortie` と `house_deferred_sortie` はそれぞれ `observed_metric.step` に latch step を記録する実装だが、issue へ写る rows では削られる。

したがって、`A:59db4923:M7873af` は築城フロー実行中の active plan step を示すだけで、deferral 対象の証拠ではない。latch step の特定には本番 decision log の `observed_metric.step` が必要だが、ローテーション済みで存在しない。

また `flow: {}` は pre-commit attempt の不存在証明にならない。`month_audit.py` L529-L569 の `_summarize_flow` は `departure_request` の限定的な項目と汎用の stage/status 系だけを拾い、`sortie_attempt` が `step` / `target_seen` / `confirmation_tick` だけを持つ場合は空に見える。#5757・#5761 と同じ制限である。

## 実欠陥の疑いがある点（ただし本 incident への確定適用は不可）

issue の `launched_orders` には、直近の `sortie_history` と将軍・target が一致する enemy-directed order が残る。

- `A:aab7c136:M77d676`: attack / パプリカ / レオナール→ロキュータ。`sortie_history` にはパプリカ→ロキュータの `lost` が tick 81089、月末から age 875 tick。
- `A:aab7c136:Mc94e8c`: retake / トレビス / アルマムーン→ベンゴディ。`sortie_history` にはトレビス→ベンゴディの `won` が tick 80924、月末から age 1040 tick。

`launched_orders` が残ること自体は snapshot 保持なので直ちに不正ではない。また `month_audit.py` の world 要約は `sortie_history` の step を出さないため、上記の対応は将軍・target の一致による推定であって、step 単位の厳密な同一性ではない。ただし現行コードには、対応する probe/latch だけが残りうる経路がある。

- `src/docich/hanjuku/flows/battle.py` L2130-L2143: 目的地での戦闘結果が出ると `_sorties(mem).pop(step, None)` して `sortie_history` と `sortie_outcome` を記録するが、`launch_probe` や `sortie_launch_pending` をここで明示的には落としていない。
- `src/docich/hanjuku/strategy/orders.py` L1047-L1052: `active_sortie_launch_priority` は `launch_probe` があれば、plan status や live sortie の確認へ進む前に step を返す。
- `src/docich/hanjuku/flows/map.py` L3175-L3179: `_probe_launch_markers` は sortie が無くなった probe を次の world-map probe 実行時に落とす。戦闘終了そのものでは落ちないため、次の survey までの間は stale probe が priority を生かしうる。

したがって、「既に終わった attack / retake の probe が残り、月次の築城・家来探索を止めた」という実欠陥候補はコード上具体的に存在する。`house_deferred_sortie` が trailing 120 秒で 7 回あることも、この疑いと整合する。

ただし本 incident に適用するには、`chikujou_deferred_sortie.observed_metric.step` がこれらの post-commit step だったという証拠が必要である。issue 本文には step が無く、decision log は残っていない。ゆえに (a) と確定はしない。

## #5757・#5761・#5764 との異同

### 共通点

- いずれも `chikujou_available_at_end` / high で、未試行候補が残ったまま月が閉じる。
- 走査不能系ではなく `chikujou_scroll` 後の `chikujou_deferred_sortie` が終端になる。
- `chikujou_deferred_sortie` は当月 1 回だけなので、v1578 の上限 2 は発動しない。
- 増築済みがあっても残金と未試行候補があれば次候補を探す設計である。
- latch step は issue rows に出ず、decision log が無いと一意に特定できない。

### #5757（ch4 4-3、(d)）との違い

- #5757 は world state に priority 対象の live `en_route` がなく、正当な発射確認待ちの独立証拠がなかった。
- 本件には attack → ロキュータの live `en_route` が 1 件あるため、#5757 より正当な priority の可能性は強い。ただし age 841 tick で確認窓内とは言えず、(b) へ進めるほどの独立証拠ではない。

### #5761（ch5 5-8、(d)）との違い

- #5761 は enemy-directed live sortie が age 924 / 1050 tick で、確認窓内ではなかった。本件の age 841 tick も同じく窓外なので、判定不能の構造は #5761 に近い。
- #5761 では trailing の `order_start` が read-only roster probe らしい点が問題だった。本件では trailing の `order_start` は 0 で、active plan は move と明示されている。したがって「read-only probe が order_start を説明する」論点は弱いが、latch step 不明という核心は同じである。
- 本件は当月増築 4 件で shop status `done`、かつ `chikujou_castle_unavailable` 直後の deferral という点で終端の見え方は異なる。ただし、この違いは監査の誤検出を示さず、latch の正当性も決めない。

### #5764（ch6 6-4、(b)）との違い

- #5764 は月末 snapshot に attack → ボスの `en_route` が複数あり、うち 2 件は age 145 / 190 tick で `LAUNCH_PROBE_ROSTER_TICKS = 200` の確認窓内だった。打ち切り時点ではさらに若いため、step 未特定でも正当な発射確認待ちの独立証拠と判断できた。
- 本件の唯一の enemy-directed live `en_route` は age 841 tick で、同じ判断はできない。target もボスではなく通常敵城ロキュータである。したがって #5764 の (b) を本件へ適用できない。

## 現行 main（v1585）とラン版 v1575 の差

`257fabbb3`（v1575 導入コミット）から HEAD `393e7bafd` までの関連差分を確認した。

- `flows/month.py`: v1578 / PR #5735 で `CHIKUJOU_DEFER_LIMIT = 2` と deferral count が追加された。本件は deferral 1 回なので、この差分は結果を変えない。
- `flows/month.py` / `flows/map.py`: v1583 / PR #5762 で、出撃未着手の月の月一サブメニュー回数を抑える仕組みと `sortie_started_month` が追加された。月次処理が長く占有する問題への mitigation だが、本件の latch が正当だったかを決める証拠ではない。
- `strategy/orders.py`: v1579 / PR #5740 で、自城宛て `move` の到着実測を成功として扱う修正が入った。本件の疑義対象は enemy-directed attack / retake であり、直接関係しない。
- `month_audit.py`: v1583 以降の大きな差分は主に camp / card evidence 系で、chikujou incident builder の fingerprint / occurrence 式と `_track_chikujou` の構造は現行でも同じである。
- `flows/map.py`: launch priority / launch probe の基本条件は変わっていない。

したがって現行 main でのコード読みは、本件の v1575 の打ち切り機構にほぼそのまま適用できる。差があるのは月内 deferral 上限と月次メニュー占有の mitigation などで、本件の 1 回の打ち切りが正当だったかという問いを決めない。

## fingerprint / occurrence の式と入力値

`python3` の実行は承認却下されたため、本環境では digest の独立再計算を行えなかった。依頼の指示どおり、式と入力値だけを以下に残す。他種別の式は使っていない。

chikujou 系 incident の builder は `src/docich/hanjuku/month_audit.py` L2255-L2259。

```python
raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
       f"{kind}|{event.get('month')}|{mem.get('chapter')}")
record['incident_id'] = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
record['fingerprint'] = _fingerprint(mem.get('chapter'), kind, [kind])
```

`_fingerprint` は L3521-L3523。

```python
raw = f"{chapter}|{kind}|{'+'.join(signals)}"
return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
```

重要点: chikujou builder は issue JSON の expanded `signals`（`chikujou_candidate` や `monthly_scan_hold` を含む）を fingerprint に使わず、`[kind]` 1 件だけを渡す。generic `_incident_id`（`runtime_id|generation|chapter|month`）も chikujou では使わない。

本件の raw 文字列:

```text
fingerprint:
7|chikujou_available_at_end|chikujou_available_at_end

occurrence / incident_id:
g1190-ch1v9|1190|chikujou_available_at_end|7-1|7
```

issue の値:

```text
fingerprint: a8f7e626304eab2a
occurrence:  0e45e1cce8432815f0d3
```

式と入力値の対応は確認できたが、SHA-256 の再計算自体は **未実施**である。検品ゲート側で再計算が必要である。

## 修正実施の有無

- コード修正: なし
- バージョン採番変更: なし
- テスト実行: なし（read-only 検証でコード変更がなく、かつ `python3` 実行が拒否されたため）
- commit / push / PR / GitHub 投稿: なし

## 残件

後日 decision log または同等の state snapshot が残っている別経路で確認する場合の分岐:

- `chikujou_deferred_sortie.observed_metric.step` が pre-commit の生きた `sortie_attempt` / `departure_request` / plan active、または未解決の正当な post-commit `launch_probe` に対応していた → **(b) 設計どおり**。
- その step が `A:aab7c136:M77d676` / `A:aab7c136:Mc94e8c` のような戦闘終了済みの attack / retake、または既に確認済みの `A:59db4923:M73131a` で、live evidence なしに latch/probe だけ残っていた → **(a) 実欠陥**。修正候補は、戦闘終了・arrival/overdue 等で sortie が閉じた時点で対応する `launch_probe` / `sortie_launch_pending` を落とす、または `active_sortie_launch_priority` が probe の live sortie を検査する、あるいはその両方である。
- 運用判断として、1 回の正当な deferral で築城候補が残る月を high issue にし続けるかは別論である。現行の上限 2 は「単発 deferral が当月の築城を終端化する」構造を変えない。
