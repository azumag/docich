# 配信音声経路の優先度維持 (docich#1811)

本番VM(4コア)はCPU圧力(PSI)が約70%に達し、ゲーム音を出すRetroArchのメインスレッドが実行時間の2倍以上CPU待ちになって、
PulseAudioへ届く音に数十〜百数十msの無音欠落(ぷつぷつ)が入っていた。エンコーダ入力(`soren_null.monitor`)を録音して
測った(変更前 92秒に7回、RetroArchのnice -10後は 8→0→3回で欠落も短い)。

`priority.py` は5秒ごとに次のプロセスの全スレッドを nice -10 に保つ。優先度を上げる方向にだけ作用し、所有者・comm・引数が一致したものだけを触る。

| 分類 | 条件 |
|---|---|
| retroarch | comm=`retroarch` |
| pulseaudio | comm=`pulseaudio` |
| encoder-ffmpeg | comm=`ffmpeg` かつ引数に `x11grab` と `flv`(短命のPNGキャプチャは対象外) |
| twica-feeder | python で `-m docich.twica_encoder` / `-m docich.twica_ffmpeg` |

コーナー切替でRetroArchが作り直されても、次の周期で追随する(それまでの数秒はnice 0)。

## 権限
`User=ubuntu` に `CAP_SYS_NICE` だけを付ける(rootでは動かさない)。スクリプトはrootが所有し、ubuntuが書き換えられない場所に置く。

## インストール(root)
```bash
install -d -m 0755 -o root -g root /usr/local/libexec/azumag-vm-ops /usr/local/libexec/azumag-vm-ops/audio_priority
install -m 0755 -o root -g root ops/audio_priority/priority.py /usr/local/libexec/azumag-vm-ops/audio_priority/priority.py
install -m 0644 -o root -g root ops/audio_priority/docich-audio-priority.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now docich-audio-priority.service
python3 /usr/local/libexec/azumag-vm-ops/audio_priority/priority.py --uid 1001 --once --dry-run   # 何をするかの確認
```

## 戻し方
`systemctl disable --now docich-audio-priority.service`。既に上がったnice値は対象プロセスの再起動で元に戻る(即時に戻すなら `renice -n 0 -p <tid>`)。

## 確認
- 稼働: `journalctl -u docich-audio-priority -f` に `reniced pid=... kind=...` が(変更があるときだけ)出る。
- 効果: `parec -d soren_null.monitor --rate=48000 --channels=2 --format=s16le --raw` を録り、前後が鳴っている無音(2ms〜300ms)を数える。
  PulseAudioの `q overrun` ログ件数は間引かれ、音の欠落とも限らないので指標にしない。
