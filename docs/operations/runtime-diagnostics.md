# Runtime diagnostics（read-only, owner-only）

production VM の runtime 状態を、Desktop Commander や対話 SSH に依存せず、
ChatGPT / GitHub Actions から安全に直接診断するための正規経路。

```
ChatGPT → GitHub Actions → owner-only VM gateway → sanitized read-only diagnostics → production VM
```

正本コード:

- `ops/vm_actions/gateway.py` — `diagnostics` operation（allowlist・検証・出力sanitize）
- `ops/vm_actions/authorize.py` — owner-only 認可
- `ops/vm_actions/collect_diagnostics.py` — VM 上で動く固定 collector（read-only）
- `ops/vm_actions/runtime_registry.py` — 診断カバレッジの正本 registry
- `.github/workflows/vm-operations.yml` — `diagnostics` 手動実行

## Operation 契約

- `diagnostics docich production <sha>` のみ許可。preview は拒否（preview に live runtime は存在しない）。
- SSH forced command は 4 要素固定。arbitrary shell command を渡す経路は存在しない。
  diagnostics は stdin を読まない。
- owner-only 認可は維持する（actor / triggering_actor / ref / workflow_ref の検証）。
  read-only のため production confirm は不要。
- gateway は collector が production git HEAD と一致する tracked-clean であることを
  毎回検証する。不一致なら fail-closed（`VM operation rejected`）。
- collector は固定 argv（soren root のみ）・scrubbed env・timeout 60s で実行される。
- 出力は gateway が型・サイズ・secret-like key を再検証してから返す。
  stdout 上限 64 KiB、最終 JSON 上限 49 KiB、文字列 500 字、list 100 件、深さ 8。
- production exec の stdout 秘匿境界は不変。diagnostics 以外の経路で
  production の任意コマンド結果を Actions へ公開してはならない。
- `stream_title_sync` は Soren の owner-only journal 1ファイルだけを読む。最大32KiB、最新行だけを許可schemaで検証し、現在の Soren gitlink SHA と一致し15分以内の記録に限って platform enum / skip reason を表示する。

## 汎用 PID helper の診断専用契約（#2469 follow-up）

`ops/vm_actions/safe_pid_cleanup.py` は、既存 API 名
`safe_unlink_pid_file()` / `safe_cleanup_stale_pid_files()` を維持するが、
PID ファイルを unlink しない。戻り値の success は常に `False`。
dead/zombie PID と観測しても `cleanup_disabled:no_writer_lock` を返す。
live PID は `pid_alive`、その他の guard 不成立は従来の理由コードを返す。
`dry_run=True` も同じ診断であり、`deleted` / `would_delete` は返さない。

writer と共有する排他契約がないため、存在・stat・inode の再確認だけでは
最終検査から unlink までの置換競合を防げない。診断結果は観測時点の情報であり、
後から同じパスを削除してよい根拠にしない。実際の cleanup は、writer との協調を
含む別途レビュー済みの所有 lifecycle に属する。この helper は gateway の
`diagnostics` collector に組み込まれておらず、soviet_now の専用 cleanup helper とも別実装。

ローカル fixture での CLI 例:

```sh
python3 ops/vm_actions/safe_pid_cleanup.py /path/to/test-root
python3 ops/vm_actions/safe_pid_cleanup.py --dry-run /path/to/test-root
```

root 省略時は `/home/ubuntu/soren`。`--dry-run` は互換引数で、指定の有無・順序によらず
診断のみ。走査対象は `tmp/state/*.pid` と `tmp/.soren_loop.lock/pid`。
結果は `SKIP: <path> (<reason>)` と表示する。終了コード 0 は走査の完了を示し、
cleanup の成功・対象の不存在・すべての読み取りの成功を保証しない。

## Output 契約

```json
{
  "status": "ok",
  "meta": {"generated_at": 0, "window_sec": 900, "docich_head": null, "soviet_head": null, ...},
  "stream_title_sync": {"record_status": "fresh", "occurred_at": "2026-10-02T12:00:00Z",
                        "age_sec": 30, "event": "result",
                        "run_soren_sha": "<40-hex>", "same_soren_sha": true,
                        "skip_reason": "none", "youtube": "updated", "kick": "not_live"},
  "tracked_drift": {"parent_tracked_dirty": 0, "owned_submodule_head_mismatch": 0,
                    "owned_submodule_tracked_dirty": 0,
                    "owned_submodule_missing_or_invalid": 0,
                    "scan_complete": 1, "unknown": 0, "drift_detected": 0},
  "workers": {"expected": 19, "running": 6, "stopped": [], "paused": [],
              "duplicates": [], "zombies": [], "stale_pid_files": [],
              "unregistered": [], "required_down": [], "required_stale": [], "details": {},
              "chat_worker": {"pid": 1234, "alive": true, "stale_pid_file": false}},
  "semantic_decision": {"present": true, "readable": true,
                        "comment_classifier_backend": "jev", "backend": "jev",
                        "route": "direct", "requested_model": "jev-1.13.0",
                        "credential": "present", "fallback_route": "vercel",
                        "fallback_credential": "present"},
  "queues": {"lanes": {"radio": {"locked": true, "owner_alive": true, "age_sec": 43}},
             "stale_locks": 0, "queue_giveups_15m": 0},
  "ai": {"attempts_15m": 0, "failures_15m": 0, "rate_limits_15m": 0,
         "fallbacks_15m": 0, "all_failed_15m": 0, "recent_events": [],
         "anomalous_components": {}},
  "improvement": {"running": false, "stale": false, ...},
  "corners": {"state_dir_found": true,
              "game_switch": {"present": true, "phase": "ready", "active_game": "sorengame",
                              "last_status": "succeeded", "last_error_code": null, ...},
              "weather_corner": {"present": true, "status": "completed",
                                 "selection_kind": "automatic",
                                 "rotation_request_matches_last_result": true,
                                 "started_at": 1790942400, "completed_at": 1790942580,
                                 "start_receipt": {"status": "succeeded",
                                                   "result_matches_owner": true,
                                                   "runtime_matches_owner": true,
                                                   "generation_matches_owner": true},
                                 "restore_receipt": {"status": "succeeded",
                                                     "result_matches_owner": true,
                                                     "runtime_matches_owner": true,
                                                     "generation_matches_owner": true}, ...},
              "soren_game": {"present": true, "readable": true, "state": "MOVE",
                             "age_sec": 2, "founding_seen": false,
                             "founding_marker_current": false,
                             "founding_marker_stale": false,
                             "make_soren_count": 0, "game_count": 12,
                             "score": 117, "pieces_count": 16,
                             "runner_pid": 1234, "runner_alive": true},
              "game_switch_fifo": {"present": true, "readable": true,
                                    "queued_count": 0, "head": null, ...},
              "retro_corner": {"present": true, "status": "active", "game": "gnurobots", ...},
              "paper_corner": {"present": false, "readable": false},
              "presentation": {"present": true, "mode": "detailed", ...},
              "boundary": {"improvement": {"present": true, "completed_at": 0, "age_sec": 0},
                           "prediction": {"present": false, "completed_at": null, "age_sec": -1}},
              "ab": {"state_present": true, "pattern": "ABBA", "games_lines": 12,
                     "games_tainted": 0, "games_age_sec": 42, "candidate_pending": true, ...}},
  "runtime_presentation": {"present": true, "readable": true, "reason": "ok",
                           "active_runtime": true, "status": "ready",
                           "display": ":1", "window": "2097160",
                           "width": 299, "height": 224, "age_sec": 12},
  "encoder": {"schema_version": 1, "present": true, "readable": true,
              "source": "twica_common", "state": "running", "ready": true,
              "frames_sent": 123456, "fps": 29.97, "progress_age_sec": 2,
              "out_time_sec": 98765, "reason": "ok"},
  "webui": {"unit_file": true, "unit_active": true, "unit_enabled": true,
            "main_pid": 1234, "n_restarts": 0, "served_port": 8787,
            "served_reachable": true, "served_matches_deployed": true,
            "listener_is_unit": true}
}
```

- `status` は `ok` / `warn` / `critical` に正規化する。
- critical: required worker の停止（pause 中を除く）/ required の stale PID。
- warn: paused worker、未登録 PID、重複、zombie、stale lock、直近 all_failed /
  queue give-up、改善ループの stale・retry 滞留、
  および `corners.corner_rotation.status == "recovery_required"`
  （共有面は健全でも全自動cornerが止まる latch。#986）。
- 単発の rate-limit だけで critical にしない。rate-limit は件数のみ報告する。
- queue waiter 数は lock 形式から観測できないため報告しない（不明は不明と扱う）。
- 診断は stale lock を削除しない。観測のみ。
- `hanjuku_tactical.last_card_assessment` はv132以降の最後の札判断を、一件の固定形式で示す。
  `prediction_only=true`、判断からの `age_ticks`、固定32札の名前、対象種別、敵HP・双方の兵数、
  総威力下限・敵兵士HP上限・将軍へ届く下限・残HP上限、`lethal` / `egg_drop_fit` / `allowed`
  と固定の判断理由のみを返す。`lethal=false` は「下限で撃破を保証できない」であり、
  実際の使用・卵落下・撃破の結果ではない。未知の値はnull、自由文・将軍名・入力列は出さない。
  他の戦術情報と同じactive identity、30秒以内のbot記録、読取り前後の世代一致を確認する。
  第2〜12話でもこの札判断は返せるが、城一覧は第1話のみのため `status=unsupported_chapter`
  のままであり、後半の所有城を第1話の名前から推測しない。
- `corners` はコーナー/番組のライフサイクル観測。`retro_corner` が
  `status=failed` かつ `recovery_required=true` の場合だけ severity を `warn` にし、
  owner-only の固定 `recover-failed` 操作を許可する。`draining` / `recovery_required`
  のcanonical phaseは自動でリセットしない。
- `runtime_presentation` は **canonical が active とする runtime** の投影状態
  （`<state_dir>/runtimes/<runtime_id>/presentation.json`）だけを bounded に投影する。
  `reason` は `present=false` / `readable=false` の理由を固定enumで示す
  （`ok` / `status_unknown` / `no_active_runtime` / `active_unreadable` /
  `runtime_id_invalid` / `record_absent` / `record_unreadable`）。
  `status` は `starting` / `ready` / `presentation_failed` / `cleanup_failed` / `stopped` の
  固定enumに限定し、`display` / `window` / `width` / `height` は形式検証済みの値、
  `age_sec` は記録ファイルのmtimeからの経過秒。`groups`（pid）・argv・path・自由文は出さない。
  共有display再起動後に投影が復帰したことは、`status` が `presentation_failed` から
  `ready` へ戻り `age_sec` が更新されることで確認する（#2097）。
- `corners.<id>.end_reason` は固定enumのみを出す。`interrupted` で
  `switch-terminal-before-corner-active` の場合、その枠は**ゲーム切替が起動前に
  terminal へ到達して中断した**ことを意味し（#1044）、他の中断と区別して読む。
  通常の完了や停止で起きた中断ではないため、以降の `recover-failed` と
  `corner-rotation recover` の2操作でのみ確定する。severity は変えない。
- `corners.soren91_corner` は meriken（soren91）枠のライフサイクルと、その枠を
  終わらせた切替の結果を出す（#1221）。`status` / `game` / `previous_game` /
  `completed_at` / `end_reason` は他コーナーと同じ固定射影で、加えて
  `switch_status`（`queued` / `succeeded` / `failed` / `rolled_back` の固定enum、
  未知は `null`）と `switch_error_code`（枠が記録した切替エラーコード。
  `readiness_timeout` 等。64文字上限・redact済み）を返す。`last_error` は
  200文字上限・redact済みの詳細で、`readiness_timeout` の場合は
  **原因**（例 `status=viewer_timeout, viewer_wait_sec=240`）を含む。
  presenter の pane は tmux window ごと消えるため、この記録が失敗理由を
  後から確認できる唯一の正規経路になる。request ID・runtime identity・
  自由文の本文は出さない。
- `pulse_sink_inputs` は PulseAudio の playback stream 状態を read-only で出す
  （#968）。各要素は `index` / `sink` / `role` / `mute` / `corked` /
  `volume_percent` / `player` の固定キーのみで、`player` は
  `bridge-ffplay` / `retroarch` / `browser` / `speech-worker` /
  `monitor-capture` / `stream-capture` / `other` の固定カテゴリに落とす。
  `application.name` の生値・PID・module/client id・stream プロパティの平文は
  **出さない**。`corked` は daemon が報告しない環境では `null`（= 未報告）で
  「corked していない」とは言わない。`muted > 0` は BGM/SE が無音になり得る
  状態だが、意図的な mute と区別できないため severity は変えない。
  `readable=false` のときは「無音なし」ではなく **観測できなかった** として読む
  （`reason` は `pactl_unavailable` / `pactl_failed` / `unbounded_output` /
  `unparsable`）。gateway は `XDG_RUNTIME_DIR` を渡さないため、collector は
  `/run/user/<uid>/pulse/native` が socket のときだけそこを明示する。
  固定(owner-only)な mute 解除 operation は本 projection に含めない。
  `attribution` は同じ stream 集合の固定軸集計で、`by_player`（`bridge-ffplay` /
  `retroarch` / `browser` / `speech-worker` / `monitor-capture` /
  `stream-capture` / `other` / `unknown`）と `by_role`（`media.role` の13語彙のうち
  実在するもの + `unattributed`）を返す。`media.role` は通常設定されないため
  `by_role` は補助で、所有主体の切り分けは `by_player` で行う。
  `streams` が出力予算で省略されても `attribution` は残す（`output_omitted=true`）。
  詳細リストは `PULSE_SINK_INPUTS_MAX`（8件）で打ち切り、`truncated` で示す。
- `external_video` は receiver の状態と `frame_fresh` に加え、最終フレームの
  mtime からの経過秒を `frame_age_sec` として返す（0〜604800秒にクランプ、
  フレームが無い/未来時刻は `null`）。`frame_fresh=false` は「受信していない」を
  意味し、`frame_age_sec=null` は「フレームが存在しない」を意味する。
- `encoder` はエンコーダの送信ハートビートを固定射影する。供給元は TwiCa 共通
  アダプタの `run/twica-common/pipeline.json` と Soren `direct_stream` の
  `tmp/state/direct_stream/status.json` で、fresh な方を優先する（TwiCa 5秒、
  direct_stream 15秒）。`state` / `ready` / `frames_sent` / `fps` /
  `progress_age_sec` / `out_time_sec` のみを返し、`source` は
  `twica_common` / `direct_stream` / `none`。`reason` は `ok` / `absent` /
  `stale` / `unreadable` / `invalid_schema`。供給元の記録には配信設定と
  再接続先チャンネルが含まれるため、**allowlistの数値・enumキーだけを写し、
  元のdictは保持しない**。URL・キー・チャンネル名・PIDは出さない。
- `game_switch_fifo` は `game-switch/requests` のreceiptを固定上限で読み、queued件数と
  FIFO先頭の operation/target/age だけを出す。request ID、payload、生成本文、秘密情報は
  出さない。malformed receiptやscan未完了は復旧せず、監視側で要対応として扱う。
- `corners.soren_game` は試合状態の固定enum（`MOVE`/`DROP`/`WAITING`/`STOP`/
  `GAMEOVER`）、state更新からの秒数、建国markerの有無と帰属、試合数、score、駒数、
  試合runnerのPID/生存だけを出す。盤面内容、ログ、入力、生成文は出さない。
  markerの帰属は `founding_marker_current` / `founding_marker_stale` で判定する。
  `founding_seen` は実在のみを表し、前の試合のmarkerでも `true` になる
  （#2345 では前の試合のmarkerを実在だけで建国中と誤判定し、復旧が約15時間
  遅れた）。`STOP` が古くても `founding_marker_current=true`（現試合の建国）の
  場合は通常の試合終了と判定しない。`founding_marker_stale=true` なら通常の
  試合終了として扱ってよい。`founding_marker_current` / `founding_marker_stale`
  が `null`（ruleを取得できず帰属が不明）のときは、`founding_seen=true` を
  建国中の疑いとして従来どおり通常の試合終了と判定しない。
- `tracked_drift` は deploy を拒否させる `git_clean(root)==false` の内訳を、
  **固定カテゴリとcounterだけ**で帰属する（#412）。`parent_tracked_dirty` /
  `owned_submodule_head_mismatch` / `owned_submodule_tracked_dirty` /
  `owned_submodule_missing_or_invalid` は 0|1。`scan_complete=0` のときは
  `unknown=1`・`drift_detected=1` とし、失敗したscanを drift なしの0件扱いにしない。
  path・filename・diff・file bytes・raw exception は出さない。severity は変えない。

- gatewayの `ops_brief_projection.status` は親コミットの公開用生成物とVMの
  実バイト/modeを比較した `matched` / `drift` / `unmanaged` / `unknown`。
  `drift` / `unknown` は少なくともwarnとする。mapping欠落時はcollectorを動かさず
  `collection.status=unavailable` / `reason=projection_missing` を返し、worker等の
  未観測項目を健全と推測しない。`unmanaged` は親生成物への移行前で同期成功を
  意味しない。ソース本文・生成本文・SHAは返さない。詳細は [ops-brief.md](ops-brief.md)。

## 収集元（すべて read-only）

- common rotation: `corner_rotation.json` の固定projectionを
  `corners.corner_rotation`へ出す。status、slot、next_due_at、last_seen_at、
  eligible_count、pending有無、`queued_manual`（ledgerまたは固定inboxに手動予約があるboolean）、および設定由来の `schedule_mode` / `cooldown_seconds`
  のみ。seed・request payload・自由文は出さない。
  `excluded` は直近のtickが `CornerRotationManager._eligible()` で自動抽選から外した
  corner idを固定理由（`disabled-or-paused` / `manual-only` / `adapter-unavailable`、
  想定外は`unknown`）へ対応づけたboundedなmap（最大32件、id順。無ければnull）。
  **有効化はここで読める**: 手動予約を受け付けるcornerは`manual-only`、無効・休止中は
  `disabled-or-paused`になるため、予約を実行しなくてもcatalogの有効化を実測できる。
  validなcatalog id以外は出さず、理由の自由文は出さない。
  さらに、`error_kind`（`docich.corner_rotation.ERROR_KINDS` と同一の固定enum。
  例外本文はstateにもdiagnosticsにも書かない。欠落はnull、不正値は`unknown`。
  直近のlatch分類として次にlatchし直すまで残る）と、予約（`pending`）が在る限り
  （latch中かどうかを問わず）次を足す:
  `pending_corner`、`pending_phase`（`selected`/`dispatched`/`unknown`）、
  `pending_age_sec`（-1は不明）、`pending_owner`（固定9種のcorner stateのうち
  同じrequestを記録したstate名。該当なし`none`、読取不可`unknown`、予約なし`absent`）、
  `pending_owner_status`。request UUIDは固定stateとの照合にだけ使い出力には含めない。
  これで「まだ起動していない」「既に終了している」「実行中・corner側の復旧が要る」を
  証跡から区別できる（#986）。
  失敗したcornerの記録として `failed_corners`（直近8件、`corner` / `at` /
  `resolution`（`released-unverified`＝記録して他cornerのローテーションを継続、
  `recovery-required`＝共有slotの安全が証明できずlatch維持、不正値は`unknown`）/
  `reason_code`（`failed-corner-slot-released` / `execution-or-state-unverified` /
  `base-game-unrestored`（終了済みcornerの枠を取り戻して継続したがbase gameが未復帰）/
  `restore-recovery-exhausted` の固定enum。欠落はnull、不正値は`unknown`）/
  `error_kind`（固定enum））を出す（#2187）。request identity・例外本文・自由文は出さない。
  end失敗の自動restore再実行の予算を `auto_restore_corner` / `auto_restore_attempts` /
  `auto_restore_attempt_limit` / `auto_restore_next_at` / `auto_restore_status`
  （固定enum）で出す（#2187）。request identityは出さない。
  自動予約とは別に `manual_pending`（bool）、`manual_pending_corner`（固定enum）、
  `manual_pending_state_file`（固定8種のstate名、拡張子なし）、
  `manual_pending_age_sec`（未観測/未来時刻は-1）、`manual_pending_owner`、
  `manual_pending_owner_status` を出す。手動予約が指定した固定allowlist内のstateで
  requestが一致した場合だけownerを報告する。別state内の一致を代用しない。
  ownerは予約なし`absent`、指定state不在/別requestなら`none`、
  不正予約・allowlist外・読取不可は`unknown`。request UUID・任意pathは公開しない。
  `pending=false/pending_owner=absent`だけでは手動予約の不在を意味しない。
  `manual-execution-pending`は手動executorのqueued/waiting/already-running返却、
  `manual-request-needs-resume-or-recovery`はtimerによる未完了手動予約の保持を表す。
  両者の変化や保持された`error_kind`だけで新しい実行失敗と断定しない。
  queueモードの過去`next_due_at`やcanonical `ready`も枠の解放を証明しない。
  `corners.retro_corner.status`と`corners.retro_corner.game_audio.status`は別物で、
  `applied`は後者の音量適用結果。これらの診断値は復旧/再開の許可ではない。
  `recovery_required`は次cornerを停止する実行契約であり、診断自体は復旧操作をしない。
  `nethack_rotation_evidence` は、このautomatic NetHack pendingのownerに結び付く
  元restore receipt、元runtimeのrequest付き終了境界、rollback復元世代の唯一のruntime、
  現在canonicalの復帰receipt・終了境界・lease/result一致を固定booleanで投影する。
  ownerに`previous_game`がある場合、欠落を入口条件とするlegacy契約を適用可能とは表示しない。
  保存済みrestore recovery/cleanup記録は有無だけを出す。UUID、runtime/lease/player、
  path、prompt、argv、任意JSON本文・例外本文は出さない。

  `contract_conditions` は同じ読取済みレコードから契約入力の内訳だけを追加する。
  `previous_game` は `absent/null/sorengame/other/invalid`、`finish_reason` は
  `absent/null/terminal/other/invalid`。キー不在と明示的nullは区別する。
  `pending_source_absent`、元receiptの`original_source_absent` /
  `original_restored_identity_absent`、復帰receiptの`return_source_absent`、
  `restore_request_distinct`（元restore requestとrotation予約の相違）はboolean。
  `original_cleanup_pending` は `absent/null/pending/clear`。
  従来の`cleanup_clear=false`だけからcleanupがpendingだと推定しない。
  legacy producerの履歴条件は `automatic_dispatch_history=matched/missing/invalid/scan_limit` と
  `manual_reservation_since_selection=absent/present/invalid/scan_limit` に分ける。
  ledger内のhistory最大512件、selected/started/last_seenと全履歴の時刻を確認し、
  選択以降・開始以前のautomatic reservationと、選択以降のNetHack manual-reservationを
  観測する。欠損・不正時刻・未来履歴・上限超過では部分的な肯定値を保持しない。
  `legacy_contract_applicable`は入口条件の集約であり、全guardの成功を意味しない。
  この内訳は現在の入力の観測であって、過去のexit 70の実際の拒否理由や
  lock下での復旧可否を返すものではない。新しい読取対象・process走査は追加しない。

  読取は固定state rootからdirfd/no-followで行い、regular JSON 64KiB以下・duplicate key拒否、
  runtime探索4096 entries以下に制限する。resource probeの後に、読んだ全レコードと
  runtime listingを再確認する。変更があれば肯定的投影を破棄し`snapshot_stable=false`。
  欠落、不正schema/identity、曖昧なruntime、scan上限、読取拒否は固定理由とunknownで返す。
  状態・lockを作らず、recover/coordinator/teardownを呼ばず、signalやtmux入力を送らない。
  playerの比較元は固定のレビュー済み`config/games/nethack.toml`であり、証拠側のpathを使わない。

  各runtimeの`presentation_stopped`、identity一致したtile manifestの`tiles_cleanup_complete`、
  exact generationの既存tmux target不在を別々に出す。いずれか否定なら
  `all_resources_released=false`。全て肯定でも、これらの記録からdetached/unregisteredな
  game childや改善workerの不在を証明できないため`all_resources_released=null`を維持する。
  各resourceの`unknown_reasons`は固定列挙値のみ。release記録の欠落は
  `presentation_record_missing` / `tiles_record_missing`、identity一致したtile記録に
  booleanのcleanup値がない場合は`tiles_cleanup_unproven`、tmux読取失敗・非boolean値は
  `tmux_probe_unavailable`。既存観測の範囲外である`detached_children_unobserved` /
  `unregistered_workers_unobserved`は常に残す。別資源に否定証拠がある場合も、
  未観測範囲の理由を消さない。全レコードの再確認が失敗すれば内訳も破棄する。
  `terminal_chain_matches`は終了証拠の一致のみであり、全資源解放や運用解除の許可ではない。
  `recovery_authority`は常にfalse。安定した前後読取も原子的snapshotや将来のfenceではなく、
  正規operatorは別承認後にwriter lock下で適用契約を再照合する必要がある。

  `corners.corner_rotation_timer` は支配的なtimer unit名（移行後は
  `docich-corner-rotation.timer`）、active/enabled、旧名が正しいaliasかを示す
  bounded boolean `legacy_alias` のみを出す。unit path・alias target・state pathは出さない。
  alias pair投影も支配timer選択と同じ`PROD_ROOT.parent`のuser unit rootを使う。
  gatewayのscrubbed `HOME=/tmp` をunit欠損の判定元にしない。
  時刻指定manual予約がある場合は、UUIDや任意payloadを出さずに
  `scheduled_manual`、固定corner名、`scheduled_manual_trigger_at`、`scheduled_manual_due`、
  7日で上限化したoverdue秒、固定 `scheduled_manual_blocked_reason`（`not_due` /
  `waiting_slot`）だけを追加投影する。独立inboxに残る時刻前予約も同じ投影へ含める。

- `corners.weather_corner` は固定 `weather_corner.json` からstatus、request/start/restore/completion時刻、
  固定enumの終了理由、前のgame名、rotation予約/完了記録とのrequest一致booleanに加え、
  音声deliveryの固定statusと `next_index`（0〜13）だけを出す。音声本文・request payload・item keyは出さない。
  開始・復元GameSwitch receiptはweather ownerが保持するcanonical UUIDと一致するときだけ、
  その固定UUIDのreceiptファイルを一件ずつbounded/no-followで読む。出力するのはstatus・operation・
  時刻・ownerとのresult/runtime/generation一致booleanのみ。request UUID、runtime ID、lease ID、
  forecast、音声queueや本文、payload、自由文エラー、パスは出さない。`restored_runtime_matches_current`
  は保存された復元identityと現在のGameSwitch canonical identityの一致を示す。後続の切替後にfalseでも、
  保存されたrestore receiptが成功している事実は変わらない。読み取りは順次行うため単一snapshotではない。
  projectionは観測のみで、corner予約・GameSwitch・runtimeを操作しない。

- `corners.retro_program_queues` は既存Soren rootの固定2 queueを、種類`scheduled`/`manual`と
  `present`・`readable`・`status`だけへ投影する。`present=false`は読取時の欠落、`null`は
  symlink/アクセス不成立など存在を確認できない場合。存在する不正JSON・非object・非regular・
  上限超過は`present=true/readable=false`。読取成立でもstatus欠落・不正型・未知値は`unknown`。
  既知statusは`waiting/waiting_boundary/waiting_turn/running/done/expired/cancelled/error`のみ。
  既存の全経路no-follow・regular file・256 KiB上限readerを使い、queueの一覧走査やlock取得・
  state作成・cleanupはしない。本文・パス・識別子・時刻・自由文例外は出さない。
  2ファイルは独立snapshotであり、対象要求との一致や取消/管理解除の適格性を証明しない。
- rotation 待機の補助証跡: `corners.rotation_evidence` に固定8種の
  corner state（retro/PAPER/Soren91/NetHackの通常・manual）と固定10種の
  改善結果（9ゲーム＋PAPER）を出す。状態enum、ゲームenum、完了時刻、
  improve起動boolean、明示的なrecovery_requiredを観測する。改善結果は
  status、started_at、completed_at、失敗理由の固定enum `reason_code` / `phase`
  （欠落・未知は `unknown`）と既存lockの `held/free/absent/unknown` のみ。
  Pac-Manは候補生成後の次回改善ジョブでゲーム本体をheadless起動し、現行/候補をABBA順に
  各設定試合数ずつ評価する（配信中の実試合ではない）。全試合でスコアが取れたときだけ
  平均を比較し、高い方を選ぶ。同点または不成立なら現行を維持する。`ab-pending` /
  `ab-incomplete` はA/Bの保留/不成立、
  `ab-adopted` / `ab-rejected` はABBA評価後の採用/見送り、`ab-stale` は基準戦略変更による
  無効化、`ab-invalid` / `ab-eval` は状態/評価の失敗を示す。
  `spawned=true` と正常なrequired workerだけでは、改善の終了証跡を確認できない。
  改善status欠落・failed・running・corner完了より古いstarted_at・保持中lockを
  区別し、`other-corner-needs-finish-or-recovery` の調査に用いる。
  status/lock単独から子プロセス終了・復旧可否を断定しない。
  存在しないlockは作成せず、既存lockを非待機でprobeして即解放する。
  state由来パス、catalog由来パス、request ID、PID、ログ、prompt、save本文は出さない。
  ファイルは64KiB上限、リンク・非regular fileは拒否、不正な値はunknown/null。
  読み取りを順に行う観測なので、一つの原子的な状態スナップショットではない。
  この追加は待機条件・FIFO・scheduler・復旧操作を変更しない。

- `corners.rotation_evidence.moon_buggy_ab` は Moon Buggy のA/B状態について、
  `staged` / `running` / `completed` / `promoted` / `kept`、完了済み試合数、
  目標4試合を返す。完了後は勝者と両腕の平均スコアも出す。候補重みやスコアログ本文は出さない。

- NInvaders改善は通常の2数値重み比較ではなく、生成方策を静的ゲートとworkerで
  検証し、実ゲームを使う6試合ずつの incumbent/candidate 評価後にだけ昇格する。
  `policy-promoted` / `policy-incomplete` / `policy-faults` / `policy-below-margin` /
  `policy-not-significant` / `policy-identical` / `policy-invalid` / `policy-eval` は
  固定理由コードで、生成コード・プロンプト・例外本文は診断へ出さない。昇格ポインタは
  `<state_dir>/resolver/ninvaders/current.json`、ライブrunnerは次試合の開始時に読む。
  workerは別プロセス、空の環境、math importだけ、CPU/file-descriptor/address-space上限を
  使うが、これはOSレベルの隔離ではない。診断だけで実際の候補実行やキー入力を証明しない。

- worker: `tmp/state/*.pid`（+ `tmp/.soren_loop.lock/pid`、
  `tmp/state/.soviet_watchdog.lock/owner`）、`*.paused` マーカー、
  `worker_duplicates.json`（supervisor 報告、10 分以内のみ採用）。
  生死判定は `src/docich/runtime_backend.py` の helper を再利用する。
- queue: `tmp/state/.ai_generation_locks/<lane>/owner`
 （token は出さず PID 生死・age のみ）。stale 閾値は 900s。
  `*.owner_guard.lock` / `*.owner_guard.d` は mutex guard のため lane 扱いしない。
- semantic_decision（#882）: 登録済み `chat_worker` が生きている場合のみ、その
  `/proc/<pid>/environ` から固定4キー名（`COMMENT_CLASSIFIER_BACKEND` /
  `DOCICH_JEV_ROUTE` / `TYPESAFE_API_KEY` / `DOCICH_JEV_VERCEL_API_KEY`）だけを読む。
  いずれも docich のコメント分類器（`docich.comment_classifier`）が実際に読む
  キーで、レビュー済みの `docich.semantic_decision.diagnostics.describe()` へ
  そのまま通す。出力は `backend`（`jev` = `COMMENT_CLASSIFIER_BACKEND=jev` で
  Jev を呼ぶ / `heuristic` = heuristic のみ / `unknown`）/ `route` /
  `requested_model` / `credential`（`present`/`absent`/`not_applicable`/`unknown`
  のみ、値は不可）/ `fallback_route` / `fallback_credential` の固定6項目。
  `route` は優先経路。`DOCICH_JEV_ROUTE=direct,vercel` のように予備経路が
  設定されているときは、`fallback_route` と、その経路自身の鍵の有無を
  `fallback_credential` に出す（予備経路が無いときは `null` / `not_applicable`）。`COMMENT_CLASSIFIER_BACKEND` は非秘密の enum
  なので `comment_classifier_backend` として値そのまま（64字上限）も出す。
  旧 soviet_now adapter だけが読んでいた `DOCICH_SEMANTIC_BACKEND` は廃止済みで、
  読まない。
  生 environ・他の環境変数名・credential の値は一切出さない。
  worker不在／死亡時は `present:false`、environ読み取り失敗時は
  `present:true, readable:false`（未確認を"heuristic"と誤認しない）。
  `DIAGNOSTICS_FILES`（gateway.py）にこの projection とそのroute解決先
  （`src/docich/semantic_decision/{diagnostics,routes}.py`）も追加し、
  drift検証の対象に含めている。
- AI: `tmp/state/ai_stats/YYYYMMDD.jsonl`（当日＋前日按分、直近 900s）。
  `attempt` / `ok` は件数のみ。`recent_events` は fail/winner/all_failed/
  queue_giveup/giveup 系のみ直近 20 件。error は 200 字に丸め・redact。
  fallback 成功は「同一 label に fail と別 agent の winner」の heuristic。
- improve: `improve_state.json`、`tmp/improve.lock`、
  `improve_monitor_status.json`、`rate_limit_backoff` と retry batch の存在のみ
  （内容は読まない）。running かつ更新停滞／PID 死亡なら stale。
- corner: 本番 `state_dir` 配下の `game_switch.json` / `game-switch/requests/*.json` /
  `retro_corner.json` /
  `paper_corner.json` / `trading/presentation.json`。status・時刻・announce 件数のみで、
  announce/台本本文は読まない・出さない。FIFOはqueued件数・先頭の固定項目だけを出す。
  `paper_corner.json` の `degraded` は
  固定boolean `corner_paper_degraded` としてのみ要約する。state_dir は固定 config
  (`config/docich.soren-live.toml`) の `paths.state_dir` から解決し、
  production checkout 内に制約する。同 `state_dir` 配下の `scores/<game>.jsonl` は
  固定6ゲームの末尾 64 KiB だけを読み、件数・連続重複長・直近5件に要約する
  （`corners.retro_scores`。後述「レトロ枠のスコアログ投影」）。
- Hanjukuの `retro_corner.bot_version` は固定許可値だけを表示する。従来の v1/v2 に加え、
  `hanjuku-chart-v129-battle-card-progress` / `hanjuku-chart-v130-summer-cursor-evidence` /
  `hanjuku-chart-v131-defense-month-economy` を許可する。
  未知の値は `null` のままとし、prefix一致による任意文字列の公開はしない。この値はbot状態の
  自己申告であり、表示だけで入力適用や勝率改善を実測したことにはならない。
- Hanjuku実況再生: retro_corner が status=active / game=hanjuku-hero の場合だけ、
  Soren の tmp/.say_queue/debug.log 末尾を最大128KiB・2048行で読み、半熟英雄キューに
  限った queue_started / queue_completed / queue_failed / queue_unmatched_starts と、
  明示的な external_kill_markers / truncated_playback_suspected /
  partial_audio_retry_suppressed の件数を retro_corner.narration_playback に出す。
  `queue_failed_fence_rejection` は、同じキュー項目の汎用失敗より前に
  `say_enqueue` の固定「runtime fence失効」マーカーを観測した失敗件数。
  重複マーカーは1件に数え、完了した項目や別項目のマーカーは流用しない。
  残りは `queue_failed_unclassified` とし、2値の合計は `queue_failed` と一致する。
  期限切れ・世代変更・終了・読取失敗の内訳はこのマーカーだけでは判定できない。
  音声再生後の期限確認でも失敗になり得るため、失敗件数を無音件数とみなさない。
  実行中ログはメモリ内だけで解析し、本文・行・ファイル名・パス・tokenは返さない。
  固定ディレクトリをsymlinkなしで開き、通常ファイル以外や読取失敗は
  status=unavailable と各値nullにする。tail_truncated=true はcollectorによる末尾制限を
  示す。Sorenも上流でdebug.logを500行超から末尾200行へ切り詰めるため、falseでも
  ラン全履歴とは限らず、開始数と完了・失敗数の合計が一致しないことがある。
  開始前・claim直後の一部fence拒否には原因ログがなく、collectorだけでは分類できない。
  queue_unmatched_starts は観測範囲で終端記録が見つからない数で、
  再生中またはログ切替でも起きるため、キャンセル確定数ではない。
  queue_completed も音声全体が聞こえた証明ではなく、リスナー側の実聴確認を代替しない。
- Hanjuku場面実況: `retro_corner.scene_narration` は現在のcanonical runtimeを直接読み、
  schema=1の `hanjuku_scene.json`（producer）、`hanjuku_scene_worker.json`（worker）、
  `hanjuku_scene_commentary.jsonl`（event log）を別々に投影する。ゲーム・runtime・世代・leaseを
  照合し、playing中で終端候補がないrunだけを対象とする。読み取り前後でcanonicalを再確認し、
  切替があれば `status=identity_changed` と全component nullへ戻す。
  JSONは各256KiB、logは末尾128KiB・2048行まで。全path要素でsymlinkを拒否し、通常ファイル
  以外は読まない。facts・本文・event key・scene hash・lease・runtime名・パスは返さない。
  producerはenabled・観測age・最新request seq/age・scope（scene/history）・期限内か・現在のsceneと一致するかだけ、
  workerは固定status/reason/role・age・処理seq・固定counterだけを返す。roleは
  BATCH_COMMENTARY_AGENTS/RADIO_AGENTS/AI_COMMON_AGENTSのいずれかで、モデル一覧は公開しない。
  `request_matches_scene=false` は観測値であり、sourceの取得失敗を意味しない。
  history scopeの完成事実はfieldへ移っても有効な場合があるため、scope・期限・worker結果と併せて読む。
  epoch hashやsource_sceneの内容は返さない。
  event enumはrequested/skipped/generate_started/generate_succeeded/generate_failed/
  deliver_enqueued/deliver_failed。既知reasonごとの件数も別に集計し、自由形式reasonは返さない。
  logはmatched/rejected件数、範囲制限、最新event/reason/age/seq・latency_ms・char_countを出す。
  request確定前のskipはseqがnullのまま件数へ含める。workerの通常reason=noneは独立counterを
  持たないためcounter列から除き、log側のreason別集計では記録されたnoneも数える。
  logの件数は観測末尾の窓であり、workerの保存counterと同じ累計ではない。
  source欠損・不正・identity不一致ではcomponentのread_status/statusをunavailableにし、
  counterはnullに保つ。有効なworkerの空counterと読める空logだけを0件とする。
  混在logでは確認できる行だけを数えてstatus=partialとし、全行不正なら件数をnullにする。
  各ageはsource読取後の小数時刻で計算した保存記録の鮮度であり、古いworker値を現在進行中の証拠として扱わない。
  前段の診断開始時刻は使わず、同じ秒の正常な更新を未来扱いしない。実際の未来時刻は不正として扱う。
  deliver_enqueuedは既存音声キュー関数の正常終了で、実キュー作成・再生完了の受領記録ではない。
  出力予算を超える場合は場面実況詳細をstatus=output_omittedへ置き換え、ゲーム戦況を優先する。
- Hanjuku操作の繰り返し: `retro_corner.decision_plans` は同じgate（status=active /
  game=hanjuku-hero かつ game_switch.active_game=hanjuku-hero）で、現在のcanonical runtimeの
  `hanjuku_decisions.jsonl` を末尾128KiB・1024行で読み、identity（game/runtime/世代/lease）と
  schema=1・event・atを照合した `action_plan` 行だけを集計する。chartのcounterは購入・戦闘・月だけで
  進むため、同じ入力を返し続けていても動かず、カーソルが点滅する限りstall判定も発火しない。
  このprojectionは繰り返している操作そのものを返す:
  固定screen kind語彙（`hanjuku_screen.classify_text` と `screen.kind` のリテラル上書きが所有。
  テストがソースから再導出するので、追加されたkindは`other`に閉じる）、許可リストのpadボタン名
  （a/b/x/y/l/r/up/down/left/right/start/select、未知は`other`）、件数、age、
  そして末尾で繰り返している単位 `trailing_repeat`（`period`=1で単一操作の連打、2〜4で
  交互/周期的なフォールバック、`null`は末尾で繰り返していない）。
  `top_signature` は窓内で最多の署名、`screen_kind_counts`/`button_counts`/`distinct_signatures`/
  `distinct_screen_kinds` は窓（最新128 plan）の内訳、`window_plans` は集計した窓、`plan_records` は
  末尾範囲内で照合したplan行数、`last_plan_age_sec` は最新planの保存 age。
  decision理由・chart step・将軍/城名・frame/decision hash・runtime名・hold_ms・log本文は読まない。
  読み取り前後でcanonicalを再確認し、切替があれば `status=identity_changed` と全値nullへ戻す。
  窓は観測末尾でありコーナー全体の履歴ではない。`tail_truncated` はcollectorの末尾制限を示す。
  外部lease・不正行は件数だけ数えて `status=partial`、全行不正なら集計をnullのまま `unavailable` にする。
  有効なplan行が無い場合は集計をnullに保つ（0件の正常を意味しない）。
  本projectionは固定の出力予算（36KiB）を超えても保持する。1KiB未満で固定・
  自動利用無く、「進んでいるcornerか同じ操作のループか」だけを区別できる唯一の
  証拠のため。gatewayのhard上限（stdout 64KiB / sanitize後64KiB）に近づく場合
  のみstatus=output_omittedへ置き換える（`HARD_JSON_BYTES` = 56KiB）。
- 出力予算とcpu_profile: 固定envelope（`runtime_registry.MAX_JSON_BYTES` = 36KiB）は
  gatewayの64KiB sanitize後capより小さい値で、24KiBのSoren91 evidence chunkが
  base64/JSON展開後も収まる量を確保する。本番の通常payloadはこの上限を超えるため、
  予算超過時は `ai.recent_events`・`workers.details`・`ai.anomalous_components`・
  `cpu_profile`の行・Soren91のgroup・`scene_narration`・`hanjuku_tactical`・
  `pulse_sink_inputs`（`streams`のみ。`attribution` は残す）の順に削る。
  `encoder` はこの予算では削らない。`decision_plans` はこの soft 予算では削らず、
  gatewayのhard上限時のみ削る。残る最大要素が `cpu_profile` なので、
  ゲーム証拠より**前**に `components`/`spawns` を先頭10行へ縮める
  （producerはbusiest順に並べるので先頭が重い行）。さらに5行へ縮める
  floor（`CPU_PROFILE_FLOOR_COMPONENTS`/`CPU_PROFILE_FLOOR_SPAWNS` = 5）を
  適用する。NetHackの古い履歴を落としても収まらない場合は、
  **latest NetHack結果より先に** `components`/`spawns` を全削除する
  （`_cpu_profile_detail_drop`。latest結果は optional な CPU/audio 詳細より
  後に落とす優先順位）。omitted行数は `components_omitted`/`spawns_omitted`
  として累積し、既存 `components_truncated` は書き換えない。
  一方で1行要約はこの予算の全段階で削除対象外です: `cpu_top`（上位3
  components＋others集計）と `spawn_top`（上位3 spawn経路）。合成fixtureでの
  値部分は `cpu_top` が通常58B・長ラベル条件217B、`spawn_top` が通常75B・
  長ラベル条件375B（JSONキー等の増分は別）。これらはpayload全体の本番上限
  保証ではないため、lists が全削除されても上位消費と spawn 経路は読める
  （azumag/docich#970）。
  縮めても収まらない場合、`HARD_JSON_BYTES`（56KiB）を超えたまま返すと gateway が
  診断操作ごと closed で拒否するため、最後に `corners` を先頭とする
  `_HARD_DROP_ORDER` の順にセクション単位で `status=output_omitted` へ落とす
  （#2533）。それでも収まらない場合は残りの top-level セクションを大きい順に
  落とす（`meta` は残す）。
  profileは既に上限（各30行）付きであり、この縮小は出力圧時のみ効く。
- boundary: Soren `tmp/state/corner_boundary_improvement.json` /
  `corner_boundary_prediction.json` の `completed_at` と age のみ。コーナーの
  境界待ちの可否を判定できる。
- boundary.audio: 共有オーディオキュー（`COMMENT_QUEUE_DIR`、既定
  `<soren>/tmp/.comment_queue`）の `comment_*.txt` / `comment_*.playing` の
  件数、最古itemのage、固定enum `reason`（`quiet` / `queued` / `playing` /
  `queued+playing` / `unreadable`）、`present` / `readable`。item名・本文・
  queue内のログは読まない・出さない。Soren `.env` は読まない（secretを含むため。
  本番は既定値）。キューが無い・読めない場合は `quiet=true`（#2236の境界は
  塞がない）。**どのコーナーが何で待っているか**は、この `reason` と
  `corner_boundary_prediction.json` の age、worker健全性の組み合わせで読む。
- A/B: Soren `tmp/state/ab_state.json`（pattern / started_at / 件数 / mtime）、
  `ab_games.jsonl`（行数・tainted 数・最終 arm）、`ab_candidate/`（有無）。
  hash・env 文字列・戦略本文は読まない・出さない。A/B 中は improvement
  boundary が保留されるため、コーナー遅延の直接原因になる。
- meta: デプロイ済み docich HEAD と soviet_now gitlink（検証用。secret ではない）。
- `supervisor_identity`: 固定system unit `soren-runtime.service` のMainPIDと
  `tmp/state/start_all.pid` が一致し、同じuserの生存中Bashである場合だけ、
  `/proc` の開始ticks＋boot時刻から `process_started_at` を観測する。
  配備済み `start_all.sh`、gitlinkの期待blob、Bashの固定script FD 255の
  SHA-256を別々に返す。FDは同じ固定script path（削除済みinodeも含む）だけを
  許可し、512KiB上限・regular file・所有user・変更検査を行う。
  root/tmp/state、procのPID/fdディレクトリはno-followで開き、proc handleを保持して
  相対readを行う。PID再利用時に別processのFDを読むことはなく、再照合不一致は不明になる。
  PID、start ticks、proc comm、FDのpath/inode、argv、環境変数、本文は出力しない。
  取得前後にunit/PID/start ticks/pidfileを再照合し、変化・終了ならprocess証跡を
  捨てて `identity_changed` とする。欠測・unsafe・FDなしは不明として返す。
  `deployed_script_mtime/ctime` は配備fileの時刻であり、deploy完了時刻や起動時hashではない。
  **hash一致・開始時刻だけでは既に定義済みのBash関数を証明しない**。in-place更新や
  同じPIDのexecがあり得るため、`loaded_functions_status` は常に `unverified`。
  `status=observed` もsource証跡の取得だけを意味する。既存shellの起動時attestationは
  存在せず、この診断は追加書込・signal・reload・休止変更を行わない。
  FDが期待hashと異なる場合は `reason=open_script_differs_expected` として旧source保持等を
  積極的に示すが、未知のsourceを特定の旧commitやloaded関数の版と断定しない。
  停止なしの確実なreload経路がない間は、効果測定を自然の次回supervisor起動まで待つ。
  この診断を理由にhotpatch・kill・service restartで適用を強制しない。
- workers: 登録workerの `expected`/`running`/`stopped`/`paused`/`duplicates`/`zombies`/
  `stale_pid_files`/`required_down`/`required_stale` と、名前ごとの `details`。
  サイズ予算は `details` を丸ごと落とすため、**登録chat workerの同一性だけは
  `details` の外側**（`workers.chat_worker` の `pid`/`alive`/`stale_pid_file`）にも出す。
  分類器のproviderを切り替えるowner（#2301）が置換前後を比較するために必要で、
  PID・生存・pid fileのstaleはsecretではなく、`webui.main_pid` と同じ扱い。
- webui: `docich-webui.service` の固定 projection と「配信 UI がデプロイ済み UI と一致するか」の観測。
  `unit_file`（unit ファイル有無）、`unit_active` / `unit_enabled`（`systemctl --user is-active /
  is-enabled`）、`main_pid` / `n_restarts`（`systemctl --user show`、再起動ループの検出用）、
  `served_port`（`config/docich.toml` の `[webui].port`、読めなければ既定 8787）、
  `served_reachable`（`http://127.0.0.1:<port>/` をプロキシ不使用・4秒・1MiB 上限で読み取れるか）、
  `served_matches_deployed`（配信 HTML とデプロイ済み `src/docich/webui.py` の `INDEX_HTML` の sha256
  一致。到達不能・ソース読込不能は `null`）、`listener_is_unit`（unit の MainPID がそのポートの LISTEN
  所有者か。`/proc/net/tcp[6]` と `/proc/<pid>/fd` の読み取りのみ。判定不能は `null`）。
  path・cmdline・HTML bytes は出さず、値は bool / int / null だけ。
  **severity には加算しない**（webui は任意コンポーネント）。deploy 済みなのに
  別プロセスがポートを掴んで旧 UI を出し続ける case を `served_matches_deployed=false` +
  `listener_is_unit=false` で検出する（`restart_webui` operation の終了コード14と同じ状態）。

## レトロ枠のスコアログ投影（#2392）

`diagnostics.corners.retro_scores` は、配信のスコア統計パネルの元になる
`<state_dir>/scores/<game>.jsonl`（1試合終了につき1レコード）の**末尾だけ**を
固定ゲーム集合（`gnurobots` / `ninvaders` / `nsnake` / `bastet` / `moon-buggy` /
`pacman4console`）について投影する。試合終了画面が残ったまま wrapper が
在プレイと誤認し続けると、同じ確定スコアが poll ごとに追記される。この回帰を
正規の read-only 経路で照合できるようにするのが目的で、public リポジトリでは
`exec` が使えないため、この投影が唯一の観測点になる。

- ゲーム名は固定タプルからのみ導出し、state・リクエスト由来のパスや basename を使わない。
- regular file のみ（symlink は `present=false`）。読むのは末尾 64 KiB（行数上限 2000 は
  極端に短い行への安全弁。実レコードは約 70〜90 バイトのため通常は全行走査）。
  窓を外れた場合は `truncated=true` とし、欠落を不在として扱わない。
- 出すのは `present` / `readable` / `records`（窓内の有効レコード数）/
  `invalid`（解釈できない行数）/ `truncated` / `sources`（`agent` / `live` /
  `wrapper` と、それ以外をまとめた `other` の件数のみ）/
  `max_same_score_run`（同一スコアが連続した最長長。重複記録の署名）/
  `last`（直近5件の `ts` / `score` / `source`）/ `age_sec`。
- `score` は 0〜10^9 の整数のみ有効。0点の試合も1レコードとして数える。
  ts 以外の自由文・clip id・hash・path・例外本文・追加フィールドは出さない。
- `records` と `max_same_score_run` は**走査窓の中の集計**であり、
  「重複した試合数」そのものではない。
  `truncated=false`（ログ全体を走査）なら、コーナー実行前後の
  **`records` の差分**が追記レコード数そのものになる。受入はこの差分で読み、
  `max_same_score_run` は poll ごとの重複追記の署名として読む。
  `truncated=true` のとき `records` は下限値にしかならないため差分の基準にせず、
  `last` と `max_same_score_run` を主に見る。
- severity には加算しない。HARD_JSON_BYTES を超える場合だけ
  `{"status": "output_omitted"}` に縮退する（通常は数 KiB 未満）。
  縮退の判定は直前の `decision_plans` 縮退を再計測した後に行う
  （同縮退だけで hard ceiling 以下に戻る場合は `retro_scores` を保持する）。

## Soren91 投下間の read-only 集計

`diagnostics.soren91_drop_profile` は固定ファイル
`<soren_root>/soren91/tmp/state/soren91_loop_metrics.json` の v1
`dropProfile.records` を集計する。戦略・設定・入力・撮影は変更せず、
新しいスクリーンショットや計測プロセスも起動しない。

- 最大2MiB、regular fileのみ、Soren root以下のsymlink拒否、読取失敗は固定status。
- 公開するのは固定数値・UUID session・分類のみ。生records・自由文は出さない。
- `all.phasesSeconds` は投下間総時間と10フェーズの mean / median（nearest rank） /
  p95 / max / total（秒）、合計時間構成比、合計call数。空集団はnull。
- 同一区間の内訳合計と総時間を検証。不整合はinvalidまたはexcludedとし、
  `missingSamples`（ringから追い出された数）と区別する。
- HOLD有無、fromTurn<10/≥10、認識保留有無、直近最大4試合、最遅1区間の
  固定数値集計を含む。保留分類はstable/stable-slow-advance以外のreasonが
  一つ以上ある区間（non-move/otherも含む）。原因確定ではない。
- プロファイル集計の24KB予算を超えたら試合別→比較別を省略し、
  omittedGameGroups / omittedComparisonGroupsで明示する。全体49KB予算に
  達した場合も比較/試合/代表を省略する。既存診断のredactionは不変。
- session / firstSample / lastSample / firstEndedAtMs / latestDropAtMs /
  fileMtimeMs / updatedAtMsで範囲と鮮度を追跡する。重なるスナップショットの
  集計同士を足してはいけない（分位点は再結合できない）。生データを取得できる
  承認済み経路が別にない限り、Nodeサマライザの複数snapshot再集計は未実施となる。
- sinceDropSentMsは最後のflush時点の未完了末尾であり、現在の停滞時間ではない。
  実行revision・撮影方式・プロセス開始時刻・計測負荷はこのJSONだけでは証明しない。
  送信完了間隔であってゲーム側の受理率・投下数の計測ではない。
- `current` は同じファイルの**未完了の1試行**（top-levelのlegacyカウンタ）を
  そのままbounded projectionしたもの。投下を一度も送らない試合は
  `dropProfile.records` が空で `all` が全部nullになるため、停滞中のループを
  観測できるのはこの節だけになる（2026-10-14の投下ゼロ実測が契機）。
  - 公開するのは固定enumと有限数値のみ: `outcome`
    （observe / hold-sent / drop-sent / error / unknown）、`observations`、
    `holds`、`stageMs`（capture/analyze/ranking/decide/input/cooldown/poll）、
    `reasonCounts`（v1の固定10 reason）、`dropSentIntervals`
    （samples/last/p50/p95/max）、`lastFailure`（stage ×
    errorClass。errorClassは `soren91/loop_metrics.mjs` の
    SAFE_FAILURE_CLASSESを写した固定enumで、未知値は`unknown`）。
  - 想定外の型・範囲・自由文はすべてnullまたは`unknown`へ劣化させ、生の
    エラー文・パス・reason文字列は出さない。schemaが未知でprofileが
    `missing`/`invalid`でも`current`だけは返す。
  - `observations`/`reasonCounts`は最後の`begin()`以降の観測で、turn遷移で
    リセットされる。したがって「その試行で何を見ていたか」の証拠であり、
    試合の受理・進行・投下数の証明ではない。値は最大1秒ごとに上書きされ、
    プロセス停止後は最後のflush時点のスナップショットが残る。
- 24KB予算の間引き対象は従来どおり試合別→比較別で、`current`は残す。
- Actions maskingで数値が`***`になった場合は欠測扱い。復元や推定をしない。
  フェーズ中央値を足して全体中央値と比較しない。

## NetHack 日次結果・終了履歴の読み取り投影

`nethack_history` は既存owner-only `diagnostics` のJSON（VM operations Actionsログ）で取得する。
新しいtimer、公開Issueへの自動転載、artifact、production exec経路は追加しない。
**この変更をmainへ統合しcanonical deployするまでは、新フィールドは実環境で使えない。**

固定収集元はproduction設定から解決した `state_dir/nethack/daily-improvements/YYYY-MM-DD.json`
と `state_dir/nethack/runs/<uuid>.json` のみ。候補catalog、raw progress JSONL、TTY、
xlogfile、dump、advisory、lockは開かず、既に保存されたretrospectiveの数値集計だけを読む。
run終了処理や日次処理を起動しない。owner境界・既存lock・稼働中ゲームに介入しない。

- 各source最大128ディレクトリエントリ、各JSON最大64KiB。directory/fileは
  dirfd相対openと`O_NOFOLLOW`で全階層のsymlinkを拒否、regular fileのみ。
  schema v1のみを投影し、JSON不正・過大・リンク・非regular・日時不正は除外する。
- 日次は観測した有効結果のうち生成日時の新しい7件。statusは`review_ready` /
  `no_new_runs`、run_count、固定candidate category件数、`pending_canary_evaluation` /
  `no_change` / `unknown`、policy_effect=none、automatic_promotion=falseのみ。
  policy変更や自動昇格を示す不正なreportは受理しない。
- 終了runは観測した有効結果のうち終了日時の新しい8件。`dead` / `ascended` /
  `ended` / `ended_unknown`、expedition、score/turns/max_depth、開始・終了日時、
  retrospective有無・生成日時、同種死因件数とprogressのsample/不正行/反復送信/turn/depth/
  HP比率/phase集計だけ。run ID・death reason/signature・候補本文・path・hashは出さない。
  同種死因件数や反復送信は観測パターンであり、失敗原因・改善効果の確定ではない。
- `collected_at`、`generated_at` / `ended_at` / `started_at`、`file_mtime` はUTC epoch秒。
  `ended_at` はproducerのroot `last_finished_at`（終了処理時刻）を投影する。
  session内の`ended_at`やxlogの死亡時刻とは区別する。
  日次`date`はproducer設定のローカル日付。日次結果が無い日は失敗・成功を推測しない。
- 各sourceのstatusは`missing` / `unavailable` / `empty` / `ok` / `partial`。
  `invalid_records`、`excluded_active`、`scanned_entries`、`scan_complete`と
  `omitted_records`を返す。`scan_complete=false`なら全履歴・全体の最新記録を証明しない。
  active/suspended等は結果から除外。nullable数値・`unknown`・progressの`missing`を0件の成功にしない。
  retrospectiveが無ければprogressや同種死因は不明。bounded JSONに含まれる余分な自由文は
  メモリ内のparseだけに留め、allowlist projectionで除去する。
- collector全体の既存36KiB予算を超えた場合は古いrecordsから段階的に省略し、
  各sourceの最新1件を残した状態で既存のAI詳細・worker詳細・Soren比較詳細の
  縮退を適用する。それでも上限超過なら最新1件も省略する。実際に省略したsourceだけ
  `output_omitted=true`と`omitted_records`に記録する。gatewayの49KiB上限・型・深さ・
  secret-redactionは維持。複数ファイルの逐次観測であり原子的snapshotではない。
  遠征・日次の一覧は互いに独立した観測なので、同じ終了runを二重加算しない。
- `completed_runs`は予算縮退でrecordsが全て省略されても比較できるよう、
  scan中に観測した**全ての**有効terminal recordに対する集計`summary`
  （`terminal_status_counts` / `score_present_count` / `analysis_error_counts`）を
  常に返す。recordsの省略はこのsummaryに影響しない。terminal recordごとの
  `analysis_error`は、run producerの固定理由コード（`xlogfile-missing` /
  `xlogfile-truncated` / `no-new-player-xlog-record`）のみそのまま投影し、
  未知のproducer値は固定token `other` に集約して出力しない。型も検証し、
  list/dict 等の非文字列値は frozenset 照合に入らず `other` に集約されるため、
  1 record の不正値で診断全体が落ちることはない（#2515 review）。

## NetHack xlogfile writer 契約の read-only 投影（#753）

`nethack_xlog` は、terminal facts の正本である xlogfile への書込みが
生産側（NetHack 3.6.x の `topten()`）で静かに失われる条件を、
owner-only `diagnostics` の固定 collector から観測する。

NetHack 3.6.x (Linux) の xlog 書込みは `link(xlogfile, xlogfile_lock)` の成功が前提で、
(1) xlogfile が存在しない場合は ENOENT で書込みを skip し自動作成もしない、
(2) 残置された `xlogfile_lock` hard link（プロセス死亡時の残骸）がある場合は
EEXIST リトライ 10 秒後に無言で skip する。どちらも run store 側からは
`ended_unknown` + `analysis_error` としてしか観測できないため、
この投影が path/permission 契約の第一次証拠になる。

- 収集元は production の tracked `config/games/nethack.toml` から解決した固定 path
  （既定は `/var/games/nethack/` 配下）と、その兄弟契約ファイル
  （`xlogfile` / `xlogfile_lock` / `logfile` / `logfile_lock` / `record`）、
  親 playground・save_dir・dump_dir のみ。任意 path・環境変数・argv は追わない。
- 各 path は `lstat`（symlink を追わない）で存在/種別/size/mtime/mode/uid/gid/nlink のみ。
  不在は `{"exists": false}` だけを返し、null の羅列で予算を消費しない。
  `xlogfile_lock`・`logfile_lock` には対象との同 inode 判定（dev+ino 一致 boolean）を付け、
  残置 lock と共有 hard link を区別する。path 名・内容・hash は出力しない。
- `launchers` は固定 2 path（`/usr/games/nethack` / `/usr/local/bin/nethack`）の
  lstat・symlink 先（文字種検証済み）・script 先頭 4KiB からの allowlist 変数
  （`HACKDIR` 等 4 key）のみ。`alternates` は固定 2 path
  （`/var/lib/nethack/xlogfile` / `/usr/games/lib/nethackdir/xlogfile`）の lstat のみ。
- `xlog_siblings` は playground の `xlogfile*` 名（字種検証済み、最大8件、名前順）の
  size/mtime のみ。`binaries` は `dpkg-query -L`（固定2 package）と launcher symlink 先から
  実体 binary（最大2件）を解決し、8MiB 上限で `xlogfile` という文字列の compile-in 有無を
  見る（binary 内容は出力しない）。読み取り量は残り byte 数で制限し、needle は
  256KiB read 境界を跨いでも needle長-1 byte の重なり保持で検出する。
  `binary_scan_complete=false` は上限打切りや file 短縮で全文走査ではないことを示し、
  その negative は確定値ではない。
- `save_dir_entries` は dirfd + `O_NOFOLLOW` で player save file の個数と最新 mtime だけを
  数える（最大128エントリ、symlink は数えない）。
- `processes` は `/proc` の `comm` が `nethack` の稼働 process（最大4件）について
  pid / 実効 uid / 実効 gid / starttime ticks のみ。cmdline・environment・fd は読まない。
- `packages` は固定2 package 名（`nethack-common` / `nethack-console`）の
  `dpkg-query -W` 結果（package / version / installed）のみ。失敗時は `available=false`。
- `sysconf` は `/etc/nethack/sysconf` から `xlogfile` / `logfile` / `dumplogfile` の
  3 key だけを case-insensitive に読み、安全な文字種の value のみ投影する
  （それ以外は `configured` に key 名だけ残し value は `null`）。
  他の key・コメント・行数は出力しない。
- `xlog` は xlogfile の末尾最大64KiBを読み、**完全行のみ**から
  完全行数・先頭切詰め有無・最終/先頭 record の数値 field（starttime / endtime / points /
  turns / maxlvl / achieve）と `name_matches_player` boolean だけを導出する。
  先頭切詰め時は window 先頭の行途中断片を完全行として数えず、最初の改行以降だけを
  対象化する（改行が無ければ完全行 0）。読めない xlogfile は `{"readable": false}` のみ。
  `record_span`（最古 record の `starttime` / 最新 record の `endtime` / 完全行数）は
  #753 の timeline 証拠であり、soft budget の縮退でも落とさない。
  death 文字列・player 名・生の行内容は出力しない。
- `findings` は導出 boolean（`xlog_lock_present` / `xlog_lock_shares_inode` /
  `xlog_file_shared_hardlink`）のみ。原因の確定は人間の判断であり、
  collector は修復・削除・権限変更を一切行わない。
- 予算縮退では、stat 系の lock/permission 証拠と `record_span` が最後まで残る。省略対象は
  tail の先頭/最終 record 詳細・binaries・siblings・processes・sysconf values の
  各 optional detail（`*_omitted` marker 付き）だけで、それらは他の optional detail と
  同じ段階で先に落ちる。セクション全体の collapse は hard ceiling の最終手段のみ。

## tmux サーバ所有と resolver daemon の read-only 投影（#1286 follow-up）

`tmux_servers` は本番コーナーが使う tmux 既定サーバと評価ジョブ専用サーバ
`docich-eval` の読取成否・セッション数・確認できた有無を投影する（`schema_version` は2）。
本番サーバのサーバ名は `default`。tmux は `-L` を付けないときソケットを `default` と
名付けるので `tmux -L default` が本番サーバそのものを読む。`docich` はその既定サーバ内の
**セッション名**であり、サーバ名ではない。以前の `-L docich` は VM 上に存在せず、
本番サーバを常に「不在」（`readable=false`）と誤報していた（2026-10-08 実測）。
セッション名は取得せず、`list-sessions -F 1` の固定マーカーだけを数える。
出力は4KiBまでを検証し、不正な行、上限超過、timeout、実行失敗、非zero終了は
`readable=false / present=null / session_count=null` とする。不在や0件に推測変換しない。
正常終了した空出力だけが `readable=true / present=false / session_count=0` になる。
旧 `sessions` フィールドは出力しない。各サーバの結果は独立している。
#1284 で評価用 tmux を専用サーバへ隔離したが、分離はプロセスツリーからは直接観測できなかった。
この投影で「eval セッションが `docich-eval` 上に作られ、本番 `default` サーバに現れない」ことを
diagnostics で直接確認できる。`tmux -L <server> list-sessions` は read-only（入力送信なし）。

`resolver_daemon` は `docich-resolver-improve.service` と
`docich-resolver-improve-gnurobots.service` の active / enabled 状態を投影する
（`schema_version` は2）。これらの長命 daemon が稼働していると、次回再起動まで
本番既定 tmux サーバを共有し続ける（#1284 の対象外経路）。
2026-10-08 の VM 実測では両 unit は **system manager**（`/etc/systemd/system`、
root所有、`WantedBy=default.target`）に install されており、`systemctl --user` だけを
見ると `not-found`（= 未稼働に見える）になっていた。実際には
`docich-resolver-improve.service` は 2026-10-01 06:18 JST から稼働中だった。
そのため `active` / `enabled` は system / user 両 manager の集約（いずれかが true なら true、
既知の false は unknown に隠されない）とし、manager ごとの生値は `scopes` に残す。
`systemctl is-active` / `is-enabled`（system / `--user`）は read-only。


## YouTube / Kick title sync の read-only 投影

`stream_title_sync` は Soren の `tmp/state/stream_title_sync/events.jsonl` を最大32KiBだけ読み、
親の`tmp` / `state` / 専用journal directoryと末尾ファイルの各segmentをdirfd + nofollowで開き、
専用directory・regular fileのowner/mode、最大32KiB、最新レコードの厳密schema・固定enum・
時刻・SHAを検証する。出力するのは時刻、年齢、記録イベント、実行時Soren SHAと現在の
gitlink SHA一致、固定skip reason、YouTube/Kickの固定結果enumだけ。追加フィールド、
不正enum、親/末尾symlink、owner-onlyでないdirectory/file、過大・不完全・古い記録は結果を
公開せず固定状態として返す。新しい状態項目はdiagnosticのoverall severityに影響しない。

`youtube_stream_id_present` / `kick_broadcaster_id_present` はhelper実行時の
`YOUTUBE_BROADCAST_STREAM_ID` / `KICK_BROADCASTER_USER_ID` が非空だったかだけを
表す厳密なboolean。空文字・未設定はfalse、空白だけでも非空はtrue。ID値やcredentialの
存在は記録・投影しない。collector自身の環境から補完しない。追加済みschemaは両項目を
必須とし、従来の2schemaも受理するが未記録項目は`null`（unknown）。freshかつ現在の
gitlinkに対応するsource fingerprintが一致する記録だけでbooleanを表示し、stale / future /
malformed / source_unavailable / source_mismatchでは`null`を維持する。最大行512bytes、
journal最大32KiBは維持する。存在だけではIDの正当性や認証・配信成功を証明しない。

`event=skipped` は `skip_reason` が示す早期終了を表し、両platformは `not_run`。
`event=started` のままならhelper開始後に結果行が残らなかった状態。
`event=result` の `updated` はAPI read-backが依頼したtitleと一致したことを示すだけで、
視聴者に見える実表示は保証しない。Twitchのtitle/categoryはこのjournalへ保存しない。
統合ログ `stream-game.log` / `stream_title_day.log` の読取り・出力もしない。

## 出さないもの

secrets・token・raw environment・prompt 本文・生成本文・HTTP header・
ファイル内容。lock owner の token、retry batch の中身は読まない・出さない。
key 名に `API_KEY` / `TOKEN` / `SECRET` / `STREAM_KEY` / `PASSWORD` /
`AUTHORIZATION` / `COOKIE` / `PRIVATE_KEY` を含む値は gateway 側でも `***` 化する。

## Registry / manifest

`ops/vm_actions/runtime_registry.py` が診断カバレッジの正本。
worker 追加時はここへ 1 行（name / required / category / pid 特殊形）を足す。
queue lane は lock dir スキャンで自動検出されるため登録不要（代表 lane のみ列挙）。

半熟の場面実況は `HANJUKU_SCENE_ONESHOT` に module・runtime内のproducer/state/lock/log・
診断key・既存radio laneを登録する。確認済みfactがある時だけ最大1件を処理するゲーム所属の
短命processなので `start_all.sh` の常駐worker表へは追加しない。待機中にPIDが無いことは正常。
healthは同じruntime/世代の `scene_narration` の鮮度と `generate_*` / `deliver_*` の進行で判断する。
初回のlauncher receiptだけでworker journalが未完成の間は、workerのstatusは `unavailable` になる。

CI は以下を検査する（`ops/vm_actions/tests/test_runtime_registry.py`）：

- registry schema と required の保守性。
- `start_all.sh` の `WORKER_NAMES` / `_pidfile_for_worker` / `_pattern_for_worker`
  にある worker が全て registry に存在すること。
  `start_all.sh` は CI が soviet_now main から取得する（取得失敗時は skip）。
- 「diagnostics ファイルの diff 必須」のような雑な check はしない。

## 実行方法

```console
gh workflow run "VM operations" --repo azumag/docich --ref main \
  -f operation=diagnostics -f target=production -f ref=main
```

固定 run id は public repo の retention で消えるため（#1881）、正規参照としては
pin しない。次の helper が、完了した `workflow_dispatch` run のうち最新の
read-only diagnostics run を解決する。

```console
python3 ops/vm_actions/resolve_canonical_diagnostics_run.py
python3 ops/vm_actions/resolve_canonical_diagnostics_run.py --dump-json <databaseId>
```

解決条件は run 成功・`main`・latest attempt の全 job 成功、
`Query read-only runtime diagnostics` 成功、`Deploy staged code` /
`Execute owner command on VM` が skipped、authorization log が
`diagnostics / production / main` かつ `INPUT_CONFIRM` が空であること。
sanitized structured output が無い候補は採用しない。`--dump-json` は log の
JSON をそのまま出し、`***` マスクを復元しない。deploy / exec / 別 operation の
job を診断代替にしない。

結果 JSON は Actions ログに出る（sanitized・bounded のため安全）。
GitHub 側の secret masking により、PID や commit SHA の一部が `***` 表示に
なる場合がある（secret 断片との偶然一致。漏洩ではない）。
定期監視を追加する場合は healthy → 通知なし、warning / critical → 結果を残す
運用にし、noisy な schedule を増やさないこと。

### 解決器の限界（実測）

参照を pin しない代わりに解決器へ依存するときは、次の限界を前提にする。

- **探索範囲は run の発生量に依存する。** `--scan`（既定 30）は完了した
  `workflow_dispatch` run を新しい順に調べるため、30 件が遡る範囲は実測で
  約 5 時間分だった（2026-10-16 の 12 時間で `vm-operations.yml` は 68 run）。
  それより古い diagnostics run を探すときは `--scan` を明示的に増やすか、
  対象の `databaseId` を直接使う。
- **`--dump-json` の出力は JSON として再パースできない場合がある。** secret
  masking は数値や日付の途中にも `***` を入れるためで、実測した 1 回の出力に
  35 箇所あった（`"at":1792176165.***7505`、`"ends_at":"2026-10-16T***:14:58"` 等）。
  `***` を保持したまま必要な固定 field を抜き出す消費側を前提にする。この性質は
  回帰テストが固定する既知契約で、パース失敗は不具合ではない。
- **最後の diagnostics run が古いと log の保持期限で解決できないことがある。**
  artifact/log retention は 1 日（`actions/permissions/artifact-and-log-retention`
  の実測値）で、log を読めない候補は解決器が skip する。実測では 1.6 日前の run は
  取得でき、15 日前の run は HTTP 410 だった。長期間 diagnostics run が無いと
  「run は存在するのに正規参照を解決できない」状態になりうる。run の消滅（#1881）
  と合わせ、古い参照を保存して使い回さない。

owner起動の evidence workflow（`.github/workflows/soren91-evidence-export.yml`、
`.github/workflows/hanjuku-evidence-query.yml`、
`.github/workflows/hanjuku-evidence-export.yml`）はそれぞれ専用の concurrency group
（`soren91-evidence-export-<repo>` / `hanjuku-evidence-query-<repo>` /
`hanjuku-evidence-export-<repo>`）を持ち、scheduled `vm-storage-monitor` と同一 group
を共有しない。#1922 のとおり、開始しない queued run が shared group を占有して
以降の owner 起動要求をすべて cancel させ、証跡を取得できなくなるからである。
monitor 側は `vm-storage-monitor-v2-<repo>` を持つ。VM 操作の直列化は
gateway の固定 `vm-operations.lock` が担うため、group は二重の防御であり、失敗時に
owner の read-only 証跡取得まで止める必要はない。

旧名 `vm-storage-monitor-<repo>` は 2026-10-15 まで owner 起動 evidence workflow と
共有していた group で、その間に開始しない queued run が占有した状態が残り、evidence
側が専用 group へ移った後も monitor だけが同じ group へ到着し続けた。結果として
scheduled monitor の run は 2026-10-02 以降 job を作らない `pending` / `cancelled`
のまま停止した（#2376）。group 名は 2026-10-17 に `vm-storage-monitor-v2-<repo>` へ
更新し、wedge した group 状態から離脱した。production control lane を
`vm-operations-v2-<repo>` へ移した #1875 と同じ処置である。旧名は再利用しない。

`.github/workflows/vm-storage-monitor.yml` は storage / runtime health に加えて、
diagnostics の `tracked_drift.drift_detected=1`（= canonical deploy を塞ぐ
`git_clean(root)==false`）を **`[VM deploy] tracked drift alert` 1件の open Issue**
へ dedup して通知する。復旧（`drift_detected=0`）で自動 close する。通知本文は
固定カテゴリと counter だけで、path / filename / diff / bytes / raw exception を
含めない。診断が取得できない・`tracked_drift` が無い場合はアラートを変更しない。

同 monitor は production の `status` も見て、`configured` でない状態
（out-of-band な HEAD/baseline 移動、`recovery_required`、`bootstrap_required`）を
**`[VM deploy] production baseline alert`** として1件に dedup して通知する。
tracked drift の形（`drift_detected=1`）は tracked drift alert が扱うため二重通知
しない。`configured` へ戻ると自動 close する。本文は固定 status enum・counter・
commit SHA のみで、path・diff・bytes・raw exception を含めない。

## CPU profiling

CPU 消費・wake-up・spawn 経路の短時間 baseline は diagnostics とは別の read-only
profiler で取る。通常 `diagnostics` は保存済みprofileのfreshnessを示すだけ。
owner-only `cpu_profile / production / main` workflow dispatch は固定条件のone-shot計測を
実行し、freshなsanitized projectionを返す。契約と実行方法は [cpu-profiling.md](cpu-profiling.md)（#970）。

NetHack tiles の `nethack_tiles.json` はruntime固有のprivate ownership manifestである。
diagnostics は active runtime と manifest のgeneration/ownership一致、固定status/mode/reason、
更新からの経過秒、cleanup完了だけを投影する。PID、argv、profile path、例外本文は公開しない。

## Runtime 変更 checklist

通常コーナーの共通BGMは、独立した `corner_bgm` 要約で確認する。
`soren-bgm.service` の状態、workerの30秒heartbeatとMainPIDの一致、開始判定の固定reason、
所有ffplayの生存・終了コード・終了回数、専用Pulse識別子と親子関係で確認したstreamの
mute/corked/音量/`soren_null`への接続を投影する。古いworkerのsnapshotは `stale` とし、
その判定を現在の状態に流用しない。一般の `pulse_sink_inputs.streams` が出力予算で省略されても
この要約は保持する。`running` や `mute=false` は信号・配信音声の確認を意味しない。
読み取り不能は不明として扱い、raw環境変数・argv・音源パス・playerのstderrは出力しない。

worker / queue / model / provider / fallback / runtime component を変えたら：

- [ ] runtime registry / manifest（`runtime_registry.py`）
- [ ] worker health 契約（pid file・pause・pattern）
- [ ] queue registry（lane・owner 形式・stale 閾値）
- [ ] structured telemetry（`ai_stats` event・return code）
- [ ] diagnostics coverage（collector＋回帰テスト）
- [ ] regression tests（正常・停止・stale・重複・未登録・malformed・redact・bound）
- [ ] secret-redaction（新規 field が出ていないか）
- [ ] deploy 影響（collector はデプロイ済み main から動くこと）

## OpenCode retention health

`opencode_retention` は固定stateの `attempt` / `default` / `worker` と専用timerのactive/enabledを返す。
statusは `running/completed/gate_timeout/disabled/deferred/failed`、reason/stageは固定enum、前後bytes・page数・削除件数・日時だけを許可する。
最新attemptが失敗/延期/ロック待機切れ、3時間超stale、timer停止ならWARN。古いDB単位のcompletedを最新attemptの成功とみなさない。
秘密・prompt・DB行の内容・例外本文は出力しない。DBファイルサイズと実際のroot空き容量は別に実測する。

`docich-opencode-retention.timer` はゲームから独立した1時間毎のoneshot maintenanceで、supervisor worker / AI queueは追加しない。
直近1日のOpenCode実行履歴を残す（認証・ゲーム結果・戦略履歴は別管理）。3日分で5GB級に再増加したため、#1337の回復後も1日保持を定期適用する。
既存のdefault DB retention opt-outは維持する。timerはcanonical deployだけで導入し、ゲーム・配信・共通音声を再起動しない。
status 75は未実行/延期であり、serviceの異常終了ループを避けてもdiagnosticsで成功には変換しない。

回収は1GiBの空きを確保し、gate→SQLite EXCLUSIVE→transactional prune→checkpoint→private VACUUM INTO→transactional backup→checkpointを使う。
各段の見込み容量と途中の空きを判定する。コピーのサイズを実測してから書き戻しを予算化する。ライブDB/WALをrename/unlinkしない。
デプロイepoch=3の1回回収後はtimerが継続担当する。回収が失敗/延期ならcanonical runも非成功になり、`diagnostics`で段階・理由と容量を確認する。

OpenCodeの `compact_storage` は `disk` / `memory` を区別する。圧縮コピーにtmpfsを利用した場合も、root空き1GiB・利用可能RAM4GiB（cgroup制限込み）を予約する。`insufficient_memory` / `memory_unknown` は成功ではなく延期で、次回の定期実行へ持ち越す。

### 半熟英雄の停滞終了checkpoint

`retro_corner.saved_checkpoint`は、cornerのruntime/generation/leaseと一致した固定保存境界を
`save_status=waiting/failed/saved_receipt/unverified`へ投影する。ファイル存在・サイズ・保存時の
metadataとの一致は別フィールドで示す。`saved_receipt`だけでは現ファイルのSHA再検証や復元成功を
証明しない。ゲーム終了を許可するadapter側はpause・実ファイルSHAを再検証する。
診断は保存ゲームの内容を読まず、checkpoint名を入力パスとして使用せず、ファイル名・パス・
SHA・request/lease/runtime識別子・自由文を出力しない。worker/queue/manifestは追加しない。

`hanjuku_resume` は固定保存再開のmetadataだけを報告します。`prepared` は未公開snapshotのみ、`queued` は標準manual queueに対応する予約、`claimed` は同候補への移行途中、`verified_receipt` は同active runtimeのcore roundtrip/native章/PLAYING検査を記録したreceiptです。公開直後に中断してprivate receiptが`prepared`のままでも、同IDの実manual queueがあれば`queued`を報告します。現在の保存の完全性やライブ画面の進行は別確認です。identity不一致・欠落はunavailable/unverifiedで、保存データ・ROM・source path・SHA・request/lease識別子・自由本文は出力しません。停止済みruntimeのreceiptを現在進行中として表示しません。


半熟の投影採用は `HANJUKU_PROJECTION_RECOVERY` に登録する既存game windowに属するguard paneです。共有19worker表やAI queueを増やしません。`hanjuku_projection_recovery` は同active identityのreceiptとguard/playerのkernel起動時刻を照合し、固定status・presence・identity一致・生存booleanだけを返します。PID・argv・native source・runtime/lease識別子・自由文は出力しません。`healthy` はready receiptとlive process birth/投影stateの一致であり、公開映像・音声の視聴証明ではありません。別epoch・欠落・symlink・PID再利用・state不同はunavailable/unverifiedです。

### 半熟英雄の予想が所有する round の停滞分類

`hanjuku_predictions` に `ownership` を足す（round を所有している＝`round` が在り state が読める場合だけ）。
`hanjuku_predictions` は外部記録が検証を通らなくなると round の所有を維持したまま
`error=invalid_state` で固定300秒の retry を回すため、「どの記録が決定的か」と
「次の poll が retry backoff（約300秒）か通常 poll（約30秒）か」を固定 enum /
boolean / 有界整数だけで示す。出すのは `retry_delay_seconds`（0〜86400に clamp）、
`blocker`（`runtime-identity-invalid` / `runtime-missing` / `run-record-missing` /
`run-record-unreadable` / `run-record-identity-mismatch` / `terminal-evidence-malformed` /
`result-record-unreadable` / `result-record-mismatch` / `progress-record-unreadable`、
決定的な記録が無ければ null）、`runtime_present` / `run_record_present` /
`run_record_readable` / `result_record_present` / `progress_record_present`、
`terminal_reason`（`game_over` / `screen_stalled` / `input_stalled` のみ）。

読み取りは runtime 配下の `hanjuku_run.json` / `hanjuku_prediction_result.json` /
`hanjuku_progress.json` の3つだけで、各16KiB上限・regular file・symlink拒否・
`runtime_id` は `..` と区切り文字を拒否する。`runtime_id`・lease・frame SHA・
記録の本文・視聴者名は出力しない。これは検証ロジックの再実装ではなく、
決定的な記録の所在を示す有界な分類であり、修正の要否は実測と
`docs/features/yosou-oh-corner.md` の記録契約に従って判断する。
