# CPU profiling（bounded, read-only）— #970 PR1

docich VM 全体で「何が CPU を使っているか」「どの経路が wake-up / process spawn を
量産しているか」を、同一条件で再現できる形で記録するための短時間 profiler。
最適化（PR2 以降）は、この baseline と同一 scenario の before/after 比較で判断する。

正本コード: `ops/vm_actions/profile_cpu.py`（stdlib のみ・単一ファイル）

## 契約

- **read-only**: `/proc` を読むだけ。signal / renice / kill / lock 操作 / state 書き込みはしない。
  書き込むのは `--output` で明示した report ファイルだけ。cadence や設定は変えない。
- **bounded**: `--duration` 10〜600 秒（既定 60）、`--interval` 0.5〜10 秒（既定 1）、
  `--warmup` 0〜120 秒に clamp。常駐しない。出力は component 40 行、spawn 30 行、
  top process 20 行で打ち切り、切り捨て件数を `components_truncated` に出す。
- **sanitized**: `/proc/<pid>/cmdline` はプロセス内で分類にだけ使い、出力しない。
  environ・cwd・ファイル内容は読まない。出力ラベルは次のいずれかだけ:
  - 固定の実行ファイル表（`retroarch`, `x_server`, `xdotool`, `audio_server`, `browser`,
    `game:<固定名>` など）と `ffmpeg:capture|stream|x11grab|other`
  - 検証済み名前: `docich:<subcommand>`（`[a-z0-9-]{1,32}`）、`py:<module|script.py>`、
    `sh:<script.sh>`
  - `runtime_registry.WORKERS` の pid file から解決した `worker:<name>`（開始時の `(pid, starttime)` で照合し、計測中に再利用された PID は worker 扱いしない）
  - `kernel` / `shell` / `python` / `other` / `profiler`
- 汎用ラベル（`shell` / `python` / `other`）の子は、最も近い `worker:*` 祖先に帰属させる。
  spawn は `component <- spawner` の組で数える（例: `ffmpeg:capture <- docich:run`）。

## 出力（`--json`）

- `host`: 期間全体の `cpu_busy_pct` / `cpu_iowait_pct`（全コア比）、interval ごとの
  busy p50/p95、load1 p50/p95、runnable p50/p95、`forks`（`/proc/stat processes` の差分。
  1 interval 未満で終わる短命プロセスも含む正確な fork 数）、`ctxt_per_sec`、プロセス数。
- `components[]`: `cpu_pct`（100 = 1 コア）、`cpu_share_pct`、processes、`spawned`
  （期間内に生まれて観測できた数）、voluntary/nonvoluntary context switch/秒、RSS 合計、
  thread 数合計。CPU 降順。
- `spawns[]`: 期間内に生まれたプロセスの `component`/`spawner` 別件数。
- `top_processes[]`: PID・component・cpu_pct・threads・RSS のみ。
- `meta.profiler_cpu_sec`: profiler 自身の CPU 消費（計測 overhead の確認用）。

## 既知の限界

- 2 sample 間で終了したプロセスは、最後の sample 以降の CPU tick を失う。短命の
  ffmpeg/xdotool は `spawned` より `host.forks` の方が正確（per-component には帰属できない）。
  spawn 経路を厳密に数えたい scenario は `--interval 0.5` を使う。
- agent の observe/decide/act 時間、screenshot capture/decode 時間、RetroArch FPS、
  dashboard request rate はプロセス外から観測できないため対象外。既存の health / telemetry
  で別途記録する（必要なら follow-up で固定 telemetry を足す）。
- gateway の `diagnostics` operation（collector timeout 60s）には載せていない。60 秒以上の
  sample を安全に返すには別 operation が必要で、follow-up とする。

## 実行方法（VM 上、owner 権限の範囲で）

```sh
cd /home/ubuntu/docich   # production checkout。tracked file は変更しない
python3 ops/vm_actions/profile_cpu.py sample --scenario idle \
  --warmup 30 --duration 90 --output /tmp/cpu-idle.json
python3 ops/vm_actions/profile_cpu.py sample --scenario hanjuku --warmup 30 --duration 90 \
  --output /tmp/cpu-hanjuku.json
python3 ops/vm_actions/profile_cpu.py compare /tmp/cpu-before.json /tmp/cpu-after.json
```

report は `/tmp` など runtime 外へ置き、Issue #970 へ表（または JSON）を貼る。
report には秘密情報・command line・パスは含まれないが、貼る前に目視確認する。

## Baseline scenario（#970 Phase 0）

同一 VM・同一 commit で、warm-up 後 60 秒以上:

1. `idle` — stream/common worker のみの通常待機
2. `hanjuku` など CPU 負荷が高い代表 corner 稼働中
3. `retro-frame` — RetroArch + frame analysis 系 corner
4. `trading-visible` / `trading-hidden` — trading dashboard 表示中 / 非表示
5. 変更対象に応じた追加 scenario
