# VERIFY-5815 — [半熟英雄] 野営放置: ch4 ピオーネ (3ヶ月)

- 対象: azumag/docich#5815。kind `camp_abandoned` / confidence `high`。
- ラン: `g1373-b4c4126f` / generation `1373` / `hanjuku-chart-v1597-chikujou-defer-reason`。
- 検証範囲: read-only。git 書き込み・`gh` コマンド・コード修正なし。本番 decision log はローテーション済みで参照しない。
- 現行コード: worktree 内 main 相当（`BOT_VERSION = hanjuku-chart-v1607-chikujou-noncontiguous`）。
- `DOCICH_HANDOFF_PATH` は環境に見えないため handoff 正本は未読と記録する（AGENTS.md §1）。

## 結論: **(d) 判定不能**

判定不能の範囲を限定すると、**監査が `camp_abandoned` を発火したこと自体は契約どおりで誤検知とは言えない**が、issue 本文と現行コードだけでは、ピオーネの3ヶ月滞留が「boss_move / recall 解消経路の実欠陥」だったのか、「ch4 四季王城サイト包囲の許容挙動と月末・月次処理のタイミングが重なっただけ」だったのかを確定できない。

特に次の2点は確定できる。

1. **home への camp recall が発火しないのは設計どおり**:
   ピオーネは HP 46/46・broken false・城候補 `四季王城(北西)`。現行コードでは ch4 の四季王城4サイトは boss site で、無傷に近い boss-site 野営は通常の home recall ではなく、最大3ヶ月の包囲免除つき `boss_move` 解消経路へ入る。`months=3` は `BOSS_CAMP_EXEMPT_MONTHS=3` にちょうど等しく、現行コードの条件 `<= 3` ではまだ免除側である。
2. **しかし boss_move が実際にピオーネへ対して開始・送信・受理されたかは本文から証明できない**:
   本文の `flow.recall` は `await_dispatch` 中の何らかの帰還/再移動を示すが、将軍名も goal も月次監査の投影に残らない。同月の #5817 は `四季王城(南西)` 向けで、ピオーネの測定サイト `四季王城(北西)` と異なるため、現行コード上はピオーネ本人の boss_move と断言できない。

したがって「発火は正当だが、ピオーネ個人の滞留原因は未証明」が本検証の結論である。

## 1. fingerprint / occurrence の式照合と再計算結果

### fingerprint

現行 `src/docich/hanjuku/month_audit.py` の `_fingerprint`:

```python
raw = f"{chapter}|{kind}|{'+'.join(signals)}"
sha256(raw.encode('utf-8')).hexdigest()[:16]
```

`_build_camp_abandon_incident` は次で呼ぶ:

```python
_fingerprint(mem.get('chapter'), 'camp_abandoned', ['camp_abandoned'])
```

したがって本件の入力は:

```text
4|camp_abandoned|camp_abandoned
```

期待値は issue 記載の `3bd148295593ce20`。同一入力の再計算一致が既存 `VERIFY-5421.md` に記録されており、式・入力・issue 値は整合する。ただし**本検証環境では python3 / sha256sum の実行が承認却下されたため、今回の独立再計算は未実施**。

### occurrence / incident_id

`_build_camp_abandon_incident` の式:

```python
raw = (f"{identity.get('runtime_id')}|{identity.get('generation')}|"
       f"camp_abandoned|{stay.get('general')}|{stay.get('first_tick')}")
sha256(raw.encode('utf-8')).hexdigest()[:20]
```

issue の値を代入した入力は:

```text
g1373-b4c4126f|1373|camp_abandoned|ピオーネ|56823
```

期待値は issue 記載の `77280f44b8a8771287ee`。こちらも**今回の独立再計算は未実施**。式と入力値は一意に対応するが、ハッシュ値の一致自体は検品ゲート側で再計算が必要。

## 2. 野営からの帰還が発火しない条件

### 2.1 通常 home recall ではなく boss-site 包囲免除へ入る

現行コードの経路:

- `post_month_status._boss_camp_holds_siege`: `marker.castle_candidate` が `chart.is_boss_castle(...)` で、broken でない camp を包囲継続と見なす。
- `_boss_move_ready`: HP が最大HPの半分より大きければ、野営メニューの `いどう` で boss へ再移動する `boss_move` 対象。
- `_tasks`: `boss_unlocked` かつ boss-site camp で、免除月数が `<= BOSS_CAMP_EXEMPT_MONTHS (3)` なら、home への `recall` ではなく `boss_move` を選ぶ。免除切れ（実装上は count が 3を超えた場合）や、ch4 site の king down が測定済みなら `recall` へ切り替わる。
- ch4 の四季王城4サイトは `hanjuku_chart.py` に boss site として登録されている。`四季王城(北西)` はその1つ。

ピオーネは HP 46/46、broken false、`四季王城(北西)`、継続ちょうど3ヶ月なので、**home recall が発火しないこと自体は設計どおり**である。反面、この時点で選ばれるべきなのは `boss_move` であって、何もしないことではない。

### 2.2 `boss_move` の失敗・未実行は本文だけでは確定できない

issue 本文には `flow.recall` が `await_dispatch` / steps 2 として写る。しかし month_audit の `_summarize_flow` は recall の `stage` / `steps` / `target` / `kind` のみを写し、`target` は list なので本文の表には出ない。`goal` / `general` / `boss_move` も投影されない。このため、この recall が:

- ピオーネの `boss_move` だったのか、
- 同時期の別将軍の `boss_move` だったのか、
- 前月から持ち越された別の camp recall だったのか

は本文だけでは分からない。

現在の月次処理は `month_done` 後の `post_month_status_armed` で始まる。一方 `camp_abandoned` は月替わり直後の最初の観測でも評価される。実際、本件の month_summary は観測1回 / 0.0秒 / first_tick 58790 で、月次処理より先に発火している。このため「issue 発火時点でピオーネの解消タスクがまだ動いていない」こと自体は異常ではなく、その後に失敗したかどうかを本文からは追えない。

### 2.3 観測1回 / 0.0秒の意味

`camp_abandoned` は当月を1か月間観察してから判定するのではなく、永続化された `general_statuses` / `camp_status_seen` と月境界を使って継続月数を数える。docs/operations/hanjuku-month-audit.md の契約も「月が変わっても camp 行が残っていれば継続月数を+1する」である。

したがって観測1回 / 0.0秒は、**発火タイミングが月替わり直後であることを示すだけで、直ちに誤検知の根拠にはならない**。ただし、同じ理由で当月内の boss_move / recall 成否は全く示していないので、「実欠陥」と断言する証拠にもならない。これが判定不能の主因である。

## 3. #5816 との fingerprint/key 同一性

#5815（ピオーネ）と #5816（タピオカ）は同じ fingerprint `3bd148295593ce20`、同じ key `3bd148295593ce20/8-10/58790` を持つ。これは式上当然である。

- fingerprint は `chapter|kind|primary_signal` だけで計算され、将軍名も発生地点も含まない。ch4 の `camp_abandoned` は全件同じ値になる。
- key は `(fingerprint, month, month_summary.first_tick)` であり、camp の `first_tick` ではない。両者とも同じ月 `8-10` で、month_summary.first_tick は 58790 なので同じ key になる。
- 一方 `incident_id` には将軍名と camp の first_tick が入る。タピオカは camp first tick 56866、ピオーネは 56823 で、別 incident である。

#5721 の `(fingerprint, month, first_tick)` 再起票抑制は、順次実行ならこの同一月の2件目を抑止する。現行 `file_issue` は:

1. open issue を fingerprint で探し、見つかれば新規 issue ではなく再発コメントにする。
2. open issue が無ければ closed 含む全 issue から同じ `(fingerprint, month, first_tick)` を探し、あれば `duplicate_month` で抑止する。

しかし camp_abandoned の生成側は `_track_camp_abandon` が将軍ごとに `_append_incident` し、そのたびに `spawn_issue_filer` で**detach された子プロセス**を起動する。複数将軍が同じ観測で3ヶ月境界に達すると、複数の filer が並行に走り、双方が「まだ fingerprint の open issue が無い」状態を読んで `issue create` する競合があり得る。#5721 の抑制は検索時点で既存 issue が見える場合だけ有効で、この同時起票 race は防げない。

したがって #5815/#5816 は「同一 incident を将軍ごとに重複起票したもの」ではなく、**将軍ごとの別 incident だが、同一月 key に対する並行起票 race で別 issue になった可能性が高い**。この race 自体は起票パイプラインの問題だが、ピオーネが野営したゲーム内原因とは別問題である。

なお #5816 のタピオカは城候補 `ビクトリア` であり、ピオーネの四季王城 boss-site 包囲経路とはゲーム内の帰還分岐も異なる。タピオカは通常城 camp なので、現行コードでは boss 免除ではなく通常 recall の対象である。

## 4. #5817（camp_recall_unconfirmed、四季王城(南西)）との関係

#5817 は同じラン・同じ月の `camp_recall_unconfirmed` で、帰還先は `四季王城(南西)`、input receipt は confirmed、`picker_closed=False`、status `arrival_unconfirmed`。直前には `camp_recall_scene_covered` があり、送信済み帰還ピッカーが月初場面に覆われたことが分かる。

ピオーネ本人の boss_move である可能性は排除できないが、現行コード上は低い:

- 月次 `boss_move` は roster marker が示す同一サイトを goal に使う。ピオーネの marker は `四季王城(北西)` なので、彼の boss_move なら goal も北西になるはずである。
- #5817 の goal は南西であり、少なくとも画面証拠上は南西側の別 camp / 別将軍の boss_move と読む方が自然である。
- `general` は null だが、これは現行 `recall.py` の terminal metric が `state.get('fort')` の場合だけ general を出すためで、月次 task では unknown になり得る。general null はピオーネである証拠にも否定証拠にもならない。

ただし #5817 は、同じ run/month で boss-site 向けの camp 移動が「送信後に受理・到着を確認できない」終端へ落ちたことを示す。さらに現行コードでは、月末接近時に新規タスクを延期する条件が `job['kind'] == 'recall'` に限られ、`boss_move` は同じ月末 defer を通らない。boss_move は recall と同じく camp menu → いどう → destination picker を経るため、月初場面に picker を覆われる同型リスクがある。

この経路は本件の滞留を説明し得る **未解消の疑点** だが、#5817 がピオーネ本人のものと証明できない以上、#5815 の直接原因とは断定できない。

## 5. 過去の同判定との異同

- #5789（グレイ 3ヶ月・ch10・実欠陥）: ch10 White House camp では boss_move が唯一の月次解消経路であり、recall flow を失った job を同月内に再キューしない欠陥が確認された。現行 main には `boss_move` を含む requeue 回帰テストがある。本件も boss-site camp という点では同じ系統だが、#5815 本文にはピオーネの boss_move job が flow 喪失で消えた証拠はなく、#5789 の機構をそのまま適用できない。
- #5792（ヴィーナス 6ヶ月・ch3・検証中）: ch3 は ch4 の四季王包囲免除とは別の地形・経路なので、直接の根拠にはしない。
- #5421（キッシュ 3ヶ月・ch4）: fingerprint は本件と同じ `3bd148295593ce20`。あちらは v1477/v1481 で修正済みの「免除カウント消失 + 古い retreat recheck の乗っ取り」が実測で確認されていた。現行 main にはその修正（冪等計数、`camp_watch_hint`、`task_recall` / `release_for_task`）が残る。本件 v1597 はそれより後なので、#5421 と同じ已修正原因とは断定できない。

## 6. 現行コードで残る疑点（結論を左右するが単独では因果確定しないもの）

1. `boss_move` が月末 defer の対象外であること（`job['kind'] == 'recall'` のみ）。#5817 の scene-covered unconfirmed と整合するが、ピオーネ本人の task か未証明。
2. input receipt が記録された recall/boss_move は、受理未確認でも `_recall_was_dispatched` により同月 requeue されない。移動が実際には受理されていない場合、次の月次 pass まで待つ設計である。送信済み input が本当に効かなかったケースの切り分けは decision log が必要。
3. `camp_abandoned` は3ヶ月目の月替わり直後に発火するが、boss-site 包囲免除もちょうど3ヶ月まで有効なので、健康な boss-site camper は設計どおりでも監査 issue になり得る。言い換えると、この issue 単体は「解消経路が壊れた証拠」ではなく「包囲免除の境界に達した証拠」に近い。

## 7. 未決の点

- ピオーネに対して実際に `boss_move` task が立ったか、立った場合に `camp_enter` → `いどう` → destination A のどこまで進んだか。ローテーション済み decision log が必要。
- #5817 の general / camp が誰だったか。ピオーネと同一でない限り、直接証拠にはできない。
- 8-8・8-9 の各月で boss_move が戦闘に入って敗退したのか、入力未受理で野営のままだったのか。前者ならゲーム内 attrition の範囲、後者なら実欠陥の可能性が高い。
- fingerprint / occurrence の今回の独立ハッシュ再計算。式と入力は上記のとおり確定しているが、実行承認が得られず未計算。

## 検品ゲート確認（Muse、2026-11-28 20:13 JST、現行 main 8f0b8e680）

- fingerprint / occurrence を検品ゲートで再計算した。SHA-256("4|camp_abandoned|camp_abandoned")[:16] = 3bd148295593ce20、SHA-256("g1373-b4c4126f|1373|camp_abandoned|ピオーネ|56823")[:20] = 77280f44b8a8771287ee。いずれも issue の値と一致する。
- 結論 (d) 判定不能を維持する。現行 main のコードで次の3点を直接確認した。
  - `BOSS_CAMP_EXEMPT_MONTHS = 3` で、boss-site 野営は `months <= 3` の間は包囲免除側である。ピオーネはちょうど 3ヶ月で、まだ免除側である。
  - `四季王城(北西)` は `hanjuku_chart.py` に boss-site 座標として登録されている。ピオーネは HP 46/46・broken false で、`_boss_move_ready` の条件（HP > 最大HP/2）を満たす。
  - 月次監査の `_summarize_flow` は recall の stage / steps / target / kind のみを投影し、general / goal / boss_move を残さないため、issue 本文の `flow.recall` がピオーネ本人の boss_move かは確定できない。同月の #5817 は `四季王城(南西)` 向けで、測定サイトが異なる。
- したがって「監査の発火は契約どおりで誤検知ではないが、ピオーネ個人の滞留が boss_move の未実行による実欠陥か、包囲免除の許容挙動かは、issue 本文と現行コードだけでは確定できない」という (d) の範囲は正しい。修正 PR は出さない。
