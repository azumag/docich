# VERIFY-5803 — [半熟英雄] 築城可能なまま月一終了: ch12 16-11

## 結論

**(a) 実欠陥**。

出撃デファー経路ではなく、築城一覧の走査が未試行候補 `バラタリア` / `ラグナグ` に到達できず、bounded な fail-closed 終端 `castle_scan_exhausted` から月一終了へ進んだ経路である。終端で B に戻り推測確定しなかったこと自体は設計どおりだが、所有・在城が記録された候補を 31 回走査しても選択行へ到達できないことは、#5704 修正後にも残る走査側の実欠陥である。

## ハッシュ再計算

現行 main `src/docich/hanjuku/month_audit.py` の `chikujou_available_at_end` 系 builder が使う式:

- fingerprint: `_fingerprint(mem.get('chapter'), kind, [kind])`
  - `_fingerprint(chapter, kind, signals)` の raw は `f"{chapter}|{kind}|{'+'.join(signals)}"`、SHA-256 hex の先頭16文字。
  - 本件入力: `12|chikujou_available_at_end|chikujou_available_at_end`
  - issue の期待値: `63706b90f609ef65`
- occurrence / incident_id: `_build_chikujou_incident` の raw は `f"{identity.get('runtime_id')}|{identity.get('generation')}|{kind}|{event.get('month')}|{mem.get('chapter')}"`、SHA-256 hex の先頭20文字。
  - 本件入力: `g1190-ch1v9|1190|chikujou_available_at_end|16-11|12`
  - issue の期待値: `b6ebc1e90ac3d43762d2`
- month key: `(fingerprint, month, month_summary.first_tick)` で、本件は `63706b90f609ef65/16-11/201070`。

`python3` の実行が承認却下されたため、この検証環境では SHA-256 を再計算できていない。上記の式と入力値を検品ゲートで再計算すること。式の項目数・文字列結合順・ truncate 幅は現行 main の該当箇所と一致させたが、本検証では独立計算未完である。

## 経路の確認

### 1. 月終了時に「築城可能」と判定されたこと自体は issue 内証拠と矛盾しない

issue 本文の監査 JSON では:

- gold 6612 / reserve 30 / affordable true / buildable true
- 未試行候補 `バラタリア`, `ラグナグ`
- 試行済み8城、増築済み6城
- 失城なし。奪取済みに `バラタリア`, `ラグナグ` がある
- 在城は `バラタリア(1)` / `ラグナグ(2)`
- 行軍中の将軍は `サムソー`, `カシス`, `キャンディー` で、候補城の在城者（`トレビス`, `ブカティーニ`, `キャロット`）は行軍中として示されていない

したがって、issue 内の世界状態だけでは「所有しているのに無い城」とは読めず、候補は実在する staffed holdings として扱うのが自然である。監査の事実検出を誤検知と断定する根拠はない。

### 2. 走査は終端まで到達せず、full-cycle fallback で止まる構造

現行 main の `_chikujou_scroll`（`src/docich/hanjuku/flows/month.py`）は:

1. 4行の可視ウィンドウが厳密なレイアウトで読めた場合だけ走査する。
2. `_chikujou_scan_direction` で、章ごとの `CASTLE_NAMES` の順序を「一覧の catalog 順」とみなし、visible rows の index 範囲に対して未試行候補が上か下かを選ぶ。
3. `end_down && end_up`、または `since_new_row >= len(labels) + 4` で `castle_scan_exhausted` にする。

ch12 の現行 `CASTLE_NAMES` はコード上

`アルマムーン, ワフェルダ, ブラックランド, バラタリア, ラグナグ, ユーフォニア, アトランテ, ...`

の順である。一方、本件の最終可視行は

`ワフェルダ / ブラックランド / ユーフォニア / アトランテ`

である。コードの catalog index では `ワフェルダ=1, ブラックランド=2, バラタリア=3, ラグナグ=4, ユーフォニア=5, アトランテ=6` なので、1・2・5・6 が同じ4行ウィンドウに連続して見える一方で 3・4 が欠けることは、「catalog 順どおりの連続ウィンドウ」という走査前提と矛盾する。

この状態では `_chikujou_scan_direction` も候補を正しく追えない。visible の index 範囲は low=1 / high=6 で、候補 index 3・4 はその内側にあるため `above` にも `below` にも入らず、方向選択は現在方向を維持するだけになる。実際には候補行がこのウィンドウの外にあるのに、コード上は「 visible 範囲の内側にあるはず」として扱われるため、候補へ向かう方向が選べない。逆方向プローブと full-cycle fallback はあっても、誤った catalog 位置を毎フレーム再評価するため、候補行へ収束せず、新規行なしの bounded 上限へ進みうる。

`len(labels)` は ch12 では boss を含めて12なので、fallback 閾値は `since_new_row >= 16` である。`since_new_row` は画面 signature が変化したときだけ進み、同一画面や停滞回復の入力は別カウンタで消費されるため、16 changed windows に達するまでに `scrolls=31` になることは矛盾しない。terminal diagnostic の `stalled: 0` は累積停滞数ではなく、終端時点の連続同一 signature 数にすぎない。

さらに現行コードの `end_down` 証明は、カーソルが最下行にあり、かつその最下行が catalog 最終行であることを要求する。ch12 を含む多くの章で catalog 最終行は `ボス` だが、ボス城は自軍の増築対象として築城一覧に出ないため、この `end_down && end_up` による「両端到達」は実質成立しにくい。したがって本件の `castle_scan_exhausted` は、文面どおり「端まで往復」を証明したというより、`since_new_row` の full-cycle fallback で停止した可能性が高い。この停止自体は fail-closed として妥当でも、候補へ到達できない走査前提の欠陥を覆い隠すものではない。

### 3. #5704 修正後も残る別段の欠陥

#5704/#5713（v1569）が直したのは、最初の repeated window で打ち切る旧欠陥であり、現行コードは逆方向プローブ・入力停滞待ち・full-cycle bound を持つ。しかし今回の観測が示すように、章ごとの catalog 順と実際の可視ウィンドウの関係が崩れると、双方向走査でも候補方向を選択できない。よって本件は #5704 の単純な未修正再発ではなく、v1569 の走査モデルに残る実欠陥である。

## 出撃デファー経路との切り分け

本件に出撃デファーの関与は認められず、純粋な走査枯渇である。

- issue の `decisions.counts` に `chikujou_deferred_sortie` は無い。比較対象の #5796 では同レコードが明示されている。
- 直前は `chikujou_scroll` が連続し、その後 `chikujou_held` で終わる。deferral は `presses >= 1` で探索を打ち切って B に戻る経路なので、発火していれば同月レコードが残る。月2回の deferral 上限後に通常走査へ戻る可能性は理論上あるが、本件の counts に deferral 自体が無く、その仮定を裏付ける記録はない。
- 本件の active step `A:5f77e647:Mfc34bd` は plan 上 `purpose: move`（アルマムーン→ブラックランド）である。現行 `active_sortie_launch_priority` の対象 purpose は `attack`, `retake`, `boss`, `invade` であり、`move` は launch-priority deferral の対象外である。issue の `order_start 1 / order_launched 0` や signal `order_start_without_launch` は、この move 注文の停滞を示す別問題であり、築城走査の deferral 証拠ではない。
- #5795/#5796 は #5696/#5702 の出撃デファーで築城探索を打ち切る経路だった。#5801 は、その deferral が `chikujou_held` / `chikujou_last_hold` を残さないため月終了時の診断が `chikujou_skip_unexplained` に倒れるという診断欠陥だった。現行 main では v1597 以降 `_note_chikujou_skip_unexplained` が同月の `chikujou_deferred_sortie` または永続 `chikujou_defer` / `sortie_launch_pending` を見て `chikujou_skip_after_deferred_sortie` を記録するが、本件はそもそも deferral レコードが無く、terminal diagnostic が `castle_scan_exhausted` なので別経路である。

## 未決・残存確認事項

- 本番 decision log / frame はローテーション済みで存在しないため、ネイティブ一覧の実際の並びを本件フレームから直接確認することはできない。ただし、issue の可視4行と現行 catalog 順だけでも、現行走査の位置モデルが観測と矛盾することは確認できる。
- `castle_scan_exhausted` に至った内部分岐が `since_new_row` fallback だったことはコード構造と boss を含む end 条件から強く示唆されるが、terminal diagnostic が `end_down` / `end_up` / `since_new_row` を保存しないため、issue 本文だけでは厳密な分岐名は特定できない。
- fingerprint / occurrence の SHA-256 再計算は `python3` 承認却下のため未実施。式と入力値は上記のとおり。

## 参照した現行 main の主な箇所

- `src/docich/hanjuku/month_audit.py`: `_chikujou_snapshot`, `_build_chikujou_incident`, `_fingerprint`, `_month_key`
- `src/docich/hanjuku/flows/month.py`: `_chikujou_scan_direction`, `_chikujou_catalog_end`, `_chikujou_scroll`, `_month_chikujou`, `_note_chikujou_skip_unexplained`
- `src/docich/hanjuku/strategy/economy.py`: `_chikujou_candidates`, `_chikujou_affordable`, `_chikujou_reserve`
- `src/docich/hanjuku/strategy/orders.py`: `LAUNCH_PRIORITY_PURPOSES`, `active_sortie_launch_priority`
- `src/docich/hanjuku_chart.py`: ch12 `CASTLE_NAMES`

## 検品ゲート確認（Muse、2026-11-28 17:47 JST、現行 main 5ead25262）

- fingerprint / occurrence を検品ゲートで再計算した。SHA-256("12|chikujou_available_at_end|chikujou_available_at_end")[:16] = 63706b90f609ef65、SHA-256("g1190-ch1v9|1190|chikujou_available_at_end|16-11|12")[:20] = b6ebc1e90ac3d43762d2。いずれも issue の値と一致する。
- 結論 (a) 実欠陥を維持する。現行 main のコードで次の3点を直接確認した。
  - ch12 の `CASTLE_NAMES` は12城で、バラタリア=3、ラグナグ=4 である。issue の最終可視行 ワフェルダ(1) / ブラックランド(2) / ユーフォニア(5) / アトランテ(6) は catalog 連続ウィンドウ（4行）にはなり得ない。走査の「可視行は catalog 連続」という前提が観測と矛盾する。
  - `_chikujou_scan_direction` は、残り候補 [3, 4] が可視範囲 low=1 / high=6 の内側にあるため above / below のどちらにも入らず、現在方向を返すだけになる。候補へ向かう方向が選べない。
  - `_chikujou_catalog_end` の down 側は、最下行のキーが catalog 最終行（ch12 ではボス）と一致することを要求する。ボス城は築城一覧に出ないため、end_down は実質成立しない。`castle_scan_exhausted` は「端まで往復」の証明ではなく、`since_new_row >= len(labels) + 4`（ch12 では16）の full-cycle fallback による停止である。
- 出撃デファー経路との切り分け（issue に `chikujou_deferred_sortie` が無く、terminal diagnostic が `castle_scan_exhausted` である点）は検証記録のとおりで、純粋な走査枯渇である。
- 未決の点として残るのは、ネイティブ一覧の実際の並びを本件フレームから直接確認できない点と、terminal diagnostic が内部分岐（end_down / end_up / since_new_row）を保存しない点である。(a) の根拠は、可視行と現行 catalog 順の矛盾、および方向選択・端判定のコード構造である。
- 修正は別途、修正 PR とセットで割り当てます。
