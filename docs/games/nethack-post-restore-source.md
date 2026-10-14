# NetHack post-restore source: PR1の証跡契約

Refs #859 / #490。PR1は復帰後の観測証跡を保存する。自動改善の実行や有効化は行わない。

2026-10-12 に公開化後の main（退役の非同期化・復帰前の終端記録 #1969 系）へ再構築した。元PR #903 との差分は「現mainへの適合」節を参照。

## 今回の範囲

`src/docich/nethack_source.py` にI/Oを持たない証跡検証・正規化関数を追加し、既存のCoordinator・コーナー・RunStoreへ接続する。

- `new_session_context` / `validate_session_context`: corner/session/start requestのUUID、scheduled/rotation/manual/unknownを固定。許可は常に `authorized_mode=off`。
- `verify_restoration`: 当該requestの成功receipt、復帰元runtime（switchは `result.source_runtime`、stopは `result.stopped_runtime`）、復帰先runtime、canonical revisionとcleanup（`retiring == []`）を照合する。readyやゲーム名だけでは成功にしない。
- `build_post_restore_source`: run/session、終端xlogの時刻・所有関係、起動由来、終了理由を照合し、上限付きのsourceを作る。save継続、manual、operator stop、不明終端は自動改善対象にしない。
- `validate_restoration_summary`: 保存用の固定フィールドだけを受理し、生のargv/例外/秘密情報を混入させない。

新規runの `birth_not_before_epoch` は、NetHack起動要求の直前に保存する。終了時はxlogのbyte baselineより後にある対象playerの終端行が一件だけで、`starttime <= endtime <= finish time` とbirth下限を満たす場合だけ `identity_verified=true` にする。NetHack 3.6.7の[`topten.c`](https://sources.debian.org/src/nethack/3.6.7-1/src/topten.c/)はxlog `starttime` にキャラクター誕生時刻 (`ubirthday`) を書くため、Coordinatorがprocess開始を記録した後になる場合がある。既存saveの採用、既存runtimeの採用、複数行、時刻欠落・不整合は未検証のままにする。実VMのxlog writer/pathが正しいことは別途 #753 で確認する。

Coordinatorの成功switchは、mainの既存どおり退役させる旧runtimeを `result.source_runtime` として新activeと同じatomic commitで公開する。成功stopは、停止確認した旧runtimeを `result.stopped_runtime` として追加記録する（additive。stopの `generation` の意味は変えない）。cleanupはcommit後に非同期で進むため、receiptではcleanup完了を主張せず、読取時のcanonicalが `retiring == []` かつ `candidate`/`previous` が空であることだけを完了証拠にする。失敗・rollback・旧形式のreceiptには成功証跡を補完しない。

コーナーは開始前に `scheduled` / `rotation` / `manual` の由来とrequest IDを固定し、rotation由来は既存の予約台帳と照合する。台帳が読めない、または一致しないときは `unknown` にする。開始したsessionには由来・run ID・canonical runtimeを記録する。終端/save記録はmainどおり先に行い（終端は復帰前に `record_confirmed_terminal` で確定し得る）、復帰が `completed` になった後、当該receiptとcanonical状態を照合した結果を `attach_post_restore_source` で同じrun/sessionへ追記する。この追記はrun status・終端履歴・current pointerを変えず、最初の封印だけを残す（再実行はno-op）。照合できなければ固定理由を `post_restore_source_error` に残す。履歴や証跡の失敗は復帰済みゲームを巻き戻さない。

`_spawn_improve_once()` は従来のno-opのまま。queue/worker/LLM/canary/promotion、本番Action・save、配信・共通音声・通知への操作はない。新しいtimerやworkflow実行権限も追加しない。

## ゲーム切替と将来の隔離評価

このPRは、旧ゲーム切替時の停止規約を変更しない。切替元の試合と保存境界を完了した後、旧ゲームの改善ジョブと子プロセスを停止し、描画・ゲーム実行・操作AIを終了してから次のゲームへ進む。配信・音声・通知などの共通基盤は維持する。sourceの記録はCoordinatorが旧runtimeの停止とcleanupを確認した後だけ行う。

`eligibility=terminal_scheduled` は観測分類であり、改善実行の許可ではない。現在の契約は `authorized_mode=off` / `slot_release_verified=false` のまま、`_spawn_improve_once()` もno-opである。ライブ改善を旧ゲームの停止後も走らせる例外はこのPRでは導入しない。

将来、隔離評価を例外として認める場合も、旧ゲームの停止を省略したり、その実runtime・save・xlog・dump・canonical stateを共有したりしてはならない。評価は別の明示opt-in契約と専用領域を持ち、ネットワーク遮断・有限の資源上限・終了後cleanupを検証する別変更としてレビューする。隔離状態と所有境界をsupervisorが確認できない場合は、旧ゲーム改善ジョブと同じく停止対象としてfail-closedにする。本PRから将来の隔離評価を起動・継続する経路はない。

## 復帰元の同一性は推測しない

旧成功stop receiptは `result.stopped_runtime` を持たない。その旧receiptは `restore_source_unverified` として拒否する。switchは `source_runtime` が無い/一致しなければ拒否する。新しい証跡も、当該run/sessionのruntimeと一致しなければ拒否する。

事前にNetHack世代10を見てから要求をqueueへ入れても、その要求が実行される前に世代が変わり得る。復帰先の世代が大きい、直前にNetHackだった、同じゲーム名へ戻った、という情報だけで補完してはならない。

source tupleはCoordinatorが実際に停止対象を所有・確認する境界から発行する。モデル出力やcorner側の自己申告からこのフィールドを作らない。Coordinatorの正本を別ファイルで再実装しない。復帰後のread時点でcanonical状態がすでに次の切替へ進んでいれば、今回の観測は証明不能としてsourceを保存しない。

## 現mainへの適合（2026-10-12 再構築）

- 元PRの `from_runtime` / `source_cleanup_completed` は使わない。switchはmain既存の `source_runtime`、stopは追加の `stopped_runtime` で結び、cleanupは読取時の `retiring == []` で判定する（commit時点でcleanup完了は成立しないため）。
- 元PRがstop結果の `generation` を新世代へ変えていた変更は採らない。stopは「receipt世代 > 復帰元世代」かつ「結果世代 == 復帰元世代」で照合する。
- 元PRの `record_finished(restoration=...)` 同時書込みは、main の「終端を復帰前に確定」設計と両立しないため、復帰後の別書込み `attach_post_restore_source` に置き換えた。復帰前に run/session/runtime を `load_run` で固定し、current pointer が先に外れていても追記できる。
- 終端の `identity_verified` は、`record_finished` の終端経路（最後の行を履歴に採用しつつ、新規行が1件のときだけ検証対象）と `record_confirmed_terminal`（完全な1行のみ）の両方で付ける。
- manual起動（`run_reserved_manual`）は `origin="manual"` を渡す。

## データと権限

関数の入力は、呼出元が保護された運用記録から取得したデータである必要がある。この検証は暗号認証ではない。`source_id` は正規化内容のSHA-256であり、署名・本人確認・実行許可ではない。将来のconsumerは信頼された保存先と所有関係も検証する。

`eligibility=terminal_scheduled` でも `authorized_mode=off`、`slot_release_verified=false` のまま。起動を許可しない。slot解放・現在のadmission・費用・lease・policyは後続の独立検証事項である。

`origin` はtrustedなscheduler/manual入口から指定する契約。引数を `scheduled` にしただけで権限が生じるものではない。rolling rotationは既存scheduler ledgerへ照合してから接続する。

## PR1の残作業

- [x] Coordinatorの成功receiptへ実停止対象を保存し、失敗/rollbackを成功扱いしない。
- [x] scheduled/rotation/manualのcontextを開始前に保存し、rotation予約がない場合はunknownにする。
- [x] RunStoreの既存current runとsessionへsourceを同時保存し、save継続は改善対象外にする。
- [x] queued/recovery/rollbackと次runの競合を含む結合回帰を追加する。遷移中の`previous`を復帰先として維持し、rollback失敗後のrecover・queued switch retryを通したうえで、復帰直後にcanonicalが次runへ進んだ場合は前runのsourceを保存しない。
- [x] 実Coordinator経路でコーナー終了・復帰・RunStoreへのsource保存を検証し、後続sessionの履歴保存失敗でも復帰成功を取り消さず、先行sessionのsourceを誤帰属しないことを確認する。
- [x] RunStoreでbyte baseline後の一意なplayer行と時刻下限を照合し、証拠が揃うrunだけ `identity_verified=true` にする。
- [ ] 実VMでxlogのcanonical path・writerとterminal行の発生を確認する（#753）。証拠がない間はterminal sourceを有効にしない。
- [ ] 旧ゲーム改善停止規約と将来の隔離評価の例外をレビューする。このコミットでは例外を有効化しない。

## 検証

`PYTHONPATH=src python3 -m pytest -q tests/test_nethack_source.py tests/test_nethack_run.py tests/test_nethack_corner.py tests/test_round_boundary.py tests/test_coordinator.py tests/test_retro_corner.py tests/test_corner_rotation.py`: 606 passed / 109 subtests（2026-10-12 再構築時）。

純粋なfixtureで、manual/save/operator/不明終端、欠落receipt、別世代、pending/rollback、cleanup不足、UUID・型・過大入力、深いコピーを確認する。成功fixtureの `from_runtime` は将来契約を表す合成証跡であり、実環境で取得できた証拠ではない。

`tests/test_coordinator.py::TestNethackPostRestoreIntegration` はfake adapterを用いた実Coordinator・コーナー・RunStoreの結合テストであり、復帰後のsave継続source保存、履歴保存失敗時の復帰維持、queued startが遷移中のprevious runtimeを保持すること、rollback失敗後のrecoverとqueue再試行、次runが先にcanonicalを進めた場合のsource fail-closedを検証する。実NetHack/VMでの受入ではない。

フルrepository CI、独立レビュー、実機検証の結果はPR本文で別に記録する。単体テストの成功を配備・本番有効化・攻略改善の証明にしない。
