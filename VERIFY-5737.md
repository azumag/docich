# VERIFY-5737 — [半熟英雄] 出撃失敗: ch1 ヘルメス

- 対象: docich#5737 / fingerprint `4cd60a6645c13f6e` / occurrence(incident_id) `95227733933f7383fa57`
- ラン: `g1373-b4c4126f` / generation 1373 / `hanjuku-chart-v1578-chikujou-defer-bounded` / 月 ch1 2-3
- 証拠: `/home/hatch/work/evidence-5737/hanjuku_decisions.jsonl`（読み取りのみ。`EVIDENCE-5737.md` は untracked pointer で commit 対象外）
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読（AGENTS.md §1）。GitHub 操作はせず、issue 要約・現行コード・decision log を根拠に検証した。

## 結論: **(a) 実欠陥**

主因は、**自城宛て move の到着実測を「出撃不発」へ誤変換する状態遷移の欠陥**である。発射確認の marker survey や roster probe の単純な読み損ねではなく、ヘルメスの位置食い違いでもない。

`A:4ab4f978:M98bbef` はアルマムーンから自城キカンドンへの `purpose=move`。自城宛ての移動は世界マップに部隊▲を描かないため、marker survey が1回で blind 化するのは既知仕様どおりだった。しかし出撃自体は成立し、その後の本人ステータスが行先キカンドンの在城を示した。ところが `_expire_spent_sorties()` は post-departure の `location='castle'` を無条件に「行軍終了＝未発射の証拠」とみなし、目的地の `castle` 名を照合していなかった。そのため、成功した移動を未発射として reopen し、2回目の miss で `sortie_launch_failed` へ到達した。

## decision log の経緯

1. 最初の実行（ガスパチョ）は month 2-2 に始まり、出撃確認まで進んだが、目標選択中に別の戦闘が割り込んだ。
   - `order_start` line 2020、`sortie_confirm` line 2053。
   - `order_launched_unconfirmed` line 2104、「目標決定前に出撃先選択を離れた」と記録。
   - 後続の一覧では在城・不在を確定できず `sortie_verification_held` line 2301。
   - その後、野営/在城の実測により未発射扱いで reopen された line 2526。
2. month 2-3 で同じ step が再開され、計画のガスパチョではなく実在一覧からヘルメスが選ばれた。
   - `garrison_seen` line 2537: アルマムーン `['どうし','ヘルメス','キャンディー']`。
   - `move_general_picked` line 2542: ヘルメスをキカンドンへ。
   - `sortie_confirm` line 2565、`sortie_departure_requested` line 2580、そして **`order_launched` line 2582**。したがって「order_launched 0」は実ログそのものの不存在ではない（後述の集計窓 artifact）。
3. 世界マップ確認では部隊▲なし。
   - `sortie_launch_unverified` line 3248: `markers=[[115,123,121,128]]`, `reads=1`, source アルマムーン, target キカンドン, `roster_pending`。
   - これはコード上も、target が自軍なら `limit=1` で blind 化する経路（`src/docich/hanjuku/flows/map.py` `_probe_launch_markers`）。自城宛てに▲が出ないことはコメント済みの仕様。
4. 到着の実測は失敗判定より先にあった。
   - `post_month_location_confirmed` line 3277: 月次の本人ステータス確認でヘルメスの現在地をキカンドンと確定し、在城リストへ反映。
   - `roster_resident_confirmed` line 3373: キカンドン実一覧 `['ヴィーナス','ヘルメス']`。
   - 既存の `routes.observed_roster()` は `incoming()`（`SORTIE_BUSY_TICKS=400` 未満）か `overdue` の移動だけを到着候補にする。今回は発射 tick 5569 から実一覧 tick 6184 まで 615 tick で、まだ `overdue` 化前だったため、この経路では `sortie_move_arrival_observed` にならなかった。これは現行テスト `test_wrong_castle_old_transfer_or_nontransfer_never_confirms_arrival` に固定された既存の保守的挙動である。
5. 最後に `_expire_spent_sorties` 系の実測終了処理が発火した。
   - line 3373 の `_remember_residents()` により、persistent な本人ステータスも `location='castle'`, `castle='キカンドン'`, `tick=6184` へ更新された。
   - `sortie_record_observed_ended` line 3378。
   - `sortie_launch_failed` line 3377: `general=ヘルメス`, `location='castle'`, `misses=2`, `source=アルマムーン`, `target=キカンドン`, `status_tick=6184`。
   - issue の世界状態でヘルメスがキカンドン在城なのは矛盾ではなく、**移動が実際に成功していたことを示す**。食い違いは、この到着を未発射扱いしてアルマムーンへ帳簿復帰させる側にあった。

## コード根拠

- 出撃 commit: `src/docich/hanjuku/flows/map.py` `_commit_departure()`（L3030付近）。launch probe を作り、自城宛てでも発射確認を追う。
- marker survey: 同ファイル `_probe_launch_markers()`（L3149付近）。`target in _owned(mem)` なら自城宛ては部隊▲を描かないので `limit=1`（L3247-3255）。本件の line 3248 と一致。
- 欠陥箇所: `src/docich/hanjuku/strategy/orders.py` `_expire_spent_sorties()`。従来は `_observed_march_end()` が `location in ('camp','castle')` を返すと、unconfirmed launch を miss として reopen/fail していた（L1795-1902）。`castle` フィールドが **target** かどうかを見ていなかった。
- 修復後: 同関数で `purpose=move`・target が自軍・本人ステータスの `castle` が target と一致する場合は `status='arrived'`・`launch_confirmed=True` とし、`sortie_move_arrival_observed` を記録して未発射巻き戻しをしない。この成功到着では、出撃元へ戻した意味を持つ `sortie_record_observed_ended` は記録しない。
- 補助要因: `src/docich/hanjuku/strategy/routes.py` `observed_roster()` は busy 窓を外れたがまだ `overdue` でない移動を実一覧照合に使わない。本件ではこれで line 3373 の直接一覧証拠を到着確定へ使えなかったが、この挙動は現行テストで意図的に固定されているため、今回の修正範囲には含めなかった。

## 月内 3件の order_start との関係

- `I:494d80:1becbce7` read-only roster probe: ジョンリギ一覧 `['リースリング','クランベリー']` を読み、`roster_probe_complete` / `order_done`（lines 3351-3354）。正常。
- `I:494d80:be0f65f0` attack キカンドン→スペンソニア: source defender 条件で取消（issue 要約どおり）。本件の直接原因ではない。
- `I:494d80:e04e6912` attack クランベリー/ジョンリギ→スペンソニア: 本件とは別 order。ヘルメス移動の失敗化を説明しない。

また incident 集計は `_read_decisions(... now-120, now)` の直近120秒を見る。`A:4ab4f978:M98bbef` の `order_launched` は line 2582 / at 1795761932.0918682、失敗レコードは line 3377 / at 1795762769.0848248 で約837秒差であり、失敗時の直近120秒には入らない。line 2582 に `order_launched` が実在するため、issue の `order_launched 0` は audit の直近窓（および decision count の集計範囲）による表示上の性質であって、出撃 commit が無かった証拠ではない。

## 既存 issue との異同

- #5734（同ラン ch1 1-5, zero_sortie, (b)）: 唯一の order_start が read-only probe で、敵向け attack が成立しなかった月。本件は attack 2件（1件 defender 取消、1件開始）に加え、自城 move が実際に `order_launched` まで到達し、成功到着を失敗化した点で別。
- #5702（ch12, (a), PR #5714）: 発射未確認中に築城などへ逃げる優先度欠陥。現行 v1578 には launch priority の修正済みコメントがある。本件は優先度欠如ではなく、到着ステータスの意味解釈の欠陥なので同じ再発ではない。
- #5623（ch5, (a), PR #5645）: すでに自軍の城への stale attack を閉じる欠陥。本件は stale attack ではなく、最初から自城宛ての正当な `purpose=move` なので別。
- VERIFY-5444 で残った「自城宛て move が blind 期限切れで失敗化する疑い」は、本件では decision log により実際の `order_launched` と target 在城が確認でき、(d) ではなく (a) に確定できた。

## fingerprint / occurrence

- fingerprint の builder は `month_audit.py` `_build_sortie_incident()` → `_fingerprint(mem.chapter, event.kind, [event.kind])`。sortie 系なので signals は1要素のみ。式は `sha256(f"{chapter}|{kind}|{'+'.join(signals)}")[:16]`。本件の理論 raw は **`1|sortie_failed|sortie_failed`**。
- occurrence(incident_id) の builder は同関数内 raw `runtime_id|generation|kind|reason_code|step_local|general|target|int(now)` の sha256[:20]。decision log の failed at は 1795762769.0848248 で、構成値は理論上 **`g1373-b4c4126f|1373|sortie_failed|sortie_launch_not_observed|M98bbef|ヘルメス|キカンドン|1795762769`**。
- 本環境では python/openssl/sha256sum 等の実行と外部 hash API 取得が拒否されたため、独立再計算は未実施。式と入力値は上記のとおり特定でき、issue 値 `4cd60a6645c13f6e` / `95227733933f7383fa57` と照合可能な形で残す。

## 実装修正

- `src/docich/hanjuku/strategy/orders.py`
  - `_expire_spent_sorties()` で、自軍 target への `purpose=move` について、post-departure 本人ステータスが `location='castle'` かつ `castle == target` の場合を成功到着として扱う。
  - `status='arrived'`、`launch_confirmed=True`、launch probe/miss budget を消し、`sortie_move_arrival_observed` を記録。出撃元へ将軍を復帰しない。
- `tests/test_hanjuku_spent_sortie_expiry.py`
  - `test_a_move_status_at_the_owned_target_is_arrival_not_a_failed_launch` を追加。本件と同型（自城キカンドン宛て move・blind launch probe・miss 1・本人ステータスが target 在城）で、`sortie_launch_failed` や出撃元復帰を意味する `sortie_record_observed_ended` へ進まず arrived になることを固定。

## 検証

- 本環境では `python3 -m pytest -q tests/test_hanjuku_spent_sortie_expiry.py` も実行拒否されたため、ローカルテストは未実行。CI 任せ。変更は元コーナー側のみで、`hanjuku-speedrun` の生成物は手編集していない。
- `git diff --check` は clean。`git add` / `git commit` は実行拒否されたため、この worktree では修正と本ファイルは uncommitted のまま。push・PR・GitHub 操作はしていない。
- 作業中バナー/音声は本番アクセスを伴うため未実施。
