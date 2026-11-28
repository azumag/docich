# VERIFY-5818 — [半熟英雄] 月内出撃ゼロ: ch12 出撃開始後に発射されない

## 対象と検証範囲

- 対象: azumag/docich#5818
- 判定: `zero_sortie` / confidence `high` / signature `departure_failure`
- fingerprint: `5ed9f5e477638b89`
- occurrence / incident_id: `7ab80f525df222ea6fe5`
- key: `5ed9f5e477638b89/19-11/253682`
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1597-chikujou-defer-reason`
- 月: ch12 `19-11`（1959 観測 / 426.0 秒 / max gap 0.8 秒）
- 出撃: 確定 0 / 発射 0 / `order_start` 5 / `order_launched` 0
- 検証範囲: read-only。issue 本文（公開 issue ページ）、現行 main のコード、同ランの #5811/#5812、既存 VERIFY の静的照合。本番 decision log はローテーション済みの前提で参照しない。コード修正・commit・push・PR 化なし。

## 結論: **(b) 設計どおり**（ただし owner 指示により現行 main では改善済み）

月内出撃ゼロは監査の誤検知ではなく、実際に発射 0 だった。ただし原因は #5770 の「読めない切り札一覧 → 空携行取消し」ループでも、#5812 の「`order_start` 前で housekeeping に map を取られる」前段枯渇でもない。本件は **`order_start` 後に出撃元城へ入り、将軍一覧の走査までは進むが、ボス出撃で要求される本人・カーソルの厳密確認を通せず、意図された bounded cancellation / pending 戻しで発射確認へ進めなかった**経路である。

同ランの #5811 が同じ停止を単件で明示している: `general_list_scanned` の完了後も `出撃一覧の本人・カーソルを確認できないため保留` が連続し、`general_list_hold_exhausted` → `order_failed` になっている。#5811 側の検証は、この bounded cancellation を **(b) 設計どおり**と判定している。#5818 の月内集計でも `general_list_scanned` 4 / `general_list_hold_exhausted` 2 / `order_failed` 2 / `sortie_hold_abandoned` 3 で、5 回の `order_start` がいずれも発射前の bounded な中断系で終わったことと矛盾しない。`sortie_input`、`card_pick`、`sortie_confirm`、`sortie_departure_requested` が月内 0 なので、カード選択や出撃確認以前に止まっている。

fail-closed の本人確認と有限化された取消し自体は v1597 の設計どおりであり、翌 5 分では出撃 3 / 回復 yes / 初出撃 89.5 秒なので、発射パイプラインの恒久的破損でもない。一方で owner は #5833 で「将軍一覧の確認が 8 回通らなかったための bounded cancellation なら、そっちを直すべき。以降そういう運用にして」と指示し、現行 main には `hanjuku-chart-v1610-general-list-recovery` として、完了済みの将軍一覧走査を証拠に strict 確認を選択本人の行へ絞る改善が入っている。したがって本件は **当時の設計どおりだが、現在の owner 基準では改善対象だった事案**と判定する。#5790/#5806 はいずれも本件をカバーしない。現行 main での実機再発がないことまでは本 read-only 検証では確認していない。

## fingerprint / occurrence の再計算

現行 `src/docich/hanjuku/month_audit.py` の zero_sortie 系 builder が使う式は次の通り。

- `_incident_id`: `sha256(f"{runtime_id}|{generation}|{chapter}|{month}".encode('utf-8')).hexdigest()[:20]`
  - 設定箇所: `_build_incident` が `_incident_id(identity, observed.get('chapter'), observed.get('month'))` を呼ぶ（現行 L3540-L3542, L3721）。
- `_fingerprint`: `sha256(f"{chapter}|{kind}|{'+'.join(signals)}".encode('utf-8')).hexdigest()[:16]`
  - zero_sortie では `signals` ではなく primary signal 1 件だけを `[primary]` で渡す（現行 L3545-L3547, L3722-L3724）。
  - primary は `order_start_without_launch` を含む signal 列から `departure_failure` になる（現行 L286-L287, L3429-L3430, L3533-L3537）。

本件の入力値:

| 項目 | raw input | 期待される digest | issue の値 | 結果 |
| --- | --- | --- | --- | --- |
| fingerprint | `12\|zero_sortie\|departure_failure` | `5ed9f5e477638b89` | `5ed9f5e477638b89` | 既存検証記録と式・入力から一致を確認（下記） |
| occurrence / incident_id | `g1190-ch1v9\|1190\|12\|19-11` | 未計算 | `7ab80f525df222ea6fe5` | **未計算** |

実行環境で `python3` のハッシュ再計算コマンドが承認却下されたため、新規の sha256 計算はできなかった。fingerprint については、同一 raw input `12|zero_sortie|departure_failure` を計算した既存 `VERIFY-5702.md` が `5ed9f5e477638b89` を記録しており、現行 main の式も同じであるため、式・入力・issue 値の整合は確認できる。occurrence は raw input と式を上記に残すが、本検証での再計算は未実施である。検品ゲート側で再計算が必要。

## 現行コードから見た `order_start` 5 / `order_launched` 0 の意味

### 1. `order_start` は発射ではなく、出撃手順の開始

`flows/map.py` L2735-L2747 で、map planner が startable な order を選び、`sortie_launch_pending` を立てて `order_start` を記録する。これは城へ向かう手順の開始であり、発射確定ではない。実際の発射側は `_finish_order(..., 'launched')` などが `order_launched` を記録する（`strategy/orders.py` L3351-L3381）。

したがって `order_start` 5 / `order_launched` 0 は、「5 回手順に入ったが、将軍選択・カード選択・出撃確認・目標確定のどこかで完走しなかった」ことを意味する。

### 2. 本件では将軍一覧の厳密確認で止まった

issue の月内 counts の対応:

- `order_start`: 5
- `general_list_scanned`: 4
- `general_list_hold_exhausted`: 2
- `order_failed`: 2
- `sortie_hold_abandoned`: 3
- `sortie_input` / `card_pick` / `sortie_confirm` / `sortie_departure_requested` / `order_launched`: 0

現行コードでは、ボス出撃の選択本人を general list で厳密に確認する。確認できない場合は `_hold_general_list_bounded` が保留し、上限で `general_list_hold_exhausted` を記録して `_finish_order(..., 'failed')` にする（`flows/deploy.py` L1074-L1095, L2622-L2699）。一方、画面分類・確認の general な保留上限では `_hold_deploy` から `sortie_hold_abandoned` になり、adjusted step は failed ではなく pending へ戻る（同 L962-L995, L1979-L1996）。

`general_list_hold_exhausted` 2 = `order_failed` 2、`sortie_hold_abandoned` 3 で、合計は `order_start` 5 と同じ数になる。ただし月内には `order_done` 1 もあり、decision log がないため各 record と 5 回の開始の一対一対応は証明できない。それでも少なくとも 2 件は general-list strict confirmation の bounded failure、3 件は出撃画面分類/確認の bounded cancel であり、いずれも発射前の fail-closed 経路である。したがって、少なくとも集計上は boss wave の開始が繰り返し発射工程へ進めなかった構造と矛盾しない。

### 3. pending の boss wave が残る理由

月末の計画には `A:4e6f38be:M2488c1`（ターシュ）、`M300e71`（レモン）、`M320ed2`（ランプータン）、`M7596f4`（セミヨン）、`Mbb4da7`（ヘーゼル）、`Mbd5059`（ライム）など、いずれも `attack / ディランダ→ボス` の pending が残る。ch12 の現行チャートでは `12-A3` が主人公をディランダへ拠点移動し、`12-B1` がディランダからボスへ出る設計で、source 自体は正しい（`hanjuku_chart.py` L1129-L1135, L1359-L1365）。現行の wave planner も staging ready では boss source をディランダに限定する（`strategy/replan.py` L1430-L1438）。

したがって pending は「そもそも不正な source の注文」ではない。先に選ばれた同じディランダ発 boss wave が general-list 確認で失敗し、pending 戻し・plan rearm・月次/戦闘占有を繰り返した結果、後続 wave が発射まで進めず残った状態である。`plan_step_rearmed` 9 がこの再供給を示す。issue 本文だけでは 5 回の `order_start` と個別 step の完全な対応は確定できないが、全て同じ source/目的の wave 群であり、発射確認へ到達しなかったことは counts で確定できる。

### 4. `order_dispatch_starved` と `order_failed` の意味

- `order_dispatch_starved` は、pending または boss retry の order が `ORDER_DISPATCH_STARVE_TICKS` を超えて `order_start` に届かないとき記録される。通常フェーズでは記録だけ、boss-only フェーズでは前景ワークへの譲りにも使われる（`strategy/orders.py` L2535-L2580）。
- `order_failed` は `_finish_order(..., 'failed')` が出す terminal record である（`strategy/orders.py` L3351-L3386）。
- `sortie_hold_abandoned` は terminal ではなく、adjusted order を pending へ戻す cancel 経路なので `order_failed` を伴わない（`flows/deploy.py` L1979-L1996）。

このため `order_start` 5 に対し `order_failed` 2 だけでも矛盾しない。残り 3 は pending に戻り、さらに他の pending wave と一緒に dispatch 待ちへ戻る。`order_dispatch_starved` の 3/5 という表記差は、issue 埋め込み JSON の月内 counts では 5 である。いずれにせよ「pending があるのに foreground が map planner へ戻らず開始できない episodes」であり、開始後の general-list 失敗とは別段の二次要因である。

## #5770 / #5790（v1593）と #5801 / #5806（v1597）のカバー判定

### #5770 / PR #5790（`hanjuku-chart-v1593-unreadable-card-source`）

対象は、切り札一覧を読めないまま出撃しようとして空携行確認に達し、empty-kit guard で取消される loop を、source 単位の unreadable-card block と全所持 census で止める修正である（`tests/test_hanjuku_unreadable_card_loop.py`, `flows/deploy.py` L3055-L3080, `strategy/unreadable_card_block.py`）。

本件月内 counts には `card_list_unreadable_depart`、`sortie_kit_empty_cancel`、`unreadable_card_loop_blocked` がなく、そもそも `card_select` や `sortie_confirm` へ到達していない。失敗しているのはカード一覧ではなく将軍一覧である。したがって **#5790 は本件をカバーしない**。

### #5801 / PR #5806（`hanjuku-chart-v1597-chikujou-defer-reason`）

対象は、出撃 deferral 後に築城が skipped になった月を `chikujou_skip_unexplained` と誤記せず、`chikujou_skip_after_deferred_sortie` として理由付きで記録する監査/診断修正である（`tests/test_hanjuku_chikujou_skip_note.py` L56-L80, `flows/month.py` L835-L852, L899-L910）。

本件では `post_month_sortie_floor` や chikujou 系が月次占有の副因として残るが、v1597 の修正は発射手順そのものを変えない。観測ランが v1597 であり、かつ本件がその適用後に発生していること自体が、**#5806 が本件をカバーしない**直接の証拠である。

## 現行 main の後続改善との関係

現行 main は `hanjuku-chart-v1610-general-list-recovery` 系で、#5833 の regression test を持つ。`tests/test_hanjuku_general_list_recovery.py` L1-L50 は「完了済み scan があり、無関係行が strict ではない場合、3 回目で選択へ回復する」ことを固定している。対応する実装は次の通り。

- `_strict_roster_failure_metric`: 誰が strict 確認に失敗したかを bounded に記録（`flows/deploy.py` L1004-L1023）
- `_scan_backed_roster`: 完了済みの走査名簿を証拠に使えるか判定（同 L1026-L1047）
- `_note_scan_backed_recovery`: scan-backed narrowing を記録（同 L1059-L1071）
- boss/general 選択部で strict failure が続いたとき scan-backed narrowing を試す（同 L2622-L2641）

この機構は、#5811 で明示された「一覧全体は読めたのに本人・カーソルの厳密確認だけが通らない」状態に正对する。したがって **現行 main は v1597 当時の主経路をかなり直接にカバーしている**。ただし本検証は read-only で、#5833 後の実機ランで同じ ch12 月が再観測されていないことまでは確認していない。

## #5812 との異同

#5812 は同ラン・同 v1597・同 ch12 だが月は `19-7` で、`order_start` 0。既存の検証コメントでは、`post_month_sortie_floor` が map を出撃計画へ明け渡した後も、launch-priority latch が `order_start` 時まで立たないため house scan が先に map frame を取り、一度も開始できなかった前段の統合優先順位欠陥と判定されている。

#5818 は `order_start` 5 あり、少なくとも出撃手順へは入っている。月次処理・戦闘は確かに副次的な占有因だが、主な停止点は dispatch 前ではなく general-list 確認である。したがって両者は同じ `zero_sortie` / ch12 / 月次占有を伴うが、失敗段階が違う別経路である。

## #5811 との関係

#5811 は同ラン `19-3` の単件 `sortie_failed` で、step `I:d8d78d:2ec91b77` / アルファルファ / ファンゴル→ボス。issue 本文には `general_list_scanned` 後に `出撃一覧の本人・カーソルを確認できないため保留` が連続し、`general_list_hold_exhausted` → `order_failed` となった時系列がある。

#5818 はこれと同じ失敗型が `19-11` に boss wave へ繰り返し起きた月次集計である。発生源の城は #5811 ではファンゴル、#5818 の残存 wave はディランダだが、失敗している画面機構は同じ general-list strict confirmation であり、城固有の問題ではない。

## なぜ (a)・(c)・(d) ではないか

- **(a) 実欠陥とは判定しない**: v1597 当時のコードは、本人・カーソルを厳密確認できない将軍を選ばず、bounded cancellation で失敗させる fail-closed 設計だった。同型の #5811 も検証上は (b) 設計どおりと扱われている。走査済みの証拠を使って回復する #5833 は owner 指示による設計改善であり、その改善前の挙動が仕様違反だったとまでは証拠から言えない。翌 5 分で回復しているため恒久障害でもない。
- **(c) 誤検知ではない**: 確定 0 / 発射 0 / `order_launched` 0 は issue の counts に明示され、翌 5 分には出撃 3・回復 yes で監査の月境界も自然である。
- **(d) 判定不能ではない**: 本番 decision log は無くても、issue の counts、月末 plan、#5811 の同型時系列、現行/後続コードの対応から主経路を特定できる。個別 step 対応の未確定は残るが結論を変えない。

## 未決の点

- `order_start` 5 件と plan 上の step の完全な対応、各 `sortie_hold_abandoned` の画面・観測メトリクス、および月内 `order_done` 1 が read-only/move だったかどうかは、ローテーション済み decision log がないため不明。
- occurrence `7ab80f525df222ea6fe5` の sha256 再計算は python3 実行が却下されたため未実施。raw input は `g1190-ch1v9|1190|12|19-11`、式は `sha256(raw).hexdigest()[:20]`。
- 現行 main の #5833 系修正が実機で ch12 boss wave を回復したかは、後続ランの観測が必要。

## 検品ゲート確認（Muse、2026-11-28 21:47 JST、現行 main a87a7fe83）

- fingerprint / occurrence を検品ゲートで再計算した。SHA-256("12|zero_sortie|departure_failure")[:16] = 5ed9f5e477638b89、SHA-256("g1190-ch1v9|1190|12|19-11")[:20] = 7ab80f525df222ea6fe5。いずれも issue の値と一致する（unit は python3 の承認却下で未計算だったが、式・入力の記述は正しかった）。
- 結論 (b) 設計どおりを維持する。現行 main のコードで次の3点を直接確認した。
  - `src/docich/hanjuku/flows/deploy.py` L999 で GENERAL_LIST_HOLD_LIMIT = 8、L1082 で上限到達時に hold を記録して order を失敗させる fail-closed の bounded cancellation がある。`general_list_hold_exhausted` は同ファイルに存在する。
  - 同ラン #5811 の検証（(b) 設計どおり）と同一の停止経路で、月内 counts（order_start 5 / general_list_scanned 4 / general_list_hold_exhausted 2 / order_failed 2 / sortie_hold_abandoned 3 / 発射系 0）と矛盾しない。
  - owner 指示（#5833）により、この機構自体の改善は既に現行 main に入っている（PR #5835、v1610-general-list-recovery：完了済み走査を証拠に strict 確認を選択本人の行へ絞る）。したがって本件に新しい fix issue は立てない。
- 修正 PR は出さない。現行 main での実機再発の有無は本 read-only 検証では確認していない。
