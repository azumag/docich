# VERIFY-5811 — [半熟英雄] 出撃失敗: ch12 アルファルファ

- 対象: azumag/docich#5811（read-only 検証。コード修正・commit・push・PR 化なし）
- 判定: `sortie_failed` / confidence `high`
- fingerprint: `4a90b2d0af973f56` / occurrence(incident_id): `cd07d89216881308013c` / key: `4a90b2d0af973f56/19-3/239775`
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1597-chikujou-defer-reason`
- 月: ch12 `19-3`（観測 680 回 / 157.4 秒 / max gap 0.7 秒、first_tick 239775）
- 検証 base: worktree `verify/5811` の現行 main 取込済みコード。`src/docich/hanjuku/version.py` は `hanjuku-chart-v1606-ch4-boss-source-reach`。
- 制約: 本番 decision log はローテーション済みのため参照しない。issue 本文（webfetch 取得）と現行 main のコードだけで検証。shell / python3 / GitHub API / 外部 hash API の実行はこの環境で拒否されたため、ハッシュは式と入力値を以下へ残す。`DOCICH_HANDOFF_PATH` も shell 実行拒否により確認できず、handoff 正本は未読。

## 結論: **(b) 設計どおり**

`order_failed` に至る制御は、現行 main に明示的に残る fail-closed 設計どおりである。出撃一覧で本人・カーソル確認が取れない間は最初は待機（`situation_held`）だが、同じ step で 8 観測続くと `general_list_hold_exhausted` を記録し、誤選択・誤出撃を避けるために出撃指示を取り消して `order_failed` で終える。今回の issue はその bounded cancellation が月内に実際に発火した記録であり、監査が存在しない失敗を作った誤検知ではない。

ただし、なぜ 8 観測の間ずっと strict な本人・カーソル確認が通らなかったか（イベント/描画遷移による正当な不可視か、OCR の過保守な不可読か）は、issue に残る縮約済み decision rows だけでは分からない。したがって「strict 条件を緩めるべきか」「走査済み名簿を選択証拠にもっと使うべきか」という改善余地は残るが、現行仕様の適用外にある実欠陥とは断定しない。

## 1. ハッシュ式と再計算結果

現行 `src/docich/hanjuku/month_audit.py` の sortie 系 builder:

- `_build_sortie_incident` は `record['fingerprint'] = _fingerprint(mem.get('chapter'), event['kind'], [event['kind']])` とする（現行 L1329 付近）。signals には `order_start_without_launch` 等が後で足されるが、fingerprint には使われない。
- `_fingerprint` は `raw = f"{chapter}|{kind}|{'+'.join(signals)}"` を UTF-8 sha256 し先頭 16 hex（現行 L3545-L3547）。
- `_build_sortie_incident` の incident_id は `raw = f"{runtime_id}|{generation}|{kind}|{reason_code}|{step_local}|{general}|{target}|{int(float(now))}"` を UTF-8 sha256 し先頭 20 hex（現行 L1324-L1327）。

issue の JSON から確定する入力:

| 項目 | 現行式の raw 入力 | 期待値 | 再計算 |
|---|---|---|---|
| fingerprint | `12|sortie_failed|sortie_failed` | `4a90b2d0af973f56` | **未計算**。python3/CLI/外部 hash API が拒否された。ただし VERIFY-5444 に同じ raw 入力 `12|sortie_failed|sortie_failed` と同じ期待値 `4a90b2d0af973f56` の記録があり、式と入力の組合せは既存検証と一致する。 |
| occurrence / incident_id | `g1190-ch1v9|1190|sortie_failed|sortie_order_failed|2ec91b77|アルファルファ|ボス|1795837859` | `cd07d89216881308013c` | **未計算**。`created_at` / `closed_at` は `1795837859.7162051`、そのため `int(float(now))` は `1795837859`。 |

検品ゲート側で python3 が使えるなら、上記 raw をそのまま UTF-8 sha256 して前方一致を確認すればよい。

## 2. `order_start` 後に `order_failed` へ至る現行経路

issue の時系列は次のとおり。

1. `order_start` (`I:d8d78d:2ec91b77`, アルファルファ, ファンゴル→ボス, attack) が記録される。
2. 出撃優先 latch が効き、`house_deferred_sortie`（「出撃の開始から発射確認までのため、家来・状態の探索を見送る」）が出る。
3. ファンゴルへ jump / confirm し、将軍一覧へ進む。
4. `general_list_scanned` で一覧走査は完了し、`garrison_seen` / `roster_resident_confirmed` が記録される。
5. その直後から同じ理由 `出撃一覧の本人・カーソルを確認できないため保留` の `situation_held` が連続する。
6. 8 回目で `general_list_hold_exhausted`、同時刻に `order_failed`（「将軍一覧の停滞上限に達したため出撃指示を取り消す」）。

現行コードの対応:

- `GENERAL_LIST_HOLD_LIMIT = 8`（`src/docich/hanjuku/flows/deploy.py` 現行 L995）。
- `_hold_general_list_bounded` は step ごとに保留回数を数え、8 未満なら `_hold_deploy` で待機 (`situation_held`)、8 以上で `general_list_hold_exhausted` を記録し `_finish_order(..., 'failed')` する（L998-L1015）。つまり理由文だけの `保留` は当初は待機であり、即時失敗ではない。上限到達後だけ bounded cancellation として失敗になる。
- boss / hero / source probe 系の選択では、走査済みを含む一覧 `present` が読め、重複がなく、現在見えている名前が `_general_visible` で厳密に読め、全員の固定 max HP が既知であることを要求する（L2538-L2548）。この strict 条件を満たさない場合が今回の `roster_unreadable` 保留である。
- 条件を通れば candidate / fallback hero / cursor 移動へ進むが、今回は将軍選択の A 入力へ進む前に上限へ達した。カード選択画面 (`card_select`) はこの後の段階であり、到達していない（L2673 以降）。

この「確認できなければ選ばない」「固まれば有限回で取消す」動作は、`tests/test_hanjuku_general_list_hold.py` が `#3306` の regression として固定している。誤選択防止を優先する fail-closed 設計であり、現行 main でも同じ経路が残る。

## 3. month audit が high confidence の `sortie_failed` にする根拠

- `SORTIE_TERMINAL` は `order_failed` を `('sortie_failed', 'failed')` に対応づける（`month_audit.py` L1158-L1161）。
- `_sortie_event` は terminal `order_failed` を拾い、今回の step/general/source/target/purpose/reason を event に残す（L1195-L1233）。
- `_build_sortie_incident` の medium downgrade は、`order_failed` かつ reason_code が `sortie_order_unaccepted` で、同月に confirmed launch が既にある場合だけ（L1263-L1278）。今回の理由は「本人・カーソル未確認」で `_sortie_reason_code` の専用条件に合致せず fallback の `sortie_order_failed` になる（L1165-L1192）。さらに confirmed は 0 なので downgrade 条件を両方満たさない。したがって confidence `high` は現行ルールどおり。
- 観測欠損の downgrade 条件にも問題は見えない: 観測 680 回 / 157.4 秒 / max gap 0.7 秒、`started_clean: true`、context_missing false。

## 4. 直前の `boss_sortie_overdue` との関係

直前の `boss_sortie_overdue` は別 step `I:d8d78d:1245f497`（バラクーダ）で、「ボス城への突入後に勝敗の実測が残らず上限超過」により旧 boss 行軍を overdue 化し、step を pending に戻す経路である。

現行 `strategy/orders.py` では、この処理は:

- 旧 sortie を `overdue` にし、古い step の context / launched order を掃除して pending に戻す（L1473-L1561）。
- 将軍の所在が証明できないため `sortie_location_recheck_queued` を出し、その将軍を `general_location_unknown` 扱いにする（L1375-L1402）。

これはバラクーダの所在確認と再出撃計画には影響するが、本件 step `I:d8d78d:2ec91b77` の将軍一覧 strict 判定を直接書き換える経路ではない。実際の issue 時系列でも、バラクーダへの `general_jump_open/failed` はファンゴル城内の一覧走査より前に終わり、その後で `castle_jump_confirm` → `general_list_scanned` → strict hold へ進んでいる。

したがって、boss overdue の残滓は dispatch 前の前景ワークを増やし、開始までの遅延には寄与した可能性がある。しかし、本件の `order_failed` を直接発生させた証拠はない。直接の終端は、ファンゴルの将軍一覧で strict な本人・カーソル確認が 8 観測続けて通らなかったことである。

## 5. #5794 との異同

#5794（ch11 `Mc7fc03`, v1591）は同じ `sortie_failed` 系だが、終端と状況が違う。

- #5794: `situation_held` + `attack_retry_exhausted` / `sortie_retry_exhausted`。既に confirmed 1 / launch 1 / order_start 2 / order_launched 1 があり、戦闘・再試行系列の上限だった。
- 本件 #5811: `order_failed` / `sortie_order_failed`。confirmed 0 / launch 0 / order_start 1 / order_launched 0 で、戦闘や出撃後の結果ではなく、将軍一覧での出撃前選択に到達できず bounded cancellation になった。

したがって #5794 の検証対象とは同じ fingerprint family の「出撃失敗」でも、終端機構は別である。

## 6. #5770/PR #5790 (v1593) と #5801/PR #5806 (v1597) のカバー範囲

### PR #5790 / v1593（#5770）

#5770 の修正は、`card_list_unreadable_depart`（切り札一覧が読めないので計画札なしで出撃続行）と `sortie_kit_empty_cancel`（空携行拒否）が同じ出撃元でループする問題を、出撃元単位の unreadable-card block で断つものである。

本件では `card_list_unreadable_depart` も `sortie_kit_empty_cancel` も出ていない。そもそも将軍選択前に失敗しており、card_select へ到達していない。したがって v1593 の修正は本件をカバーしない。

### PR #5806 / v1597（#5801）

#5806 は `chikujou_deferred_sortie` 後の築城 skipped を `chikujou_skip_unexplained` と誤記しないよう、理由記録を区別する監査/記録修正である。出撃の将軍一覧判定、cursor 判定、hold limit、boss 出撃の選択条件は変更していない。

本件 chart は確かに `hanjuku-chart-v1597-chikujou-defer-reason` で、#5806 マージ後の観測だが、それは「v1597 で直っているはずの出撃欠陥の再発」ではなく、v1597 が対象外の経路であることを意味する。現行 main v1606 にも同じ strict roster hold → bounded cancel 経路が残る。

## 7. 切り札の携行未確認との関係

切り札未確認は原因ではなく結果である。

- 出撃手順は、将軍一覧で本人を選んで A を押した後に `card_select` / `sortie_confirm` へ進む。
- 本件は将軍一覧の strict 確認で 8 回保留し、将軍選択前に `order_failed` になった。
- したがって出撃確認レシートが無く、在庫・携行・計画 cards が `None` / 未確認なのは当然である。非携行とは断定できず、自動 Issue 化しないという issue 本文の型は正しい。
- 逆に、切り札未確認が roster_unreadable を起こした経路もコード上ない。

## 8. 残る未決点

- strict 判定を通らなかった直接の画面上の原因は不明である。`general_list_scanned` 完了と最初の保留が同時刻に並ぶため、少なくとも初回は「走査には十分な読み」でも「選択用の strict な `_general_visible` には不足」だった可能性が高い。ただし issue の縮約 rows には `situation_held` の `observed_metric`（`candidates` / `text_len` / `hold_count` 等）が残らず、実フレームもローテーション済みなので、正当な遮蔽か OCR 過保守かは判定できない。
- 改善候補としては、走査済み名簿と現在カーソルの必要最小限の行だけを選択証拠に使う、strict 判定の失敗理由（どの行・どの条件か）を `observed_metric` へ残す、boss 出撃優先中の overdue location recheck の優先度を整理する、などが考えられる。ただし今回の検証範囲では実装しない。

## 最終整理

- 監査の fingerprint/incident 形式は現行式と整合する。fingerprint は同一 raw の既存検証でも同値が記録されているが、本環境では sha256 の直接再計算は未実施。
- `order_failed` は実際に出撃未発射のまま bounded cancellation になった記録であり、誤検知ではない。
- その cancellation は現行 main に残る fail-closed 設計どおりなので、本検証の結論は **(b) 設計どおり**。
- #5790/v1593 と #5806/v1597 はどちらもこの roster strict-hold 経路をカバーしない。
- boss overdue は遅延要因になり得るが、直接の失敗原因とは証明できない。
- 切り札未確認は出撃前失敗の結果であって原因ではない。

## 検品ゲート確認（Muse、2026-11-28 19:22 JST、現行 main 59bfc0850）

- fingerprint / occurrence を検品ゲートで再計算した。SHA-256("12|sortie_failed|sortie_failed")[:16] = 4a90b2d0af973f56、SHA-256("g1190-ch1v9|1190|sortie_failed|sortie_order_failed|2ec91b77|アルファルファ|ボス|1795837859")[:20] = cd07d89216881308013c。いずれも issue の値と一致する。
- 結論 (b) 設計どおりを維持する。現行 main のコードで次の3点を直接確認した。
  - `GENERAL_LIST_HOLD_LIMIT = 8`。`_hold_general_list_bounded` は step ごとに保留回数を数え、8 未満なら `situation_held` で待機し、8 以上で `general_list_hold_exhausted` を記録して `_finish_order(..., 'failed')`（理由「将軍一覧の停滞上限に達したため出撃指示を取り消す」）で bounded cancellation する。
  - boss / hero / source probe 系の選択では、走査済みを含む一覧 `present` が読め、重複がなく、現在見えている名前が `_general_visible` で厳密に読め、全員の固定 max HP が既知であることを要求する。満たさない場合は `roster_unreadable` の保留になる。
  - 回帰テスト `tests/test_hanjuku_general_list_hold.py` は、7 回の保留の後に上限到達で `general_list_hold_exhausted` が出て失敗になる形を #3306 の regression として固定している。
- 本件の時系列（`general_list_scanned` 完了の直後から同じ理由の `situation_held` が 8 回連続し、8 回目で `general_list_hold_exhausted` と同時に `order_failed`）は、この fail-closed 設計の適用そのものである。strict 判定が 8 観測通らなかった直接の画面上の原因（正当な不可視か OCR の過保守な不可読か）は issue の縮約 rows だけでは分からず、改善余地として残るが、現行仕様の適用外にある実欠陥とは断定しない。修正 PR は割り当てない。
