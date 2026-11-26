# VERIFY-5704 — [半熟英雄] 築城可能なまま月一終了: ch12 27-2

## 対象と検査基盤

- Issue: azumag/docich#5704「[半熟英雄] 築城可能なまま月一終了: ch12 27-2」。
- 判定: `chikujou_available_at_end`（confidence: high）。
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1525-sortie-cards-eligible`。
- 月: ch12 `27-2`（観測 137 回 / 26.527291774749756 秒 / max gap 0.21252727508544922 秒 / `started_clean: true`）。
- 出撃集計: confirmed 1 / departures 0 / launch_events 0 / `order_start` 0 / `order_launched` 0。
- 築城: `ended` / `month_done` / gold 9815 / reserve 30 / `shop_status: skipped` / 未試行候補 `['アファニア', 'ファンゴル']` / upgrades 空。
- issue 本文・再発コメント（ch12 27-3、incident_id `a9171f4f0316732c8375`）・割当コメントを全文確認した。再発側の decisions 明細はコメントに無いため、本検証の機構判定は 27-2 の証拠 JSON を主根拠とする。
- 観測版 v1525 の base commit は `05f05c050`（`fix(audit): 計画が空のときは卵落0の札だけを携行可能な在庫として数える (v1525, #5613) (#5636)`）。この commit の `src/docich/hanjuku/version.py` は `BOT_VERSION = 'hanjuku-chart-v1525-sortie-cards-eligible'`。
- 現行 `origin/main` / HEAD は `aca84be44`（`hanjuku-chart-v1566-egg-attempt-retry`）。read-only 検証であり、本ファイル以外のコード変更・VM 操作・push・PR・issue コメント・Slack 投稿は行っていない。
- `DOCICH_HANDOFF_PATH` の有無を確認するコマンドは実行環境に拒否され、正本 `handoff.md` は未読。GitHub の issue 本文・コメントと checkout 内コードを根拠にした。

## 結論: **(a) 実欠陥 — 築城一覧の走査が候補へ到達せず打ち切られ、fail-closed の skipped がそのまま月終了へ進んだ。現行 main でも未修正**

本件は誤検知ではない。gold 9815 / reserve 30 で `_chikujou_affordable` は真、監査と flow は同じ `_chikujou_candidates` 系の候補集合を使い、アファニア・ファンゴルは staffed な未試行候補として残っている。それでも 1 城も確定・増築せず `skipped` で月を終えたのは、「余剰があれば所有城も増築する」という `_month_chikujou` の設計目標を達成できていない。

一方で、`chikujou_held` の後に B で戻り、支払い・結果を照合できないまま追加確定しない経路そのものは fail-closed 設計どおりである。問題はその前段で、**まだ buildable と見なされている候補が一覧内で選択可能になる前に走査が `castle_scroll_no_progress` 側で終わった**ことである。issue の証拠だけでは「実一覧に候補行が無かった」のか「入力無効で途中までしか進めなかった」のかは一意に切れないが、どちらであっても設計内の正常終了ではなく、現行 main にも同じ実装が残っている。

さらに本件では 2 つの診断欠陥が確定する。

1. `chikujou_held` が直前に存在したのに `chikujou_skip_unexplained` が記録された。これは #5338 で追加された `chikujou_skip_after_hold` 分岐が実運用の観測単位をまたいで機能していないことを示す。
2. `chikujou_max_level_seen` が 6 回あるのに監査 JSON の `maxed` が空なのは、同レコードに `month` が付かず、監査側の same-month evidence filter から落ちるためである。issue 本文だけではレベル5走査の内訳を正確に復元できない。

## fingerprint / occurrence の照合

### 実行環境での再計算

`python3` による hashlib 再計算と `sha256sum` による再計算は、いずれもこの実行環境で拒否された。したがって **sha256 の直接再計算は未実施**である。以下はコード上の raw 文字列と、issue コメントに記録された検品ゲート側の再計算結果の照合である。

### fingerprint

現行 main `src/docich/hanjuku/month_audit.py:3394-3396` の helper は

```python
raw = f"{chapter}|{kind}|{'+'.join(signals)}"
sha256(raw.encode('utf-8')).hexdigest()[:16]
```

である。chikujou incident の呼び出し側は `_build_chikujou_incident`（`month_audit.py:2128-2133`）で

```python
record['fingerprint'] = _fingerprint(mem.get('chapter'), kind, [kind])
```

となっている。つまり **issue JSON の全 signals ではなく `[kind]` 1件だけ**が使われる。raw は

```text
12|chikujou_available_at_end|chikujou_available_at_end
```

である。これは依頼文の指摘どおりであり、検品ゲートが一致を確認済みとする `63706b90f609ef65` と式が一致する。v1525 `05f05c050` の同関数も同じ式だった。

### occurrence / incident_id

依頼文の「`_incident_id(identity, chapter, month)`」は、chikujou incident には適用されない。`_incident_id`（`month_audit.py:3389-3391`）は generic `_build_incident`（`month_audit.py:3427-3499`）で使われる raw

```text
runtime_id|generation|chapter|month
```

用である。一方 `_build_chikujou_incident` は独自に（`month_audit.py:2128-2130`）

```python
raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
       f"{kind}|{event.get('month')}|{mem.get('chapter')}")
record['incident_id'] = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
```

としている。したがって 27-2 の正しい raw は

```text
g1190-ch1v9|1190|chikujou_available_at_end|27-2|12
```

であり、`g1190-ch1v9|1190|12|27-2` ではない。issue コメントにはこの raw から `8233e6945c193f49f997`、27-3 の raw

```text
g1190-ch1v9|1190|chikujou_available_at_end|27-3|12
```

から `a9171f4f0316732c8375` になることが検品ゲートで確認済みと記録されている。**occurrence は issue 側の誤りではなく、検品ゲートが最初に試した generic `_incident_id` 形式の raw が chikujou builder と違ったことが不一致理由**である。v1525 `05f05c050` でも同じ独自 raw だった。

## v1525 と現行 main の照合

### 差分の有無

`git diff 05f05c050..HEAD -- src/docich/hanjuku/flows/month.py` の差分は、募集費確認が読めない場合の bounded abort（`MONTH_SUB_UNREAD_CONFIRM_HOLD`）と `boss_dispatch` 引数の伝播だけである。築城一覧のスクロール、候補選択、`chikujou_held`、`_finish_month_sub` の skipped 終端には差分がない。`strategy/economy.py` にも v1525 以後の差分はない。`month_audit.py` は別種 incident の追加で大きく変わっているが、`_build_chikujou_incident`・`_track_chikujou` の本件関連部分は v1525 と同じである。

したがって現行 main でも本件は再発しうる。v1525 以後にマージされた修正で、この「候補未到達のまま scroll no-progress → skipped」を止めるものは確認できなかった。

### 候補計算

`src/docich/hanjuku/strategy/economy.py:1574-1585` の `_chikujou_candidates` は、

- 章の home castle と `captured` の所有城を集め、
- `garrison[c] == []` と実測された城だけを外し、
- home → staffed → unmeasured の順で返す。

監査側も同じ `_chikujou_candidates` を使い、tried / upgrades / maxed / unavailable を除いたものを candidates とする（`month_audit.py:1946-1951`）。つまり flow と監査の候補集合は内部整合しており、「監査だけが勝手に buildable と誤解した」構造ではない。

### 一覧の選択とスクロール

`src/docich/hanjuku/flows/month.py:1564-1600` の `_chikujou_step` は、築城一覧 `ぞうちくなさいます` が読める間、

1. `_chikujou_visible_maxed` で可視行のレベル5を当月 `rows_tried` に加え、
2. 未試行候補 `names` を作り、
3. 画面内で `menu_to` できる候補があれば A で選び、
4. どれも画面内で選べなければ `_chikujou_scroll` へ進み、
5. `names` が空のときだけ `chikujou_declined` で終える。

`_chikujou_scroll`（`month.py:1204-1241`）は、4行・一意な既知城名・同一 x・16px 間隔・カーソルが行上にあることを確認した場合だけ `down` を送る。打ち切り条件は、

- 同一 signature が `MENU_NAV_PRESS_LIMIT = 4` 回続く、または
- 総 press 数が `len(labels) + 4` に達する

ことである。ch12 の `CASTLE_NAMES[12]` は 11 城 + ボスの 12 件なので、総数上限は 16 である（ボスを除いても 15）。本件の `chikujou_scroll` は 12 回なので、**全一覧一周の探索上限には達していない**。残るのは「同一 signature が 4 回続いた」側、つまり `stalled >= 4` による `castle_scroll_no_progress` である。

### 12回スクロールと6回レベル5の意味

直前決定は 17:00:40.963Z から 17:00:43.393Z まで約 2.43 秒で、`chikujou_scroll` 12 回、`chikujou_max_level_seen` 6 回、最後に `chikujou_held` である。

- レベル5行を省くこと自体は設計どおりである。レベル5は最大で増築できないため、実一覧で見えた城だけ当月の選択対象から外す。
- アファニア・ファンゴルがレベル5と誤認されて省かれた可能性は、issue データ上は否定できる。もし `_chikujou_visible_maxed` でこの 2 城が読めていたなら `rows_tried` に入り、終端時に `chikujou_tried` へ併合されて監査 candidates から外れるはずだが、2 城は未試行候補として残っている。
- ただし監査 JSON の `maxed: []` は「レベル5行が無かった」ことの証拠ではない。`chikujou_max_level_seen` の `_record` 呼出し（`month.py:1577-1579`）には `month` が渡されず、監査 `_track_chikujou` の same-month evidence filter（`month_audit.py:2143-2149`）は `row.get('month') == month` を要求するため、これらの行は maxed/unavailable 集計から落ちる。6回のレベル5確認と `maxed: []` の矛盾は、主にこの診断欠陥で説明できる。

### なぜ未試行 2 城へ到達できなかったか

確実に言えること:

- 探索回数上限ではない（12 < 16）。
- 2 城をレベル5として誤って省いた経路ではない（省かれていれば candidates に残らない）。
- `chikujou_held` の理由文は「移動が効かないか探索上限に達したため」だが、実際には上記のとおり停滞側である。

確実ではないこと:

- 実一覧にアファニア・ファンゴルの行が存在しなかったのか、
- 行は存在したが 4 回連続 no-progress の前に露出しなかったのか、
- ごく短時間の入力無効・画面遷移で走査が途中停止したのか。

issue に埋め込まれた `decisions.before` は `month_audit._decision_rows`（`month_audit.py:3243-3257`）経由で、`chikujou_scroll` / `chikujou_held` の `observed_metric.visible`、`scrolls`、`stalled` を落としている。このため最後に見えていた 4 行を issue から復元できず、下位機構の一意特定には runtime の `hanjuku_decisions.jsonl` と `hanjuku_frames/` が必要である。

それでも分類は (a) である。アファニア・ファンゴルは半角/全角バリアントを持たない通常のカタカナ名で、`STATUS_NAMES` は空、`_chikujou_castle_key` は NFKC fold 済みである。名不一致が確定的に説明できる既知の `イデ！` 型ではない。候補がある限り選択またはレベル5/拒否の実測へ進むべき設計に対し、実測不能なままの stalled abort は正常完了ではない。

## `chikujou_held` → `chikujou` → `chikujou_skip_unexplained` → `month_done` の経路

### fail-closed 部分は設計どおり

`_chikujou_scroll` は stalled/上限で `sub['declined']` と `sub['aborted']` を立て、B で戻る（`month.py:1227-1234`）。月メニューへ戻ると `_finish_month_sub`（`month.py:1838-1879`）が、

- verified な支払い・増築結果なし、
- `cost_declined` なし、
- `aborted` あり、

なので `shop['chikujou'] = 'skipped'` にし、「築城を見送った／結果と支払いを照合できないため追加の増築は行わない」という `chikujou` を記録する。城や支払いを推測しない限り、この終端は設計どおりである。

### しかし「月一の購入を終了」は設計上の正当な築城省略理由ではない

`month_done` の理由「月一の購入を終了」は、月次購入処理の終了を記録しているだけであり、出撃優先・時間切れ・候補不在を判断した記録ではない。VERIFY-5696 が確認したとおり、築城走査を pending/active 出撃のために早める分岐は v1525 にも現行 main にも存在しない。本件でも `order_start` は 0 で、出撃優先判断の形跡はない。したがって「月一の購入を終了したから築城しなくてよい」という設計内理由にはならない。

### `chikujou_skip_unexplained` は本件では誤記

`_note_chikujou_skip_unexplained`（`month.py:753-808`）は、同じ月の `chikujou_held` が見つかれば `chikujou_skip_after_hold` を記録し、`unexplained` にはしない設計である。しかし実装は `mem.get('_records')` だけを逆順に見る（`month.py:781-788`）。

`_records` は永続ログではなく観測ごとの出力バッファである。orchestrator は各観測の開始で `mem['_records']=[]` に初期化し（`orchestrator.py:1784`）、返却時に `updated['_records'] = mem.pop('_records')` として policy memory から取り出す（例 `orchestrator.py:1876`）。本件の `chikujou_held` は 17:00:43.393Z、`chikujou`/`month_done` は 17:00:43.790Z で、別観測である。したがって `month_done` を記録する観測時点の `mem['_records']` には直前観測の `chikujou_held` が残っておらず、`held_reason` は見つからない。

`tests/test_hanjuku_chikujou_skip_note.py` は `mem['_records']` に `chikujou_held` を直接入れて同関数を呼ぶため、この観測またぎの失敗を固定できていない。結果として本件では `chikujou_held` が存在するのに `chikujou_skip_unexplained` が記録された。これは監査上の誤検知ではなく、**実装の説明不足（診断記録の実欠陥）**である。

## #5696 との関係

VERIFY-5696（結論 (b)）は、築城の全候補探索そのものが v1525/現行 main の設計であり、月次経済処理が pending 出撃より優先されるために出撃完了が間に合わなかった、という時間配分の問題だった。owner メモの「次回に設計＋テスト見直しで対応」は、その優先順位・打ち切り設計に関するものである。

本件との共通点:

- 築城一覧は未試行候補を全て探す設計で、出撃を見て早期中断する経路はない。
- v1525 から現行 main まで、この優先順位と scroll 実装は変わっていない。

本件との相違点:

- #5696 は `order_start` があり、出撃キャンセル `sortie_cancelled` が主対象だった。本件は `order_start` 0 で、主対象は築城そのものの未実施である。
- #5696 は「時間を使いすぎたが設計どおり」、本件は「候補に到達せず走査が失敗した」。時間配分を変えても、候補行未到達や入力停滞の原因は消えない。
- したがって #5696 の結論 (b) は本件へ適用できない。本件は別経路であり、#5696 で扱われた設計見直しだけではカバーされない。

既存修正との関係では、#5301（城名 fold）はアファニア・ファンゴルの plain カタカナ名を変えない。#5320/#5338 の skip 診断は本件の `unexplained` 誤記を防げていない。#5274 系のイベント文保護があっても scroll no-progress 自体は変わらない。本件を修正する PR が現行 main にマージされた形跡は確認できなかった。

## なぜ (b)・(c)・(d) ではないか

- **(b) 設計どおり・既存修正でカバー済みではない**: fail-closed の B 復帰と skipped 終端は設計どおりだが、staffed+affordable な候補へ到達できなかった根本原因は設計目標の未達である。現行 main に修正は入っていない。
- **(c) 誤検知ではない**: 監査と flow が同じ候補ロジックで内部整合し、起票降格条件（refusal / hold / menu unreadable / reserve / cost decline）はいずれも偽である。候補 2 城が残った事実自体は監査データ内で一貫している。
- **(d) 判定不能ではない**: 「実欠陥か設計どおりか」は判定できる。判定不能なのは、scroll no-progress の下位機構（行未描画か入力停滞か）の一意特定だけである。これは (a) の中の残件であって、分類を変えない。

## 再現条件（コードレベル）

少なくとも次の条件がそろうと同型になる。

1. `_chikujou_candidates` に 4 行窓の外へ残る未試行候補がある。
2. 可視行はレベル5または当月試行済みで、選択可能な候補が画面内に無い。
3. `_chikujou_scroll` が `down` を返すが、候補行が選択可能になる前に同一 signature が 4 観測続く。
4. `chikujou_held` で `declined+aborted`、B で戻り、`_finish_month_sub` が verified な支払い/増築なし・`cost_declined` なしと判断する。
5. upgrades 空のため `shop['chikujou']='skipped'` となり、月終了時に監査が `chikujou_available_at_end` を high で起票する。

## 残件（今回は修正しない）

- runtime `g1190-ch1v9` の `hanjuku_decisions.jsonl` 完全版で、12 回の `chikujou_scroll.observed_metric.visible` と最後の `chikujou_held.observed_metric`（`visible` / `scrolls` / `stalled`）を確認し、アファニア・ファンゴルが一度も可視化されていないのか、最終 4 行のどこかで入力が止まったのかを確定する必要がある。
- `hanjuku_frames/` で同時刻の一覧を読み、アファニア・ファンゴルの実描画・一覧順・レベル値を確認する必要がある。
- 診断側では、`chikujou_scroll` / `chikujou_max_level_seen` / `chikujou_held` に `month` を付けるか、監査の evidence filter を month 欠落の同月 decision に対応させる必要がある。現在は `maxed` や終端可視行が issue から落ちる。
- `chikujou_skip_after_hold` 分岐は観測またぎで機能していない。`_records` の出力バッファではなく、直近の `chikujou_held` を shop/month_sub 等の永続状態に残すか、decision log から same-month を引く必要がある。
- ローカルの git object には `0d88128af`（`feat(audit): retain terminal chikujou hold evidence`）と関連する `5d01f8fa1` / `f061db8d5` が存在し、`visible` / `scrolls` / `stalled` を incident へ残す方向の診断強化だったが、現行 HEAD の `month_audit.py` には入っていない。マージ済み PR としては確認できなかった。
- python3 / sha256sum が拒否されたため、sha256 の直接再計算は未実施。raw 文字列は上記のとおり確定しており、検品ゲートでは correct raw での一致が記録済みである。
