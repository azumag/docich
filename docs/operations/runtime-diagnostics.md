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
  "workers": {"expected": 19, "running": 6, "stopped": [], "paused": [],
              "duplicates": [], "zombies": [], "stale_pid_files": [],
              "unregistered": [], "required_down": [], "required_stale": [], "details": {}},
  "queues": {"lanes": {"radio": {"locked": true, "owner_alive": true, "age_sec": 43}},
             "stale_locks": 0, "queue_giveups_15m": 0},
  "ai": {"attempts_15m": 0, "failures_15m": 0, "rate_limits_15m": 0,
         "fallbacks_15m": 0, "all_failed_15m": 0, "recent_events": [],
         "anomalous_components": {}},
  "improvement": {"running": false, "stale": false, ...},
  "corners": {"state_dir_found": true,
              "game_switch": {"present": true, "phase": "ready", "active_game": "sorengame",
                              "last_status": "succeeded", "last_error_code": null, ...},
              "retro_corner": {"present": true, "status": "active", "game": "gnurobots", ...},
              "paper_corner": {"present": false, "readable": false},
              "presentation": {"present": true, "mode": "detailed", ...},
              "boundary": {"improvement": {"present": true, "completed_at": 0, "age_sec": 0},
                           "prediction": {"present": false, "completed_at": null, "age_sec": -1}},
              "ab": {"state_present": true, "pattern": "ABBA", "games_lines": 12,
                     "games_tainted": 0, "games_age_sec": 42, "candidate_pending": true, ...}}
}
```

- `status` は `ok` / `warn` / `critical` に正規化する。
- critical: required worker の停止（pause 中を除く）/ required の stale PID。
- warn: paused worker、未登録 PID、重複、zombie、stale lock、直近 all_failed /
  queue give-up、改善ループの stale・retry 滞留。
- 単発の rate-limit だけで critical にしない。rate-limit は件数のみ報告する。
- queue waiter 数は lock 形式から観測できないため報告しない（不明は不明と扱う）。
- 診断は stale lock を削除しない。観測のみ。
- `corners` はコーナー/番組のライフサイクル観測であり、現時点では severity を
  変えない（時間監視の通知を増やさない）。失敗状態の警報化は別途判断する。

## 収集元（すべて read-only）

- worker: `tmp/state/*.pid`（+ `tmp/.soren_loop.lock/pid`、
  `tmp/state/.soviet_watchdog.lock/owner`）、`*.paused` マーカー、
  `worker_duplicates.json`（supervisor 報告、10 分以内のみ採用）。
  生死判定は `src/docich/runtime_backend.py` の helper を再利用する。
- queue: `tmp/state/.ai_generation_locks/<lane>/owner`
 （token は出さず PID 生死・age のみ）。stale 閾値は 900s。
  `*.owner_guard.lock` / `*.owner_guard.d` は mutex guard のため lane 扱いしない。
- AI: `tmp/state/ai_stats/YYYYMMDD.jsonl`（当日＋前日按分、直近 900s）。
  `attempt` / `ok` は件数のみ。`recent_events` は fail/winner/all_failed/
  queue_giveup/giveup 系のみ直近 20 件。error は 200 字に丸め・redact。
  fallback 成功は「同一 label に fail と別 agent の winner」の heuristic。
- improve: `improve_state.json`、`tmp/improve.lock`、
  `improve_monitor_status.json`、`rate_limit_backoff` と retry batch の存在のみ
  （内容は読まない）。running かつ更新停滞／PID 死亡なら stale。
- corner: 本番 `state_dir` 配下の `game_switch.json` / `retro_corner.json` /
  `paper_corner.json` / `trading/presentation.json`。status・時刻・announce 件数のみで、
  announce/台本本文は読まない・出さない。state_dir は固定 config
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
