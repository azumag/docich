# Soren Linux 配信 引き継ぎ

> 更新: 2026-08-12
> 文書リポジトリ: `/Users/azumag/work/docich`
> 実装リポジトリ: `azumag/soviet_now`

## 現在の目標

Issue #96 の FFmpeg 直接配信経路を Oracle A1 上で実配信まで検証し、720p30、映像・音声・オーバーレイ、再接続、24 時間継続、60 秒以内の OBS 復帰をすべて満たす。

現時点では実装と短時間ベンチマークまで完了している。外部 Twitch への切替と 1 時間・24 時間試験は未完了なので、Issue #96 は閉じない。

## リポジトリの役割

- `azumag/soviet_now`: Soren の実装本体。
- `azumag/docich`: Oracle 構成判断、移行計画、運用引き継ぎ文書のみ。
- `/Users/azumag/work/docich/soren-phase1`: `soviet_now` の一時 Git worktree。`docich` には追加しない。PR の積み替え・マージが完了するまで手動で移動・削除しない。

関連 PR:

- [soviet_now #95](https://github.com/azumag/soviet_now/pull/95): Linux OBS portability Phase 1。2026-08-12 に merge commit `8170da044` で `main` へマージ済み。
- [soviet_now #97](https://github.com/azumag/soviet_now/pull/97): FFmpeg 直接配信バックエンド。`main` を base にした Draft PR。

#97 は #95 マージ後に `main` へ積み替え済みで、3 コミット差分・mergeable の状態。

## 実装済みの直接配信経路

```text
Xvfb :99 / Chrome game
  -> FFmpeg x11grab 1280x720@30
  -> status/event overlay composition
  -> H.264 + AAC
  -> local nginx RTMP relay
  -> root-only push configuration
  -> Twitch

PulseAudio soren_null
  <- game / BGM / SE / TTS
```

- `start_all.sh` から OBS / FFmpeg バックエンドを選択できる。
- 外部 RTMP 宛先は `/etc/soren-rtmp/push.conf` に隔離し、リポジトリや `.env` に保存しない。
- cutover は relay の構文・資格情報・reload 成功を確認してから OBS と `.env` を変更する。
- rollback は OBS を既定経路へ戻す。
- 意図的停止を示す既存 `tmp/stop` がある場合、runtime installer は変更前に失敗する。

## 実機ベンチマーク

### 4 OCPU / 24 GB

- FFmpeg: 29.97 fps、speed 0.999x
- ゲーム描画: 29.900 fps
- 900 フレーム: drop 0、duplicate 2
- OBS 比の配信プロセス CPU 削減: 69.146%
- 短時間受入: 合格

### 2 OCPU / 12 GB

- FFmpeg: 29.81 fps、speed 0.995x
- ゲーム描画: 25.353 fps
- 899 フレーム: drop 33、duplicate 35
- system CPU busy: 99.45%
- 短時間受入: 不合格

VM は比較後に 4 OCPU / 24 GB へ戻した。これは無料トライアル中または PAYG 前提の構成で、トライアル終了後の free-only 上限ではない。free-only を継続するなら、終了前に合計 2 OCPU / 12 GB 以下へ縮退する。

## 現在の運用状態

- 最終確認時の VM: Oracle A1、Ubuntu 24.04 ARM64、4 OCPU / 24 GB。
- VM 上のコード: `/home/ubuntu/soren`。
- OBS 経路はフォールバックとして維持している。
- FFmpeg の外部 Twitch cutover は、root-only push 設定が未配置のため実施していない。
- ストリームキー、OAuth token、秘密鍵、push target はこの文書に書かない。

## 次の実行順序

1. #97 のレビューを通す。実配信ゲートが残るため Draft のまま維持する。
2. VM 管理者が `/etc/soren-rtmp/push.conf` を root 権限で設定する。値はチャットやログへ貼らない。
3. `./install_direct_stream_relay.sh --status` と `./cutover_direct_stream.sh --preflight` を実行する。
4. 計画を確認して `./cutover_direct_stream.sh --cutover --confirm-live-cutover` を実行する。
5. Twitch 側で 720p30、codec、bitrate、ゲーム画面、BGM、SE、TTS、同期、オーバーレイを確認する。
6. `./direct_stream_soak.sh start --duration 3600 --interval 60` で 1 時間試験を行い、合格後に 86400 秒へ進む。
7. 異常時は `./cutover_direct_stream.sh --rollback --confirm-live-rollback` で OBS へ戻し、60 秒以内の復旧を測る。

## Issue #96 の未完了ゲート

- 実 Twitch ingest と codec / bitrate の合格
- ゲーム、BGM、SE、TTS の全音声経路
- A/V 同期とオーバーレイの同等性
- relay 切断・再接続
- 1 時間 canary と 24 時間 soak
- OBS への 60 秒以内 rollback
- トライアル終了前の 4/24 継続方針: PAYG 化または 2/12 縮退

## 参照

- [Issue #96](https://github.com/azumag/soviet_now/issues/96)
- [Oracle Free Tier](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm)
- [Oracle Always Free Resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- `docs/oracle_arm_setup_guide.md`
- `docs/soren_linux_migration_plan.md`
