# VERIFY-5733 — [半熟英雄] 築城可能なまま月一終了: ch9 20-11

## 対象と検査基盤

- Issue: azumag/docich#5733「[半熟英雄] 築城可能なまま月一終了: ch9 20-11」。
- 判定: `chikujou_available_at_end` / confidence `high`。
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1575-ch9-cliff-camp-recall`。
- 月: ch9 `20-11`（28 観測 / 5.618973731994629 秒 / tick 152206–152233）。
- fingerprint `3ac564ab77acc95b` / occurrence（incident_id）`572385961011c2103a6e`。
- 検証 base: worktree `verify/5733`、HEAD `acdd99f78`（main と同じ。`hanjuku-chart-v1577-boss-redirect-y-jump`）。v1575 から本 HEAD まで、築城・launch priority・出撃 probe の関連実装には差分がない（`month.py` / `orders.py` は無変更、`map.py` は RECALL Y jump の別修正のみ）。
- 正本 `handoff.md`: `DOCICH_HANDOFF_PATH` 未設定のため**未読**。コピーの作成・内容推測はせず、GitHub issue 本文と現行コードを根拠にした。
- `gh` コマンド・push・PR・GitHub/Slack への投稿は行っていない。issue 本文は公開 GitHub ページの read-only 取得で確認した。
- 重要な証拠欠落: 本件ラン `g1190-ch1v9` の decision log は本番 VM 上に見つかっていない。同名ディレクトリはなく、11/15 頃の別ラン `g1190-67435f4d` は世代こそ同じ 1190 でも日時・版が異なる別物なので使用していない。以下は issue 本文に埋め込まれた決定表・集計・世界状態とコードからの検証であり、完全な `hanjuku_decisions.jsonl` とフレームによる復元ではない。

## 結論: **(b) 設計どおり — 出撃コミット済みで発射確認待ちの敵向け行軍が実在し、その確認窓の内側で築城走査を意図的に打ち切った**

主因は、走査不能・候補名不一致・scroll 停滞ではなく、v1570 系で導入された launch priority である。`chikujou_deferred_sortie` は不当な stale latch ではなく、少なくとも ledger 上は `イデ！ → ボス` の敵向け attack が commit 済み・`en_route`・未確認という、まさに優先化の対象状態で発火している。

確定できる時系列:

1. issue 世界状態の行军中 sortie に `I:381454:2b535a13` がある。`purpose: attack`、`source: イデ！`、`target: ボス`、`status: en_route`、月終了 tick 152233 時点で `age_ticks: 97`。
2. したがって commit tick はおよそ 152136 で、月開始 tick 152206 の時点ですでに age 70 の出撃確認待ちだった。築城走査の打ち切り（05:04:24Z）は月終了（05:04:26Z）より前なので、その時点の age は 97 未満である。
3. chapter 9 は `PORTRAIT_MARCH_CHAPTERS` に入り、行軍部隊は肖像表示で triangle marker を読めない。また target はボスなので、marker survey だけでは発射を確定できず、source roster・到着・期限に委ねる設計である。blind probe が roster 待ちへ入っても `LAUNCH_PROBE_ROSTER_TICKS = 200` までは rollback/kept 判定へ進まない。
4. 本件の打ち切り時点は age < 97 < 200 であり、発射確認の証拠収集窓の内側だった。当月内に `confirmed` が 0 なのは「まだ確認できていない」ことと一致するが、「もう失敗と確定していた」ことは意味しない。
5. trailing 120 秒の counts には `order_start: 11`、`order_launched: 7`、`sortie_launch_confirmed: 6`、`sortie_launch_unverified: 6`、`sortie_unverified_rearmed: 8` がある。発射確認と rollback/re-arm の機構が動き続けており、latch だけが放置された停止状態ではない。

物理的にゼウスが本当に歩き出していたかは、launch probe の存在意義どおり roster/frame の直接証拠が必要であり、今回は decision log と frame が欠けているので独立には確定できない。ただし `en_route` は単なる计划ではなく、journaled A と picker close に基づく `_commit_departure` で作られる。優先化の条件は「物理発射が確認済み」ではなく、**commit から確認/rollback が付くまで**なので、本件はその条件を満たす。仮にこの sortie が後で roster 実測により rollback されたとしても、打ち切り時点ではまだ失敗と確定しておらず、優先化は設計どおりである。

## コード経路の照合

### 出撃 commit と発射確認

- `src/docich/hanjuku/flows/map.py` L3030-L3092 `_commit_departure`: journaled A + picker close の receipt がある場合だけ `order` を `launched` にし、garrison から将軍を動かし、`sorties[step].status='en_route'` を作る。source/target が章の城なら `launch_probe` を作り、`arm_sortie_launch_priority` で latch する。
- 同 L3108-L3119: `LAUNCH_PROBE_READS = 5`、`LAUNCH_PROBE_ROSTER_TICKS = 200`、chapter 9 は `PORTRAIT_MARCH_CHAPTERS`。
- 同 L3149-L3264 `_probe_launch_markers`: fresh triangle があれば `sortie_launch_confirmed`。読めない場合は blind にして source roster を要求する。chapter 9 / boss target では、肖像表示で triangle を判別できないため、200 tick 後に rollback せず `kept` とし、roster・到着・期限へ委ねる。
- 同 L3290-L3333 `settle_launch_probes_from_roster`: source roster に将軍が残っていれば rollback、いなければ launch confirmed。
- 同 L3336-L3385 `_rollback_unverified_launch`: 未発射と分かった場合だけ order を pending/failed へ戻す。

### launch priority が残る条件

- `src/docich/hanjuku/strategy/orders.py` L1005-L1020: priority 対象は `attack` / `retake` / `boss` / `invade` で、`move` と read-only probe は除外。
- 同 L1023-L1066 `active_sortie_launch_priority`: latch は単なる sticky flag ではなく、対象 order が存在し、かつ (i) 未解決の `launch_probe` がある、(ii) plan active / target-selection attempt / departure reservation のいずれかにある場合だけ返る。terminal (`done`/`failed`/`launched`/`launched_unconfirmed`) や `probe.kept` では消す。
- 本件の live sortie `I:381454:2b535a13` は `purpose=attack` であり、他の `en_route` は `move` ばかりなので、priority latch の対象として残りうる敵向け行軍はこれである。

### 築城走査の打ち切り

- `src/docich/hanjuku/flows/month.py` L1296-L1433 `_chikujou_scroll`: 通常は 4 行の築城一覧を上下に探索するが、L1366-L1377 で `active_sortie_launch_priority(mem)` が返り、かつ 1 回以上 scroll 済みなら、`sub['declined']` / `sub['aborted']` を立て、`chikujou_deferred_sortie` を記録して B で戻る。理由文は「出撃の開始から発射確認までのため、築城一覧の探索を打ち切る」。
- issue の決定表は `chikujou_leftover_refusal` → `chikujou_max_level_seen` → `chikujou_scroll` 1回 → `chikujou_deferred_sortie` → `chikujou` → `month_level_scan_wait` → `chikujou_skip_unexplained` → `month_done` で、このコード経路と一致する。
- `month.py` L734-L743: 月メニュー close 時に `month_done` を記録する。`month_done` の理由「月一の購入を終了」は築城省略の根拠ではなく、月次処理の終端記録である。

したがって、打ち切りは scroll の失敗ではなく、owner 指示どおりの優先順位付けである。5.6 秒しかない短い月では、全候補走査より発射確認へ時間を回す設計の効果がそのまま出た。

## #5704・#5326 との異同と v1575 での再発性

### #5704（ch12 27-2）との違い

- #5704 は v1525 で、12 回 scroll した後に同一 signature の停滞による `castle_scroll_no_progress` 側の打ち切りだった。launch priority は当時存在せず、候補へ到達できない走査失敗が主因だった。
- その後の修正 v1569/#5704 は、repeated window で止めず両方向へ走査する変更だった。v1575 には含まれている。
- 本件は scroll 1 回の直後に `chikujou_deferred_sortie` で止まっている。両方向走査へ進む前に、明示的な launch priority 分岐が先に発火した。したがって #5704 の走査失敗の再発ではない。
- #5704 修正は本件を「カバーしていない」というより、そもそも本件の原因層が違う。launch priority 中は走査を継続しない設計なので、走査改良の出番がない。

### #5326（ch11 17-11）との違い

- #5326 は v1446 で、scroll 成功 0 回、sub 起動直後の画面遷移/読取待ちが有限上限へ達した系だった。主な候補は `build_screen_unclassified` で、一覧探索以前の問題だった。
- 本件は一覧へ入り、`leftover_refusal` と `max_level_seen` を処理し、少なくとも 1 回は `chikujou_scroll` 成功後に優先化で退避している。画面未分類や unread の有限待ちではない。
- したがって #5326 の再発でもない。

### v1575 での再発か

- v1575 には #5704 の両方向走査修正と、#5696/#5702 由来の launch priority（v1567/v1568/v1570）が両方入っている。
- 本件は前者の未修正再発ではなく、後者の設計が初めて可視化した衝突である。v1575 以後 HEAD までに、この衝突を解消する修正は入っていないが、それは実装漏れではなく現行の優先順位設計そのものである。
- v1577 の `map.py` 変更は RECALL Y jump 二重開始の防止で、launch probe の確認条件や築城優先順位を変えない。`month_audit.py` の差分は #5730 の別種 audit evidence で、chikujou builder/tracker を変えない。

## fingerprint / occurrence の再計算

`python3`・`sha256sum`・`node` の実行は環境に拒否されたため、bash の整数演算だけで SHA-256 を実装した inline 手順で raw 文字列から再計算した。hash 対象は種別ごとの実 builder 式であり、汎用式を当てていない。

### fingerprint

現行 `src/docich/hanjuku/month_audit.py` L3449-L3451:

```python
raw = f"{chapter}|{kind}|{'+'.join(signals)}"
sha256(raw)[:16]
```

chikujou incident の呼び出しは `_build_chikujou_incident` L2187 で、issue JSON の全 signals ではなく `[kind]` 1件を渡す:

```python
record['fingerprint'] = _fingerprint(mem.get('chapter'), kind, [kind])
```

raw:

```text
9|chikujou_available_at_end|chikujou_available_at_end
```

再計算した全長 SHA-256:

```text
3ac564ab77acc95ba3b2ba14b8cb72f4176ba91f1ac5bbd51d4045ea9ca849b1
```

先頭 16 桁は `3ac564ab77acc95b` で、issue と一致する。

### occurrence / incident_id

generic `_incident_id`（L3444-L3446）は `runtime_id|generation|chapter|month` だが、chikujou incident では使わない。`_build_chikujou_incident` L2183-L2185 の専用式は:

```python
raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
       f"{kind}|{event.get('month')}|{mem.get('chapter')}")
record['incident_id'] = sha256(raw)[:20]
```

raw:

```text
g1190-ch1v9|1190|chikujou_available_at_end|20-11|9
```

再計算した全長 SHA-256:

```text
572385961011c2103a6e53452f2c13fc5df9fe8bbd8d4ab9451752a605806c3c
```

先頭 20 桁は `572385961011c2103a6e` で、issue と一致する。generic `_incident_id` の raw `g1190-ch1v9|1190|9|20-11` は chikujou builder ではないため採用しなかった。

## `chikujou_skip_unexplained` について

決定表には `chikujou_skip_unexplained` がある。字面どおりには誤りに見えるが、これは本件の主分類を変えない。

- `_note_chikujou_skip_unexplained`（`month.py` L754-L813）は refusal・reserve・`chikujou_held` 系の説明だけを見ており、`chikujou_deferred_sortie` を説明済み終端として扱う分岐がない。
- したがって「拒否・保留・読取不可・予約不足の記録が無い」という文は、列挙した 4 原因については事実でも、launch priority による意図的退避を読者へ示さない不十分な診断である。
- ただし flow 本体の打ち切りは設計どおりなので、この診断不足は「実際に築城可能なのに打ち切られた原因」ではない。監査の説明性を改善するなら別の policy/audit 変更候補になるが、本件を (a) のゲーム動作欠陥にはしない。

## なぜ (a)・(c)・(d) ではないか

- **(a) 実欠陥ではない**: 築城しない判断は、input/scroll の失敗ではなく、commit 済みの敵向け出撃を優先する明示設計で行われた。少なくとも打ち切り時点では、対象 sortie は rollback/failed/kept に達しておらず、確認窓の内側だった。発射が最終的に未確認で rollback されたとしても、その判定が下る前の優先化は設計の対象である。
- **(c) 誤検知ではない**: gold 9993 / reserve 30 で affordable、候補 `イデ！` は staffed candidate、`shop_status=skipped`、`buildable=true` という監査事実は issue 内で一貫している。監査が嘘を検知したのではなく、意図的な犠牲を検知した。
- **(d) 判定不能ではない**: decision log 欠落で物理発射の最終結果や `chikujou_deferred_sortie.observed_metric.step` は確定できない。しかし、issue 世界状態に対象条件を満たす live attack が残り、age が 200 tick 未満と分かるため、「打ち切り時点で優先化条件を満たしていたか」という本問には回答できる。残る不確実性は分類を変えない。

## 修正実施の有無

- 実修正なし。結論が (a) 実欠陥ではないためである。
- バージョン採番変更なし。
- テスト実行なし（コード変更がない read-only 検証のため）。

## 残件

- 完全な `hanjuku_decisions.jsonl` / `hanjuku_frames/` が回収できた場合、`I:381454:2b535a13` のその後（roster confirm、rollback、boss arrival）と、`chikujou_deferred_sortie.observed_metric.step` を確認してよい。ただし本結論は、打ち切り時点の正当性なので変わらない見込みである。
- 運用上の論点として、launch priority による意図的退避を監査で high の unexplained として扱い続けるかは別途判断がある。現行監査は「築城できたのに終わった」事実を正しく拾っているが、原因の説明性は低い。policy として cost を可視化したいなら現行でよく、ノイズと見なすなら `chikujou_deferred_sortie` を説明済み終端として medium/no-file にする変更が候補になる。これは owner の設計判断であり、本検証では実装しない。
