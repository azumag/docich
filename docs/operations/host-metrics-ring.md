# ホスト指標リング（再起動をまたぐ bounded な host metrics）— #2578

2026-10-15 のゲスト OS 無応答（#2278）では、障害前の CPU / memory / swap / I/O / load の
時系列が VM 上に残っておらず、原因を評価できなかった。diagnostics は実行時点の値だけを
返し、ゲスト OS が無応答になると SSH 依存の diagnostics も `vm-storage-monitor` も動かない。

本機構は、次に同じ事象が起きたときに**障害直前のホスト指標を再起動後も読める**ようにする
ためのもの。原因の断定（2026-10-15 の件）と自動再起動・クラウド操作権限の新設は
スコープ外（#2578）。

## 1. 契約（サイズ・頻度・保存先は固定）

| 項目 | 値 |
|---|---|
| 採取間隔 | 60 秒（`docich-host-metrics.timer`、`OnCalendar=*:*:00` / `AccuracySec=1s`） |
| 保持点数 | 最大 240 点（4 時間） |
| ファイル上限 | 256 KiB（`MAX_BYTES`）。超過時は最新 200 行へ圧縮 |
| 1 行の上限 | 4096 B（超過行は捨てる。切り詰めて書かない） |
| 保存先 | `/home/ubuntu/.local/state/docich/host-metrics/ring.jsonl`（mode 0600・dir 0700） |
| 書き込み | 1 サンプル = 1 回の `O_APPEND` 書き込み（`flock` 直列化・fsync 付き）。
電源断で末尾改行が無いときは `\n` を 1 つ補修してから追記する。上限超過時だけ temp+`os.replace` で圧縮 |

- **tmpfs に置かない。** runtime の `state_dir`（`run-soren-live/`）とも分離する。
  どちらも再起動や掃除で消え得るため、事後調査の証拠にはならない。
- 最悪時の常駐サイズは約 96 KiB（240 点 × 約 400 B）、ハード上限 256 KiB、
  書き込みは毎分 1 回。storage 監視の alert 閾値（used_percent の WARN/CRITICAL）に
  影響する規模ではない。`storage_breakdown` の固定カテゴリにも追加しない
  （カテゴリを増やすと監視側の契約変更が必要になるため。サイズの上限はこの表と
  回帰テストで固定する）。
- サンプラーは `/proc`、`/sys/kernel/random/boot_id`、固定 6 unit の
  `systemctl is-active` だけを読む。ファイル内容・環境変数・argv・path・自由文は
  記録しない。secret / prompt / 環境変数は保存しない。
- 圧縮は temp ファイル + `os.replace`、追記は単一 `write`。途中で落ちてもリングが
  壊れた状態を「正常」と読まない（読めない行は捨て、`status` で示す）。

## 2. 1 サンプルの形式（v1・1 行 1 JSON）

```json
{"boot":"a1b2c3d4e5f6","cpu":[123456,98765,4321],
 "disk_ms":[123456,78901],"disk_sectors":[1111,2222],"load":[0.42,0.5,0.6],
 "mem_kb":{"available":111,"dirty":1,"swap_free":222,"swap_total":333,
           "total":444,"writeback":0},
 "psi":{"cpu":[1,2],"io":[3,4],"mem":[5,6]},
 "t":1761000000,"units":63,"uptime_s":123456,"v":1}
```

- 1 点として有効なのは `v == 1` かつ `t` が整数（`0..now+86400`）のときだけ。
  それ以外の field はすべて nullable で、読めない source は `null` として記録し、
  点自体は証拠として残す（degraded な VM で「健全」と偽らない）。
- `t`: 採取時刻（epoch 秒）。`boot`: boot_id の先頭 12 hex。`uptime_s`: 起動からの秒。
- `cpu`: 累積 jiffies `[total, idle, iowait]`。連続 2 点の差で interval の使用率になる。
- `mem_kb`: `/proc/meminfo` の `MemTotal` / `MemAvailable` / `SwapTotal` / `SwapFree` /
  `Dirty` / `Writeback`。取れない項目は `null`。
- `psi`: `/proc/pressure/{cpu,io,memory}` の `[some avg10, full avg10]`。PSI 非対応では `null`。
- `disk_sectors` / `disk_ms`: root device の `/proc/diskstats` 累積値
  `[read, write]` / `[io_ms, weighted_ms]`。解決できなければ `null`。
- `units`: 固定 `UNIT_ORDER` の各 unit を 2 bit で表す（`00` inactive、`01` active、
  `10` failed、`11` unknown = activating/reloading/deactivating・未導入・probe 失敗）。
  `11` を「停止」と混同しない。unit 名は diagnostics 側が `unit_order` として 1 回だけ出す。

`UNIT_ORDER`（固定。設定から読まない）:

```
0 docich-corner-rotation.timer   (user)
1 docich-corner-rotation.service (user)
2 docich-webui.service           (user)
3 docich-paper-corner.timer      (user)
4 soren-runtime.service          (system)
5 docich-audio-priority.service  (system)
```

## 3. diagnostics の投影（`host_metrics`）

owner-only `diagnostics` に固定 field の投影を追加する。生ログ・path・生の行は出さない。

```json
"host_metrics": {
  "status": "ok",
  "retention": {"interval_sec": 60, "max_points": 240, "max_bytes": 262144},
  "unit_order": ["docich-corner-rotation.timer", "..."],
  "ring_bytes": 54800, "points_available": 137, "points": 8, "samples_omitted": 0,
  "oldest_age_sec": 420, "newest_age_sec": 18,
  "boots": 2, "current_boot": "a1b2c3d4e5f6", "spans_reboot": true,
  "runtime": {"service_state": "failed", "service_enabled": false,
              "timer_state": "active", "timer_enabled": true,
              "service_result": "exit-code", "service_exec_code": 1,
              "service_exec_status": 2, "service_n_restarts": 0,
              "journal": {"readable": true, "lines": 4,
                          "newest_age_sec": 30,
                          "signature": "python_traceback"}},
  "samples": [
    {"age_sec": 18, "boot": "a1b2c3d4e5f6", "t": 1761000000, "uptime_s": 123456,
     "load1": 0.42, "load5": 0.5, "load15": 0.6, "cpu_busy_pct": 12.5,
     "mem_available_kb": 111, "mem_total_kb": 444,
     "swap_free_kb": 222, "swap_total_kb": 333,
     "dirty_kb": 1, "writeback_kb": 0,
     "psi_cpu_some10": 1.0, "psi_cpu_full10": 0.0,
     "psi_io_some10": 3.0, "psi_io_full10": 1.0,
     "psi_mem_some10": 5.0, "psi_mem_full10": 2.0,
     "disk_read_sectors": 2048, "disk_write_sectors": 4096, "disk_io_ms": 30,
     "units": 63, "down_units": ["docich-paper-corner.timer"]}
  ]
}
```

- `status`: `ok` / `absent`（ファイル無し）/ `unreadable`（権限・IO・通常ファイルでない・
  symlink）/ `invalid`（v1 レコードが 1 つも無い、またはファイルが上限 256 KiB を超えて
  いて圧縮が効いていない）/ `stale`（最新点が 3 interval より古い）。サンプラーが
  止まっている状態を「健全」と偽らない。
- `ring_bytes`: 実際に読んだリングファイルのバイト数（`absent`/`unreadable`/`invalid`
  では 0）。保存サイズが上限内であることを本番でも 1 field で確認できる。
- `points` は投影した点数（最大 8 + boot 境界で最大 10）、`points_available` は
  保持している点数（最大 240）、`samples_omitted` は hard ceiling 対応で落とした点数。
- `down_units` は**inactive または failed** の固定 unit 名だけ。`unknown`（起動直後の
  activating・未導入・probe 失敗）は含めない。
- `spans_reboot`: 保持点に複数の `boot` が含まれるとき true。受入条件
  「再起動後に直近の指標が取得できる」はこの field で確認する。
- `uptime_s`: その点の起動からの秒。最新点の `t` / `uptime_s` から、無応答が始まった
  時刻と「起床後どれだけ経ってから止まったか」を推定できる。
- `runtime`: sampler unit 自身の健康。`service_state` / `timer_state`
  （固定 enum）、`service_enabled` / `timer_enabled`、`service_result`
  （`systemctl --user show` の `Result`。`oom` なら MemoryMax 起動）、
  `service_exec_code` / `service_exec_status` / `service_n_restarts`、
  そして `journal`（bounded な末尾を固定分類したもの: `readable` / `lines` /
  `newest_age_sec` / `signature`）。**journal 本文・path は一切出さない。**
  sampler が止まっている・OOM している・例外で落ちている場合、
  ring が `absent` でもこの field で原因が分かる（deploy hook の失敗理由が
  VM 側 private log にしか残らないため、これが正規の読み戻し経路）。
- `cpu_busy_pct` と `disk_*` は、**同一 boot の連続 2 点**の差から求める。boot をまたぐ
  最初の点は `null`（起動直後の累積値はリセットされるため差分に意味がない）。
  分母が 0 の interval は `null`、disk の逆転（カウンタリセット）は 0 に clamp する。
- 出力は**最新 8 点 + 直前 boot の最終点（最大 2 boot 分）**。再起動後に sampler が
  窓を埋めると「最新 N 点」だけでは障害直前の点が消えるため、boot 境界の最終点を
  必ず残す（合計 10 点まで、8 KiB 未満）。
- 縮小は **hard ceiling（56 KiB）に触れた時だけ**、古い点から 1 点ずつ。最新点と
  boot 境界点（最大 2 boot 分）は絶対に残す。床（最新点 + 境界点）自体が
  ceiling に収まらない時は縮小を skip する（どうせ既存 section が 1 個落ちるため、
  境界を焼いてまで縮めない）。soft target（36 KiB）では縮めない。production の envelope は
  もともと soft target を超えており（#2533 実測 50,280 B）、soft で縮めても目標に
  届かないうえ、既存 section（`corners` は約 23 KB）を小さな超過で落としかねないため。
  hard ceiling でも縮小で収まらない場合に限り `_HARD_DROP_ORDER` の最後に落とす
  （#2578 の事後調査証拠を他の detail より優先して残す）。
- 既存キーの形・意味は変えない。`host_metrics` は追加キーであり、payload に
  `host_metrics` が無いときの collector 出力は従来と同一（回帰テストで固定）。

## 4. 導入手順（canonical deploy 経由）

1. `main` に merge する。
2. `VM operations` の deploy が `ops/vm_actions/ensure_host_metrics_timer.sh` を
   deploy hook として実行し、`~/.config/systemd/user/` へ
   `docich-host-metrics.service` / `.timer` を配置して `enable --now` する。
   hook は同じ deploy 内で 1 サンプルを実行し、リングファイルの存在・mode 0600・
   親 dir 0700 を検証する（失敗したら deploy を失敗させる。黙って取れない状態を残さない）。
   unit の配置先が runtime user の home 配下であることも検証する。
   workflow 側は hook を `bash -n` してから渡す。
   hook が検証するのは「1 サンプルが書けること」であり、毎分の cadence ではない。
   cadence は deploy 後の `diagnostics.host_metrics.newest_age_sec` の増加で確認する。
3. 手動の `systemctl` 導入・SSH でのコピーは正規経路にしない。

`systemd` の user unit を使うのは、既存の corner rotation / retention timer と同じ
権限境界（ubuntu・昇格なし）に揃えるため。ゲスト OS 全体が無応答の間はどのサンプラーも
書かない（残るのは無応答直前の最後の点まで）。systemd user manager だけが落ちた場合は
`stale` として見える。

## 5. 復旧後の確認（実機）

`docs/operations/vm-guest-unresponsive-recovery.md` の再起動後チェックリストで、
`diagnostics` の `host_metrics` を確認する。

- 平常時: `status=ok`、`points` が 8（boot 境界があれば 9〜10）、`samples_omitted=0`、
  `newest_age_sec` が約 0〜60。`stale` は sampler 停止の信号で、`ok` と混同しない。
- 再起動後: `spans_reboot=true`、`boots>=2`、`current_boot` が再起動後の boot。
  `newest_age_sec` が小さい = 再起動後も採取が再開している。
- 障害時: 最新点の `t` / `age_sec` と `uptime_s` から「無応答が始まった時刻」を
  推定できる。`down_units` と PSI / `cpu_busy_pct` / `dirty_kb` / `writeback_kb` の
  推移が、その直前のホスト状態を示す。

**実機確認のために本番 VM を再起動しない。** `spans_reboot` は次に自然な再起動が
起きた時に確認する（それまでの受入は、リングが 240 点まで積み上がること・圧縮が
設計どおり動くこと・`status=ok` の 3 点で確認する）。

## 6. 戻し方

`systemctl --user disable --now docich-host-metrics.timer`。リングファイルは
`rm -rf ~/.local/state/docich/host-metrics` で消える（運用状態であって設定ではない）。
deploy hook は timer を再度有効化するため、恒久的に止める場合は hook と workflow step を
reviewed PR で外す。

## 7. 実装側の一次契約

コードとテストが参照する一次契約（record format・nullable 規則・投影 field・bound 値）は
`ops/vm_actions/host_metrics_contract.md`。本ドキュメントと矛盾したら、record 形式と
bound 値は契約ファイルを正とし、運用手順は本ドキュメントを正とする（両方を同じ PR で
更新する）。

## 8. テスト

- `ops/vm_actions/tests/test_host_metrics.py`: サンプル形式・固定 keys・null
  fallback・リング上限（点数 / バイト数 / 行長）・圧縮・mode・symlink 拒否・
  boot またぎ。
- `ops/vm_actions/tests/test_host_metrics_timer.py`: unit / hook / workflow の固定契約。
- `ops/vm_actions/tests/test_host_metrics_diagnostics.py`: 投影・status・再起動またぎ・
  delta 計算・budget（soft/hard）・既存キー不変。
- CPU profile / headroom と同様、collector timeout（60 秒）内に収まることは
  read 上限 256 KiB + 8 点投影 + 既存 headroom 回帰で確認する（追加の実測待ちはしない）。
