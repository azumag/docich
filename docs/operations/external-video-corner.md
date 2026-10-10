# 手動OBS特別コーナー

`external-video-corner` はownerが明示的に起動する受信・表示経路です。
自動rotation候補に追加せず、soren91のsender/agent/CDP/19192を使用しません。
Windowsのゲーム実行・入力・自動プレイには関与しません。

## 準備と受信確認

VMの配備済みmainで実行します。秘密情報や任意URLは引数に渡しません。

```sh
bin/docich --config config/docich.soren-live.toml external-video-corner prepare --listen-ip <VMのTailscale IPv4> --wait-minutes 30
bin/docich --config config/docich.soren-live.toml external-video-corner status
```

受信はTailscale IPv4のUDP19194だけにbindし、単一receiverが所有します。
OBSは `srt://<同IP>:19194?mode=caller&latency=500000`、stream keyは空欄、
H.264/AAC、ゲームウィンドウとゲーム音声のみを送信してください。
準備中はprivate previewと音声RMSだけを保存し、配信画面・共有音声へ出しません。
`receiver.fresh=true`、`audio_present=true`を確認し、異時刻のpreviewを目視して
意図したゲーム・四辺・操作案内が写ることを確認します。
音声無しの起動は通常拒否します。ownerが明示した場合だけ`--video-only`を使用します。

新receiverが既に生存中ならprepareは拒否します。テスト用受信プロセスが19194を
所有している場合は、その所有を確認して当該プロセスだけを終了してからprepareします。
リモートOBSは再接続が必要になる場合があります。

## 表示と終了

```sh
bin/docich --config config/docich.soren-live.toml external-video-corner start --duration-minutes 15
```

startはforeground runnerです。ownerの既存SSHセッション、または同じ配備済み
launcherを実行するuser transient unitで動かせます。unitを使う場合は復帰予算を
含むRuntimeMaxSecとKillMode=control-groupを設定し、最終報告にunit名/PIDを残します。
runnerはprogram_slotの予測境界とFIFOを守り、GameSwitchが旧ゲームの試合終了・
保存・資源解放を担当します。既存cornerのfailed/active、canonicalの未完了切替、
`run-soren-live/corners/external-video.paused`は開始を拒否します。
ユーザーの休止を解除しません。

receiverは同じSRT接続からloopback UDP19195へ映像・音声を中継します。
generation/lease/request所有のpresenterだけがこのsourceを表示し、元解像度から
containで `(0,90,960,540)` に収め、黒余白を付けます。映像の切り抜き・引き伸ばし、
encoder/DISPLAY/PulseAudioの再起動は行いません。ゲーム音声はviewerから既存の
`soren_null`へ接続します。

```sh
bin/docich --config config/docich.soren-live.toml external-video-corner stop
bin/docich --config config/docich.soren-live.toml external-video-corner status
```

stopは現在のstart requestに紐づく停止要求です。切替の試合境界待ちを強制終了せず、
runnerが受理済みの切替を収束させてから復帰します。表示期間は1-30分、既定15分。
receiverの寿命内に切り詰めます。受信切断、最終decoded frameの10秒超過、期限到達、
手動stop、TERM/INTで、記録したprevious gameへ正規switch/stopで復帰します。
同一pixel hashだけで切断を判定しないため、ゲームが静止していても接続は維持できます。

復帰の成功receiptとcanonical runtimeが一致し、retiring/candidate/previousが残らない
ことを確認してcompletedにします。復帰失敗はfailed/recovery_requiredとして所有を残し、
別ゲーム・他ownerを上書きしません。

```sh
bin/docich --config config/docich.soren-live.toml external-video-corner recover
```

recoverは当該cornerのstart/restore requestだけを再照合します。既存の別cornerが
canonicalを失敗させている場合は拒否し、そのownerの正規復旧を先に実施します。
表示していないprepared receiverだけを止める場合は`receiver-stop`を使います。
receiverはwait-minutes（1-60分）のdeadlineとsystemdの安全上限を持ち、終了時に
自分のffmpeg process groupだけを回収します。

## 診断・配備・受入れ

- registry: `ops/vm_actions/runtime_registry.py` の `EXTERNAL_VIDEO`。
- owner-only diagnostics: `corners.external_video` の固定status、receiver_alive、frame_fresh、
  audio_present、corner_status、recovery_required。IP/UUID/PID/argv/log/本文は公開しません。
- AI worker・AI queue lane・providerは追加しません。常駐workerの不在を障害扱いしません。
- main/PRを迂回するVMソース編集をしません。専用receiverとrunnerは新規起動時に配備済み
  コードを読み、共通workerの再起動を必要としません。
- tests/test_external_video.pyで競合、pause、期限、切断、復帰失敗、foreign owner、
  Tailscale bind、receiver identity、実ffmpeg video/audio処理、診断redactionを確認します。
- 実OBS受信、表示後の異時刻配信frame・四辺、音声、共有PID維持、stop/期限/切断からの
  復帰を別々に実測します。CIやdeployだけで受入れ完了にしません。

A failed rollback with one failed Soren restore candidate can be recovered
without stopping the live previous singleton only when the terminal failure
receipt, adjacent restore generation, idle broker including its control
marker, and two stable fixed-root process identities are all proven. The
coordinator re-leases the existing previous runtime after readiness; ordinary
stable-owner retirement proof then clears the obsolete attempt. It never
starts a new process across unproven resources or edits canonical state by hand.
