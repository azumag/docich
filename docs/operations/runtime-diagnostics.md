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

## Output 契約

```json
{
  "status": "ok",
  "meta": {"generated_at": 0, "window_sec": 900, "docich_head": null, "soviet_head": null, ...},
  "tracked_drift": {"parent_tracked_dirty": 0, "owned_submodule_head_mismatch": 0,
                    "owned_submodule_tracked_dirty": 0,
                    "owned_submodule_missing_or_invalid": 0,
                    "scan_complete": 1, "unknown": 0, "drift_detected": 0},
  "workers": {"expected": 19, "running": 6, "stopped": [], "paused": [],
              "duplicates": [], "zombies": [], "stale_pid_files": [],
              "unregistered": [], "required_down": [], "required_stale": [], "details": {}},
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
              "soren_game": {"present": true, "readable": true, "state": "MOVE",
                             "age_sec": 2, "founding_seen": false,
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
- `corners` はコーナー/番組のライフサイクル観測。`retro_corner` が
  `status=failed` かつ `recovery_required=true` の場合だけ severity を `warn` にし、
  owner-only の固定 `recover-failed` 操作を許可する。`draining` / `recovery_required`
  のcanonical phaseは自動でリセットしない。
- `game_switch_fifo` は `game-switch/requests` のreceiptを固定上限で読み、queued件数と
  FIFO先頭の operation/target/age だけを出す。request ID、payload、生成本文、秘密情報は
  出さない。malformed receiptやscan未完了は復旧せず、監視側で要対応として扱う。
- `corners.soren_game` は試合状態の固定enum（`MOVE`/`DROP`/`WAITING`/`STOP`/
  `GAMEOVER`）、state更新からの秒数、建国markerの有無、試合数、score、駒数、
  試合runnerのPID/生存だけを出す。盤面内容、ログ、入力、生成文は出さない。
  `STOP` が古くても `founding_seen=true` の場合は通常の試合終了と判定しない。
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
  eligible_count、pending有無、および設定由来の `schedule_mode` / `cooldown_seconds`
  のみ。seed・request payload・自由文は出さない。
  さらに、`error_kind`（`docich.corner_rotation.ERROR_KINDS` と同一の固定enum。
  例外本文はstateにもdiagnosticsにも書かない。欠落はnull、不正値は`unknown`。
  直近のlatch分類として次にlatchし直すまで残る）と、予約（`pending`）が在る限り
  （latch中かどうかを問わず）次を足す:
  `pending_corner`、`pending_phase`（`selected`/`dispatched`/`unknown`）、
  `pending_age_sec`（-1は不明）、`pending_owner`（固定8種のcorner stateのうち
  同じrequestを記録したstate名。該当なし`none`、読取不可`unknown`、予約なし`absent`）、
  `pending_owner_status`。request UUIDは固定stateとの照合にだけ使い出力には含めない。
  これで「まだ起動していない」「既に終了している」「実行中・corner側の復旧が要る」を
  証跡から区別できる（#986）。
  `recovery_required`は次cornerを停止する実行契約であり、診断自体は復旧操作をしない。
  `corners.corner_rotation_timer` は支配的なtimer unit名（移行後は
  `docich-corner-rotation.timer`）、active/enabled、旧名が正しいaliasかを示す
  bounded boolean `legacy_alias` のみを出す。unit path・alias target・state pathは出さない。

- rotation 待機の補助証跡: `corners.rotation_evidence` に固定8種の
  corner state（retro/PAPER/Soren91/NetHackの通常・manual）と固定10種の
  改善結果（9ゲーム＋PAPER）を出す。状態enum、ゲームenum、完了時刻、
  improve起動boolean、明示的なrecovery_requiredを観測する。改善結果は
  status、started_at、completed_at、失敗理由の固定enum `reason_code` / `phase`
  （欠落・未知は `unknown`）と既存lockの `held/free/absent/unknown` のみ。
  `spawned=true` と正常なrequired workerだけでは、改善の終了証跡を確認できない。
  改善status欠落・failed・running・corner完了より古いstarted_at・保持中lockを
  区別し、`other-corner-needs-finish-or-recovery` の調査に用いる。
  status/lock単独から子プロセス終了・復旧可否を断定しない。
  存在しないlockは作成せず、既存lockを非待機でprobeして即解放する。
  state由来パス、catalog由来パス、request ID、PID、ログ、prompt、save本文は出さない。
  ファイルは64KiB上限、リンク・非regular fileは拒否、不正な値はunknown/null。
  読み取りを順に行う観測なので、一つの原子的な状態スナップショットではない。
  この追加は待機条件・FIFO・scheduler・復旧操作を変更しない。

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
  production checkout 内に制約する。
- boundary: Soren `tmp/state/corner_boundary_improvement.json` /
  `corner_boundary_prediction.json` の `completed_at` と age のみ。コーナーの
  境界待ちの可否を判定できる。
- A/B: Soren `tmp/state/ab_state.json`（pattern / started_at / 件数 / mtime）、
  `ab_games.jsonl`（行数・tainted 数・最終 arm）、`ab_candidate/`（有無）。
  hash・env 文字列・戦略本文は読まない・出さない。A/B 中は improvement
  boundary が保留されるため、コーナー遅延の直接原因になる。
- meta: デプロイ済み docich HEAD と soviet_now gitlink（検証用。secret ではない）。
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
- Actions maskingで数値が`***`になった場合は欠測扱い。復元や推定をしない。
  フェーズ中央値を足して全体中央値と比較しない。

## 出さないもの

secrets・token・raw environment・prompt 本文・生成本文・HTTP header・
ファイル内容。lock owner の token、retry batch の中身は読まない・出さない。
key 名に `API_KEY` / `TOKEN` / `SECRET` / `STREAM_KEY` / `PASSWORD` /
`AUTHORIZATION` / `COOKIE` / `PRIVATE_KEY` を含む値は gateway 側でも `***` 化する。

## Registry / manifest

`ops/vm_actions/runtime_registry.py` が診断カバレッジの正本。
worker 追加時はここへ 1 行（name / required / category / pid 特殊形）を足す。
queue lane は lock dir スキャンで自動検出されるため登録不要（代表 lane のみ列挙）。

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

結果 JSON は Actions ログに出る（sanitized・bounded のため安全）。
GitHub 側の secret masking により、PID や commit SHA の一部が `***` 表示に
なる場合がある（secret 断片との偶然一致。漏洩ではない）。
定期監視を追加する場合は healthy → 通知なし、warning / critical → 結果を残す
運用にし、noisy な schedule を増やさないこと。

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
profiler で取る。契約と実行方法は [cpu-profiling.md](cpu-profiling.md)（#970）。

## Runtime 変更 checklist

worker / queue / model / provider / fallback / runtime component を変えたら：

- [ ] runtime registry / manifest（`runtime_registry.py`）
- [ ] worker health 契約（pid file・pause・pattern）
- [ ] queue registry（lane・owner 形式・stale 閾値）
- [ ] structured telemetry（`ai_stats` event・return code）
- [ ] diagnostics coverage（collector＋回帰テスト）
- [ ] regression tests（正常・停止・stale・重複・未登録・malformed・redact・bound）
- [ ] secret-redaction（新規 field が出ていないか）
- [ ] deploy 影響（collector はデプロイ済み main から動くこと）
