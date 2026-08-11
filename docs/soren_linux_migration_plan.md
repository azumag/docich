# Soren Linux 移行・FFmpeg 直接配信計画

> 更新: 2026-08-12
> 対象: Oracle A1 / Ubuntu 24.04 ARM64 / Xvfb
> 実装: `azumag/soviet_now` Issue #96、PR #95・#97

## 1. 目的

macOS で稼働していた Unity WebGL 自動プレイと配信処理を Linux へ移し、Oracle A1 で 720p30 の 24 時間配信を安定させる。

OBS 版の移植はフォールバックとして維持する。CPU 使用量と X11 キャプチャの不安定さを減らす本命は、FFmpeg が映像・音声・オーバーレイを直接合成し、ローカル RTMP relay へ送る構成とする。

## 2. リポジトリと変更管理

- 実装本体: [`azumag/soviet_now`](https://github.com/azumag/soviet_now)
- 設計文書: [`azumag/docich`](https://github.com/azumag/docich)
- Linux OBS portability: [PR #95](https://github.com/azumag/soviet_now/pull/95)
- FFmpeg direct backend: [Draft PR #97](https://github.com/azumag/soviet_now/pull/97)

#97 は #95 に積んだ PR である。#95 のマージ後に #97 の base を `main` へ変更または rebase する。Issue #96 は実装 PR の作成だけでは閉じず、実配信の受入条件をすべて満たしてから閉じる。

## 3. Oracle A1 の運用モード

| モード | 構成 | 費用上の位置づけ | 測定結果 |
|---|---:|---|---|
| free-safe | 2 OCPU / 12 GB | free-only テナンシの A1 合計上限 | 現行 720p30 条件では不合格 |
| trial / PAYG | 4 OCPU / 24 GB | トライアル credit または Always Free 超過分の課金対象 | 短時間試験は合格 |

Oracle 公式文書上、free-only テナンシの A1 は合計 2 OCPU / 12 GB である。4 OCPU / 24 GB を常時稼働しても全量無料になるわけではない。トライアル終了前に、次のどちらかを選ぶ。

1. PAYG に移行し、予算アラートと利用量監視を設定して 4/24 を継続する。
2. 合計 2/12 以下へ縮退し、画質・ゲーム描画・音声負荷を再設計する。

トライアル終了時に A1 が free-only 上限を超えていると、既存 A1 が無効化され、アップグレードしなければ 30 日後に削除される可能性がある。詳細は [Oracle Free Tier](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm) と [Always Free Resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm) を参照する。

## 4. 最終アーキテクチャ

```text
Xvfb :99 (1280x720)
  └─ Chrome / Unity game
       └─ FFmpeg x11grab 1280x720@30
            ├─ status/event overlays
            ├─ H.264 video
            └─ AAC audio <- PulseAudio soren_null.monitor
                              ├─ game audio
                              ├─ BGM
                              ├─ SE
                              └─ TTS
                 │
                 v
          local nginx RTMP relay
                 │
                 v
      root-only external push config
                 │
                 v
               Twitch
```

設計上の境界:

- FFmpeg publisher は外部ストリームキーを持たず、localhost の relay だけを見る。
- 外部 push target は `/etc/soren-rtmp/push.conf` に root-only で置く。
- `.env`、Git、PR、テストログ、チャットに認証情報を保存しない。
- OBS は削除せず、失敗時の既定 rollback backend とする。
- `start_all.sh` と systemd runtime は backend 選択を共有する。

## 5. OBS portability の到達点

PR #95 で macOS `screen_capture` を保ったまま Linux 分岐を追加した。

- XComposite source id: `xcomposite_input`
- XComposite setting key: `capture_window`
- item value: `<decimal XID>\r\n<title>\r\n<WM_CLASS>`
- XSHM full-screen source は別 family として扱う。
- Linux のログ、process 名、再起動、GNU `stat` を分岐した。
- macOS の ScreenCaptureKit 設定と bounce は変更しない。

OBS 30.0.2 の XComposite は同一設定の update を繰り返すと watcher 登録が重複し得る。そのため Linux では、現在値と一致する `SetInputSettings` を送らず、同値 freeze bounce は既定無効にする。ユーザー要望により、90 秒ごとの最小化自動復元も採用しない。

XSHM は 1280x720 の Xvfb 全画面を確実に取得できる一方、OBS UI やブラウザ chrome が前面にあると写り込む。直接配信では OBS UI を経路から外し、ゲーム画面配置を固定する。

## 6. FFmpeg direct backend の実装範囲

PR #97 には次を含む。

- `direct_stream.sh` / `lib/direct_stream.py`: FFmpeg publisher と状態管理
- `lib/direct_overlay.mjs`: status・event overlay 合成
- `install_direct_stream_relay.sh`: nginx RTMP relay の導入と検査
- `cutover_direct_stream.sh`: preflight、cutover、rollback
- `install_soren_runtime_service.sh`: systemd runtime 導入
- `stream_backend_condition.sh`: OBS / FFmpeg backend 条件
- `lib/direct_av_sync.py`: A/V sync 測定
- `lib/direct_benchmark.py`: OBS と direct の比較判定
- `lib/direct_soak.py`: 1 時間・24 時間の監視と集計
- `direct_stream_recovery_test.sh`: relay・publisher 復旧試験

安全策:

- preflight は OBS や `.env` を変更しない。
- cutover は relay の構文、root-only push 設定、reload、active 状態を確認してから OBS を止める。
- relay reload 失敗時は OBS と `.env` を変更しない。
- 既存 `tmp/stop` があれば runtime installer は変更前に停止する。
- rollback は OBS backend と従来設定を復元する。

## 7. 音声経路

PulseAudio null-sink `soren_null` を配信音声バスとする。

| 音源 | Linux 再生 | 配信への入力 |
|---|---|---|
| ゲーム | Chrome / PulseAudio | `soren_null.monitor` |
| BGM | `ffplay` loop | `soren_null.monitor` |
| SE | `paplay` | `soren_null.monitor` |
| TTS | VOICEVOX -> `say_enqueue.sh` -> `paplay` | `soren_null.monitor` |

BGM は正しいアセットを明示選択し、SE はゲームイベントと同じ時刻基準で発火させる。FFmpeg 側で映像と audio clock の差を記録し、目視だけでなく `direct_av_sync_test.sh` でも判定する。

PulseAudio daemon が複数起動すると、OBS/FFmpeg と再生プロセスが別 socket を掴んで無音になる。runtime service では `XDG_RUNTIME_DIR` と `PULSE_SERVER` を固定し、単一の `soren_null.monitor` を確認してから publisher を起動する。

## 8. 実測結果

### 4 OCPU / 24 GB

| 指標 | 結果 |
|---|---:|
| FFmpeg output | 29.97 fps / speed 0.999x |
| game render | 29.900 fps |
| 900-frame sample | drop 0 / duplicate 2 |
| stream-process CPU reduction vs OBS | 69.146% |
| short acceptance | pass |

### 2 OCPU / 12 GB

| 指標 | 結果 |
|---|---:|
| FFmpeg output | 29.81 fps / speed 0.995x |
| game render | 25.353 fps |
| 899-frame sample | drop 33 / duplicate 35 |
| system CPU busy | 99.45% |
| short acceptance | fail |

2/12 でも encoder 自体は約 30 fps を維持したが、ゲーム描画が 30 fps を満たさず、drop/duplicate も許容外だった。現行 workload のまま free-safe を本番採用しない。

## 9. 段階的 cutover

### 9.1 コードと relay の準備

```bash
./install_direct_stream_relay.sh --print-config
./install_direct_stream_relay.sh --install --confirm-package-install
./install_direct_stream_relay.sh --status
./cutover_direct_stream.sh --print-plan
./cutover_direct_stream.sh --preflight
```

外部 push 設定は VM 管理者が root 権限で作成する。値を shell history、Codex、GitHub、ログへ出さない。

### 9.2 短時間 canary

```bash
./benchmark_direct_stream.sh --confirm-live-interruption --duration 30
./cutover_direct_stream.sh --cutover --confirm-live-cutover
./direct_stream_status.sh
```

Twitch 側で映像・音声・統計を確認し、異常があれば直ちに rollback する。

```bash
./cutover_direct_stream.sh --rollback --confirm-live-rollback
```

### 9.3 1 時間と 24 時間

```bash
./direct_stream_soak.sh start --duration 3600 --interval 60
./direct_stream_soak.sh status
./direct_stream_soak.sh summary --expected-duration 3600

./direct_stream_soak.sh start --duration 86400 --interval 60
./direct_stream_soak.sh summary --expected-duration 86400
```

1 時間が不合格なら 24 時間へ進まない。

## 10. Issue #96 受入条件

| 項目 | 条件 | 状態 |
|---|---|---|
| output | 1280x720、30 fps、Twitch 対応 H.264/AAC | 実 ingest 未確認 |
| pacing | 長時間で speed 約 1.0、drop/duplicate が許容内 | 短時間のみ合格 |
| game | 体感だけでなく測定値も 30 fps 近傍 | 4/24 短時間合格 |
| audio | game/BGM/SE/TTS の全経路 | OBS では確認、direct live 未確認 |
| sync | SE とゲームイベント、TTS と表示が許容差内 | direct live 未確認 |
| overlay | status/event/dashboard の必要情報が同等 | live 未確認 |
| reconnect | relay/publisher 障害から自動復旧 | 合成試験あり、live 未確認 |
| canary | 1 時間連続 | 未実施 |
| soak | 24 時間連続 | 未実施 |
| rollback | OBS 配信へ 60 秒以内に復帰 | live 未実施 |
| secrets | Git・ログ・PR に秘密情報なし | 実装済み |

## 11. 中止条件

次のいずれかが起きたら direct を止め、OBS に rollback する。

- Twitch が映像または音声を受信しない。
- speed が継続して 1.0 を下回り、遅延が累積する。
- ゲーム描画が 30 fps を大きく下回る。
- BGM/SE/TTS の無音、途切れ、重複、明確な同期ずれがある。
- relay reload/reconnect が失敗する。
- CPU、メモリ、disk、network の閾値超過が継続する。
- 60 秒以内に OBS へ戻せない。

## 12. 完了後の整理

1. #95 をマージする。
2. #97 を `main` に積み替えてレビュー・CI を通す。
3. 1h/24h の実測 artifact と結論を Issue #96 に残す。
4. 受入条件をすべて満たした時だけ #97 を ready 化し、Issue #96 を閉じる。
5. PR の積み替えとマージ後に、一時 worktree `/Users/azumag/work/docich/soren-phase1` を Git worktree の手順で安全に削除する。
