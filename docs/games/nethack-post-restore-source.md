# NetHack post-restore source: PR1の証跡契約

Refs #859 / #490。これはPR1の最初の実装単位であり、PR1全体の完了や自動改善の稼働を意味しない。

## 今回の範囲

`src/docich/nethack_source.py` にI/Oを持たない証跡検証・正規化関数を追加する。

- `new_session_context` / `validate_session_context`: corner/session/start requestのUUID、scheduled/rotation/manual/unknownを固定。許可は常に `authorized_mode=off`。
- `verify_restoration`: 当該requestの成功receipt、復帰元runtime、復帰先runtime、canonical revisionとcleanupを照合する。readyやゲーム名だけでは成功にしない。
- `build_post_restore_source`: run/session、終端xlogの時刻・所有関係、起動由来、終了理由を照合し、上限付きのsourceを作る。save継続、manual、operator stop、不明終端は自動改善対象にしない。
- `validate_restoration_summary`: 保存用の固定フィールドだけを受理し、生のargv/例外/秘密情報を混入させない。

このコミットはコーナーやRunStoreへの呼出しをまだ追加しない。`_spawn_improve_once()` は従来のno-opのまま。queue/worker/LLM/canary/promotion、本番Action・save、配信・共通音声・通知への操作はない。新しいtimerやworkflow実行権限も追加しない。既存CIのNetHackテスト列へ単体テストを登録するだけ。

## 復帰元の同一性は推測しない

調査基準 `3b7f3f91b20c839e77dceac9770605413d524cf5` の既存成功receiptは `from_game` を持つが、この検証器が要求する `result.from_runtime` の正確なsource tupleを持たない。そのため、現行receiptをそのまま入力しても `restore_source_unverified` として拒否する。これは意図した未接続状態である。

事前にNetHack世代10を見てから要求をqueueへ入れても、その要求が実行される前に世代が変わり得る。復帰先の世代が大きい、直前にNetHackだった、同じゲーム名へ戻った、という情報だけで補完してはならない。

PR1の残作業で、Coordinatorが実際に停止対象を所有・確認する境界からsource tupleを既存receiptへ保存し、queued/recovery/rollback/cleanupまでテストする。モデル出力やcorner側の自己申告からこのフィールドを作らない。Coordinatorの正本を別ファイルで再実装しない。

## データと権限

関数の入力は、呼出元が保護された運用記録から取得したデータである必要がある。この検証は暗号認証ではない。`source_id` は正規化内容のSHA-256であり、署名・本人確認・実行許可ではない。将来のconsumerは信頼された保存先と所有関係も検証する。

`eligibility=terminal_scheduled` でも `authorized_mode=off`、`slot_release_verified=false` のまま。起動を許可しない。slot解放・現在のadmission・費用・lease・policyは後続の独立検証事項である。

`origin` はtrustedなscheduler/manual入口から指定する契約。引数を `scheduled` にしただけで権限が生じるものではない。rolling rotationは既存scheduler ledgerへ照合してから接続する。

## PR1の残作業

- [ ] Coordinatorの実停止対象を復帰receiptへ結び付け、複数世代・queued/recovery競合を検証する。
- [ ] scheduled/rolling rotation/manualの起動時にcontextを保存し、retryで起動由来を昇格させない。
- [ ] RunStoreのexpected run/session照合、終端記録との同時保存、重複終了・次runとの競合を実装する。
- [ ] コーナー終了・復帰をブロックせず、履歴保存失敗でも復帰成功を取り消さないことをlifecycleで検証する。
- [ ] 旧ゲーム改善停止規約と将来の隔離評価の例外をレビューする。このコミットでは例外を有効化しない。

## 検証

`PYTHONPATH=src python -m pytest -q tests/test_nethack_source.py`

純粋なfixtureで、manual/save/operator/不明終端、欠落receipt、別世代、pending/rollback、cleanup不足、UUID・型・過大入力、深いコピーを確認する。成功fixtureの `from_runtime` は将来契約を表す合成証跡であり、実環境で取得できた証拠ではない。

フルrepository CI、独立レビュー、実機検証の結果はPR本文で別に記録する。単体テストの成功を配備・本番有効化・攻略改善の証明にしない。
