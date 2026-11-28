# VERIFY-5796 — [半熟英雄] 築城可能なまま月一終了: ch11 16-1

- 対象: docich issue #5796 / 監査 `chikujou_available_at_end` (confidence: high)
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1593-unreadable-card-source`
- 月: ch11 `16-1`（観測 100 回 / 20.3 秒 / max gap 0.4 秒、first_tick 173909 / last_tick 174008）
- fingerprint `0b6b69f4168c8ef4` / occurrence（incident_id）`4468362da100cc2adc9a` / key `0b6b69f4168c8ef4/16-1/173909`
- 検証範囲: read-only。コード修正・git の書き込み操作・`gh`・push・PR 作成は行っていない。issue 本文は公開 GitHub ページの read-only 取得で確認した。
- 正本 handoff: 明示参照はなく、環境確認を兼ねた最初の shell 実行が承認却下されたため **未読**。checkout 内のコピー作成・内容推測はしていない。
- 重要な証拠欠落: 依頼どおり、ラン `g1190-ch1v9` の本番 decision log はローテーション済みとして扱い、探していない。以下は issue 本文と現行 main のコードだけによる検証である。

## 結論: **(b) 設計どおり — 確認窓内の敵向け live sortie がある中で、築城走査を発射確認へ意図的に譲った**

ただしこれは「築城候補が残らなかった」という意味ではない。未試行かつ在城ありの候補 4 件（クラボニア・トリフェ・アエンタール・ネペンテ）が残った事実自体は正しく、監査の事実検出は **(c) 誤検知ではない**。分類の要点は、その残存が走査不能や stale latch ではなく、#5696/#5702 で導入された launch priority（出撃開始〜発射確認まで housekeeping を退避する仕組み）の正当な適用だったかである。本件には #5764 と同型の独立証拠があるため、(d) ではなく (b) とする。

## fingerprint / occurrence の式と入力値

`python3` による read-only 再計算を試みたが承認却下されたため、本環境では SHA-256 の独立再計算は **未実施**である。一致を主張しない。検品ゲートが下記 raw から再計算すること。

現行 main `src/docich/hanjuku/month_audit.py` の chikujou 系 builder は L2277-L2281 で次を使う。

```python
raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
       f"{kind}|{event.get('month')}|{mem.get('chapter')}")
record['incident_id'] = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
record['fingerprint'] = _fingerprint(mem.get('chapter'), kind, [kind])
```

`_fingerprint` は同ファイル L3543-L3545。

```python
raw = f"{chapter}|{kind}|{'+'.join(signals)}"
return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
```

重要点: chikujou builder は issue JSON の expanded `signals`（`chikujou_candidate` / `battle_grind` を含む）を fingerprint に使わず、`[kind]` 1 件だけを渡す。generic `_incident_id`（`runtime_id|generation|chapter|month`）も chikujou では使わない。

本件の raw 文字列:

```text
fingerprint:
11|chikujou_available_at_end|chikujou_available_at_end

occurrence / incident_id:
g1190-ch1v9|1190|chikujou_available_at_end|16-1|11
```

issue の値:

```text
fingerprint: 0b6b69f4168c8ef4
occurrence:  4468362da100cc2adc9a
```

式と入力値の対応は確認できたが、SHA-256 の再計算自体は **未実施**である。補助的な整合確認として、同じ chapter 11 / kind の VERIFY-5326 も fingerprint `0b6b69f4168c8ef4` を記録しているが、これは独立再計算の代替ではない。key の `16-1` と `173909` は month と first_tick であり、追加のハッシュ入力ではない。

## 現行 main での打ち切り・終了経路

### 1. `chikujou_deferred_sortie` の発火条件

`src/docich/hanjuku/flows/month.py`:

- L1411: `CHIKUJOU_DEFER_LIMIT = 2`。
- L1484-L1506: `_chikujou_scroll` は 4 行の築城一覧を探索中に `active_sortie_launch_priority(mem)` を確認する。step が返り、scroll press が 1 回以上、月内 deferral 数が上限未満なら、次を行う。
  - `chikujou_defer.count` を 1 増やす。
  - `sub['declined'] = True` と `sub['aborted'] = True` を立てる。
  - `chikujou_deferred_sortie` を記録する。実ログの `observed_metric` には `visible` / `scrolls` / `step` / `deferrals` が入る。
  - B を返して一覧を閉じる。
- 上限に達した後は、同じ launch priority でも通常の scroll 探索へ進む。

issue の決定内訳では `chikujou_deferred_sortie` は **1 回**だけである。したがって現行 main の上限 2 を適用してもこの 1 回目は防げない。v1578 で入った上限は、連続した deferral を減らす緩和であって、単発 deferral が当月の築城を終端化する構造を変えない。

### 2. launch priority の意味

`src/docich/hanjuku/strategy/orders.py`:

- L1058-L1073: priority 対象は `attack` / `retake` / `boss` / `invade`。`move`、`roster_probe`、`source_probe` は除外される。
- L1120-L1166: `active_sortie_launch_priority` は latch を無条件に信用せず、次のいずれかで step を返す。
  1. live `en_route` / `launched_unconfirmed` sortie と一致する、未解決で `kept` でない `launch_probe` がある。
  2. plan active / 中断されていない `sortie_attempt` / `departure_request` が同一步を示す。
- terminal (`done` / `failed` / `launched` / `launched_unconfirmed`) や kept march では latch を落とす。現行 main では interrupted な `sortie_attempt` を in-flight 扱いしない。

`src/docich/hanjuku/flows/map.py` では、order dispatch 時（L2735-L2742）と departure commit 時（L3091-L3100）に `arm_sortie_launch_priority` する。`_probe_launch_markers` は marker 確認または roster 確認で probe を閉じ、blind probe は `LAUNCH_PROBE_ROSTER_TICKS = 200`（L3124）を境界に扱う。portrait march 特例は chapter 9 だけである（L3127）。

### 3. 2 件の増築済みで shop_status が `done` になる経路

`chikujou_deferred_sortie` は単なる探索スキップではなく、`sub['declined']` と `sub['aborted']` を立てて B で戻る終端操作である。

`src/docich/hanjuku/flows/month.py`:

- verified な増築では `_finish_month_sub` が `chikujou_upgrades` へ城を追加し、残金と未試行候補があれば `shop['chikujou'] = 'check'` に戻す（L2198-L2207）。実際に issue の決定列にはゴンダル 12G・マランマ 8G の verified 増築と 2 回の `chikujou_continue` がある。
- deferral 後の sub は支払いがなく `cost_declined` もない。L2208-L2219 の declined 終端では、`cost_declined` があり `not aborted` で追加候補へ進める場合だけ `check` に戻る。本件の deferral は `aborted=True` なので `check` に戻らない。
- ここで当月の増築が無ければ `skipped` になるが、本件は `chikujou_upgrades` にゴンダル・マランマの 2 件があるため、shop status は **`done`** になる。
- その後 `chikujou` が「築城を見送った／結果と支払いを照合できないため追加の増築は行わない」で記録され（L2220-L2230）、月メニュー close 時に `month_done` が記録される（L815-L824）。

これは issue の列、`chikujou_deferred_sortie` → `house_deferred_sortie` → `chikujou`（見送り）→ `month_level_scan_wait` → `month_done` と一致する。`month_done` 自体は残候補の直接原因ではなく、直接の打ち切りは deferral である。

重要なのは、`done` が「全候補を処理した」意味ではないことである。現行実装は 1 件増築しただけでは終了せず、残金と未試行候補があれば `chikujou_continue` で次の城へ進む設計である。したがって未試行 4 件が残ったことと shop status `done` は矛盾しない。`done` は「少なくとも 1 件の verified 増築があり、その後の sub が終端した」ことを示すにすぎない。

### 4. 監査と skip 診断の関係

`_note_chikujou_skip_unexplained` は `shop['chikujou'] == 'skipped'` の月だけを見る（`month.py` L855-L927）。本件は `done` なので対象外であり、#5801 の指摘する `chikujou_skip_unexplained` 誤記とは別物である。

一方、監査側 `_chikujou_event()` は `month_done` を ended として拾い、affordable・staffed candidate・非 refusal covered・非 cost declined から `buildable` を計算する（`month_audit.py` L1989-L2134）。`_build_chikujou_incident()` が medium へ落とす条件は held / menu unreadable / affordability unverified / reserve skipped であり、launch priority deferral は含まれない（同 L2189-L2214）。したがって本件が high confidence の `chikujou_available_at_end` になるのは現行監査どおりである。

## 世界状態からの独立証拠

月末 tick は 174008。`world.sorties` の行軍は次のとおり。

| step | purpose | source → target | status | 月末 age ticks | priority 証拠としての評価 |
| --- | --- | --- | --- | ---: | --- |
| `A:be378878:M93732c` | move | アルマムーン → タタール | arrived | 11112 | 到着済み・move。対象外 |
| `A:d7a35b83:M602915` | attack | トリフェ → ジャンセニア | en_route | 134 | **敵向け attack・確認窓内** |
| `A:d7a35b83:Me14678` | move | トリフェ → ゴンダル | en_route | 627 | move は priority 対象外。age も 200 超 |

`A:d7a35b83:M602915` はハバティーの attack で、target ジャンセニアは issue の captured 一覧に含まれない敵城である。purpose `attack` は launch priority 対象である。月末 age 134 tick は `LAUNCH_PROBE_ROSTER_TICKS = 200` 未満であり、chapter 11 は portrait march 特例の chapter 9 ではなく、target もボスではない。blind probe の rollback 境界に達していない。

`chikujou_deferred_sortie` は month_done より約 1.16 秒前である。月の観測は 99 tick / 20.346 秒なので、deferral 時点では age はおおむね 128 tick 程度（どんなに見積もっても月末の 134 tick 未満）だった。したがって少なくとも 1 件、正当な発射確認待ちになりうる敵向け live sortie が確認窓内に実在した。これは #5764 の age 145 / 190 tick の attack と同型である。`move` の age 627 tick はこの証拠にカウントしない。

留保: issue 本文には `chikujou_deferred_sortie.observed_metric.step` がなく、当該 attack の probe が deferral 時点にまだ開いていたか、既に marker/roster confirmation で閉じていたかは分からない。したがって step 一意の証明ではない。ただし #5764 と同じ検証基準では、確認窓内の enemy-directed live sortie の実在は正当な deferral の独立証拠になる。さらに #5765 で見つかった stale latch 機構は v1587（merge 時 v1591 へ振り直し）で修正済みで、v1593 の本件ランはそれを含む。現行 main でも battle outcome / road defeat で `clear_sortie_launch_priority` が呼ばれ、interrupted attempt は in-flight 扱いされない。よって本件を (d) へ下げるほどの反証はない。

`sortie_history` 末尾にはリディアのジャンセニア attack が tick 173832 に `lost` とある（月末 age 176）。これは live sortie ではなく、月 first_tick 173909 より前に決着している。#5765 のように latch step と同一である証拠はなく、上記の live attack の存在を消すものではない。

## 同型 issue との異同

### #5768（ch8 7-10、(d)）との異同

- 共通: 増築 2 件後に `chikujou_scroll` → `chikujou_deferred_sortie` → `chikujou`（見送り）→ `month_done`。verified 増築があるため shop status `done` でも未試行候補が残る構造が同じ。
- 相違: #5768 は enemy-directed live sortie の最小 age が retake 621 tick、attack 1260 tick、launched_unconfirmed 2109 tick で、200 tick 確認窓内の独立証拠がなかった。本件は attack `A:d7a35b83:M602915` age 134 tick が窓内なので、(d) ではなく (b) 側の証拠がある。

### #5757（ch4 4-3、(d)）との異同

- #5757 は trailing の `order_start` / `order_launched` が 0 で、world state にも priority 対象の live `en_route` がなかった。本件は窓内の enemy-directed attack がある。
- 共通の留保として、issue 本文には latch step がなく、pre-commit attempt を完全には復元できない。それでも本件には #5757 に無い独立証拠がある。

### #5761（ch5 5-8、(d)）との異同

- #5761 の enemy-directed live sortie は age 924 / 1050 tick で確認窓外だった。本件の attack は age 134 tick で窓内である。
- したがって #5761 で不足していた正当な発射確認待ちの独立証拠が、本件にはある。

### #5764（ch6 6-4、(b)）との異同

- #5764 は月末の世界状態に敵向け attack の live `en_route` があり、少なくとも age 145 / 190 tick が 200 tick 窓内だった。本件は attack age 134 tick が 1 件ある。move age 627 tick は除外する。
- 件数は #5764 より少ないが、launch priority は単一 latch で十分に発動するものなので、1 件でも同型の独立証拠になる。latch step が未特定なのは両者に共通する留保である。

### #5765（ch2 5-11、(a)）との異同

- #5765 は本番 decision log が残り、`chikujou_deferred_sortie.observed_metric.step` が `A:59dc871a:Md3216a` と特定でき、その出撃が deferral の 190 tick 前に `lost` と決着していた。したがって stale latch と確定できた。
- 本件は issue 本文に latch step がなく、`lost` したリディア出撃と deferral を同一 step で結べない。さらに窓内の live attack がある。
- 機構面でも、#5765 の修正（#5772、v1587・merge 時 v1591 へ振り直し）は battle outcome / road defeat で latch・probe を掃除し、interrupted attempt を in-flight 扱いしない。v1593 の本件ランはこの修正後である。#5765 と同じ (a) にする根拠はない。

### #5795（ch3 7-10、同じ v1593）との異同

- 共通: `chikujou_scroll` 後に `chikujou_deferred_sortie` で終端し、未試行候補を残して月が終わる。
- 相違: #5795 は増築 0 件なので `_finish_month_sub` で shop status `skipped` になり、月末に `chikujou_skip_unexplained` が記録された。本件は増築 2 件なので shop status `done` であり、skip 診断の対象外である。
- したがって #5801 が問題にする「deferral 後の skipped が unexplained と誤記される」事案は #5795 型であって、本件 #5796 ではない。#5801 本文は #5796 を「増築2件確認後の done。意図どおりの動作」と整理しているが、本検証の (b) はその整理に依存せず、上記の現行コードと窓内 live attack を根拠にする。

## 修正実施の有無

- コード修正: なし
- バージョン採番変更: なし
- テスト実行: なし（read-only 検証でコード変更がなく、かつ `python3` 実行が拒否されたため）
- commit / push / PR / GitHub 投稿: なし

## 残件

- `chikujou_deferred_sortie.observed_metric.step` は issue 本文に出ず、decision log もローテーション済みのため、latch された step の一意な特定はできない。latch step の特定には実ログの `observed_metric.step` が必要であり、issue 決定表の `step` 列は `_record` が付ける `chart_step` であり、priority latch が返した step とは限らない。
- `A:d7a35b83:M602915` の probe が deferral 時点で未解決だったかは issue 本文だけでは分からない。ただし #5764 と同じ検証基準では、窓内の敵向け live attack の実在により (b) と判断できる。
- 運用判断として、1 回の正当な deferral で築城候補が残る月を high issue にし続けるかは別論である。現行の上限 2 は「単発 deferral が当月の築城を終端化する」構造を変えない。shop status `done` の deferral 終了を監査対象外または低優先にするかは、#5801 の未決質問 (b) として owner 判断が残っている。

## 検品ゲート確認（Muse、2026-11-28 15:31 JST）

- fingerprint / occurrence を検品ゲートで再計算した。SHA-256("11|chikujou_available_at_end|chikujou_available_at_end")[:16] = 0b6b69f4168c8ef4、SHA-256("g1190-ch1v9|1190|chikujou_available_at_end|16-1|11")[:20] = 4468362da100cc2adc9a。いずれも issue の値と一致する。
- 結論 (b) 設計どおりは、月末の世界状態に敵向け attack の live `en_route`（`A:d7a35b83:M602915`、age 134 tick）が 200 tick 確認窓内に実在し、#5764 と同型の正当な発射確認待ちの独立証拠があることで裏付けられている。`done` は「全候補を処理した」意味ではなく、verified 増築 2 件の後に deferral で sub が終端したことを示すにすぎない。
