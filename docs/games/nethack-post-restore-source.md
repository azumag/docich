# NetHack post-restore source: PR1の証跡契約

Refs #859 / #490。PR1は復帰後の観測証跡を保存する。自動改善の実行や有効化は行わない。

## 今回の範囲

`src/docich/nethack_source.py` にI/Oを持たない証跡検証・正規化関数を追加し、既存のCoordinator・コーナー・RunStoreへ接続する。

- `new_session_context` / `validate_session_context`: corner/session/start requestのUUID、scheduled/rotation/manual/unknownを固定。許可は常に `authorized_mode=off`。
- `verify_restoration`: 当該requestの成功receipt、復帰元runtime、復帰先runtime、canonical revisionとcleanupを照合する。readyやゲーム名だけでは成功にしない。
- `build_post_restore_source`: run/session、終端xlogの時刻・所有関係、起動由来、終了理由を照合し、上限付きのsourceを作る。save継続、manual、operator stop、不明終端は自動改善対象にしない。
- `validate_restoration_summary`: 保存用の固定フィールドだけを受理し、生のargv/例外/秘密情報を混入させない。

新規runの `birth_not_before_epoch` は、NetHack起動要求の直前に保存する。終了時はxlogのbyte baselineより後にある対象playerの終端行が一件だけで、`starttime <= endtime <= finish time` とbirth下限を満たす場合だけ `identity_verified=true` にする。NetHack 3.6.7の[`topten.c`](https://sources.debian.org/src/nethack/3.6.7-1/src/topten.c/)はxlog `starttime` にキャラクター誕生時刻 (`ubirthday`) を書くため、Coordinatorがprocess開始を記録した後になる場合がある。既存saveの採用、既存runtimeの採用、複数行、時刻欠落・不整合は未検証のままにする。実VMのxlog writer/pathが正しいことは別途 #753 で確認する。

Coordinatorは成功したswitch/stopで、実際に停止を確認した旧runtimeの `game/generation/runtime_id` と `source_cleanup_completed=true` を同じrequest receiptに記録する。失敗・rollback・旧形式のreceiptには成功証跡を補完しない。

コーナーは開始前に `scheduled` / `rotation` / `manual` の由来とrequest IDを固定し、rotation由来は既存の予約台帳と照合する。台帳が読めない、または一致しないときは `unknown` にする。開始したsessionには由来・run ID・canonical runtimeを記録する。復帰後に当該receiptとcanonical状態を照合できた場合だけ、RunStoreの終端/save記録と同じ書込みで `post_restore_source` を保存する。履歴や証跡の失敗は復帰済みゲームを巻き戻さない。

`_spawn_improve_once()` は従来のno-opのまま。queue/worker/LLM/canary/promotion、本番Action・save、配信・共通音声・通知への操作はない。新しいtimerやworkflow実行権限も追加しない。

## 復帰元の同一性は推測しない

旧成功receiptは `from_game` だけで `result.from_runtime` を持たない。その旧receiptは `restore_source_unverified` として拒否する。新しい証跡も、当該run/sessionのruntimeと一致しなければ拒否する。

事前にNetHack世代10を見てから要求をqueueへ入れても、その要求が実行される前に世代が変わり得る。復帰先の世代が大きい、直前にNetHackだった、同じゲーム名へ戻った、という情報だけで補完してはならない。

source tupleはCoordinatorが実際に停止対象を所有・確認する境界から発行する。モデル出力やcorner側の自己申告からこのフィールドを作らない。Coordinatorの正本を別ファイルで再実装しない。復帰後のread時点でcanonical状態がすでに次の切替へ進んでいれば、今回の観測は証明不能としてsourceを保存しない。

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

`PYTHONPATH=src python3 -m pytest -q tests/test_coordinator.py tests/test_nethack_corner.py tests/test_nethack_run.py tests/test_nethack_source.py`: 220 passed / 27 subtests。

純粋なfixtureで、manual/save/operator/不明終端、欠落receipt、別世代、pending/rollback、cleanup不足、UUID・型・過大入力、深いコピーを確認する。成功fixtureの `from_runtime` は将来契約を表す合成証跡であり、実環境で取得できた証拠ではない。

`tests/test_coordinator.py::TestNethackPostRestoreIntegration` はfake adapterを用いた実Coordinator・コーナー・RunStoreの結合テストであり、復帰後のsave継続source保存、履歴保存失敗時の復帰維持、queued startが遷移中のprevious runtimeを保持すること、rollback失敗後のrecoverとqueue再試行、次runが先にcanonicalを進めた場合のsource fail-closedを検証する。実NetHack/VMでの受入ではない。

フルrepository CI、独立レビュー、実機検証の結果はPR本文で別に記録する。単体テストの成功を配備・本番有効化・攻略改善の証明にしない。
