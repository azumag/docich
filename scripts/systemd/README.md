# docich systemd ユニット (Phase 3)

tmux 常駐 (architecture.md §2) を主に systemd --user ユニットで包むためのテンプレート。
このディレクトリの通常ユニットファイルは **雛形**であり、`__DOCICH_ROOT__` を実際の
リポジトリ絶対パスに置換してから `~/.config/systemd/user/` に配置して使う。
リポジトリ内のユニットファイル自体は編集しないこと (置換はコピー先で行う)。

| ファイル | 役割 |
|---|---|
| `docich.service` | `docich up` / `docich down` を包む oneshot ユニット (`RemainAfterExit=yes`) |
| `docich-rotate.service` | `docich rotate` を1回実行する oneshot ユニット (`[Install]` なし。timer 専用) |
| `docich-rotate.timer` | `docich-rotate.service` を毎時起動する timer ([rotation] 利用時のみ) |
| `docich-corner-rotation.service` | Soren本番 `:99` に全corner rolling rotationを載せる長時間oneshot (canonical) |
| `docich-corner-rotation.timer` | 毎分 `corner-rotation tick`。全catalogを実効N件の24時間rollingで判定 |
| `docich-retro-corner.service` | canonical serviceの互換名。移行前はregular template、移行後はrelative alias |
| `docich-retro-corner.timer` | canonical timerの互換名。移行後はcanonicalへのrelative alias |
| `docich-game-switch-fifo.service` | 期限切れdrainingを安全に復旧し、保存済みFIFO先頭を再駆動するoneshot |
| `docich-game-switch-fifo.timer` | ゲーム切替FIFOを30秒ごとに独立監視するtimer |
| `docich-soren91-corner.service` | Soren本番 `:99` にSoren91定時コーナーを載せる長時間oneshot |
| `docich-soren91-corner.timer` | 毎分 `soren91-corner tick`。設定timezone/start_hour(/weekdays)に一致した時だけ1日1回実行 |
| `docich-webui.service` | `docich webui` を常駐させる simple ユニット (Tailscale serve で公開する場合のみ利用) |

`docich-free-strategy-worker.service` だけはDocker socketを使うためsystem scopeのunitです。
`User=ubuntu` と `SupplementaryGroups=docker` をunit内で指定し、ubuntuアカウントへdocker groupを
恒久追加せず新workerプロセスにだけ権限を渡します。既存PAPERのuser unitやSoren runtimeは操作しません。
導入とenableは `docs/operations/free-strategy-paper-worker.md` の手順に従ってください。

## 導入手順

```sh
cd /path/to/docich   # リポジトリのルートへ移動 (git clone 先など)
mkdir -p ~/.config/systemd/user
DOCICH_ROOT="$(pwd)"

for f in \
  docich.service \
  docich-rotate.service \
  docich-rotate.timer \
  docich-corner-rotation.service \
  docich-corner-rotation.timer \
  docich-retro-corner.service \
  docich-retro-corner.timer \
  docich-game-switch-fifo.service \
  docich-game-switch-fifo.timer \
  docich-soren91-corner.service \
  docich-soren91-corner.timer
do
  sed "s|__DOCICH_ROOT__|${DOCICH_ROOT}|g" "scripts/systemd/${f}" \
    > ~/.config/systemd/user/"${f}"
done

systemctl --user daemon-reload

# 基盤 (display/audio/stream) を起動する。必要な場合だけ明示enableする。
systemctl --user enable --now docich.service

# 旧 [rotation].games の互換ローテーションを使う場合のみ。
systemctl --user enable --now docich-rotate.timer

# 現行の全corner共通rotationを使う場合。PAPER/メリケンもこのtimerで判定する。
# 本番設定は config/docich.soren-live.toml の [corner_rotation] を読む。
# protected mainからのproduction deploy時にも同じreview済みunitを再配置してenableする。
# 移行期間中は docich-retro-corner.timer を独立にenableしない
# (移行後はcanonical timerへのaliasとして解決される)。
systemctl --user enable --now docich-corner-rotation.timer

# ゲーム切替の呼び出し元が停止しても、期限切れdrainingとFIFOを復旧する。
# 期限前の試合終了待ちは変更せず、共通配信基盤も再起動しない。
systemctl --user enable --now docich-game-switch-fifo.timer

# 旧Soren91固定時刻入口を個別に使う場合。共通rotationでは新規導入不要。
# 有効化しても同じcorner-rotation state/lockへ委譲される。
systemctl --user enable --now docich-soren91-corner.timer
```

`docich-rotate.service` と `docich-corner-rotation.service` は `[Install]` を持たないため
直接 `enable` しない。timer が起動するoneshotである。
`docich-game-switch-fifo.service` も同様に直接 `enable` せず、専用timerだけを有効化する。

## 全corner共通 rolling rotation

本番では `config/docich.soren-live.toml` を必ず使う。このprofileは
`display=:99`, `managed=false`, `stream.mode="null"`, `audio.enabled=false` で、
**SorenのXvfb・音声bus・FFmpegを所有しない**。

毎分のtimerで `corner-rotation tick` を実行し、`[corner_rotation].corners` のうち
実行可能で休止していないN件を同列に扱います。目標間隔は24時間/Nで、直近24時間の
使用履歴を除外した決定論的なseed順位から選びます。固定時刻のレトロ/PAPER/メリケン枠は
共通rotationにはありません。詳細な永続化・移行・復旧契約は
[corner-rotation.md](../../docs/corner-rotation.md) を正本とします。

PAPERは固定時間を持たず、事実に基づく読み上げネタを1件ずつ生成して生成できた順に
読み上げ、ネタが尽きたら読み上げキューが捌けるのを待ってから表示を戻します
（AI失敗時は有限の決定論フォールバックを読み切り、失敗として記録します）。
いずれも終了境界・改善サイクル・予想APIの確定を確認できない場合は次のcornerを開始しません。

両枠はSoren rootの共通lockで直列化します。開始が遅れても短縮せず、レトロ枠は
ゲーム切替完了後、PAPER枠は詳細表示へ切替後から読み上げを始めます。境界が来なければ
待機を継続し、暗黙のタイムアウト開始は行いません。待機中・実行中の再起動にも状態を保存し、
他枠が未復旧のactive状態なら開始を拒否します。PAPER枠は現在の模擬集計を伝え、
終了後compactへ戻ります。通常ゲーム中も公開データのPAPER workerは継続します。

本番導入では `.venv-trading` に `requirements-trading.txt` をインストールし、
`docich-paper-runtime.service` と `docich-corner-rotation.service/timer` を配置します。
共通rotationでは `docich-corner-rotation.timer` をenableすれば全cornerを判定します
（移行期間中は旧 `docich-retro-corner.timer` が同unitへのalias）。
旧PAPER/Soren91 timerを併用しても同じstate/lockで重複実行は防止されますが、新規導入は不要です。
過去通知を避けるため、
workerの初回起動より先にlive profileの `trading notify-once` を実行します。
実注文・private API・鍵は使用しません。資金は模擬1万円、投入上限30%です。

状態確認:

```sh
bin/docich --config config/docich.soren-live.toml corner-rotation status
bin/docich --config config/docich.soren-live.toml retro-corner status --json
bin/docich --config config/docich.soren-live.toml paper-corner status
bin/docich --config config/docich.soren-live.toml soren91-corner status --json
bin/docich --config config/docich.soren-live.toml trading status
```

### 旧unit名からの移行（review済み deploy hook）

- 2026-09-22時点のproductionは移行済み（epoch追加PR #919、main `f941164f`）。
  deploy hookはcanonical unitの再配置とcanonical timerのenableを維持し、
  旧名はcanonicalへのaliasとして解決される。
- canonical unitは `docich-corner-rotation.service/timer`。移行後は旧
  `docich-retro-corner.service/timer` がcanonicalへのrelative aliasになり、
  旧名を独立timerとして二重enableしない。
- 移行はdeploy hook `ops/vm_actions/ensure_corner_rotation_timer.sh` が
  `ops/vm_actions/corner_rotation_timer_migration_epoch` の存在時だけ
  `ops/vm_actions/migrate_corner_rotation_timer.sh` を実行する。
- migrationは旧timerのstop/disable後でなければ切り替えず、旧serviceがactiveなら
  killせず中断する。旧unitがreview済み内容と一致しない場合は上書きしない。
- rollbackは canonical operator workflow の固定operation `rollback-timer`
  （またはowner-only `exec`）が `ops/vm_actions/rollback_corner_rotation_timer.sh` を実行し、
  新timerを停止して旧regular unitを復元する。state/lock/pause marker/receiptは変更しない。
- 共有の `docich.service` / Soren / 表示 / 音声 / 配信unitは移行・rollbackで
  restartしない。installerはunitを一時ファイルへ描画してrenameで配置し、
  symlink（alias）へ直接書き込まない。

## Soren91 コーナー (macOS リモートレンダラー)

Soren91 は macOS 側のローカルエージェントが動かすリモートレンダラーで、OCI 側は
その SRT 映像を配信枠へ載せる。コーナーの開始には Mac エージェントの接続情報が
必要で、次の env ファイルを VM に 0600 で置く (秘密情報を含むためリポジトリへ
commit しない):

`/home/ubuntu/soren/soren91-macos-agent.env`

- `SOREN91_MACOS_AGENT_BASE_URL` … Mac エージェントの base URL (Tailscale IPv4 + port)
- `SOREN91_OCI_TAILSCALE_IP` … OCI 側の Tailscale IPv4
- `SOREN91_LOCAL_AGENT_TOKEN` … Mac エージェントの Bearer token

`docich-soren91-corner.service` はこのファイルを `EnvironmentFile=` で読む。
現行の共通rotation timerでは同じファイルをunit全体へ継承せず、メリケンadapterが
allowlistした3項目だけを実行中に読み込むため、レトロ/PAPER子プロセスへ秘密を渡しません。
手動でコーナーを実行する場合 (検証時) も同じ env が必要:

```sh
cd /home/ubuntu/docich
set -a; . /home/ubuntu/soren/soren91-macos-agent.env; set +a
bin/docich-soren91-corner-manual --config config/docich.soren-live.toml \
  start --duration-minutes 10
bin/docich-soren91-corner-manual --config config/docich.soren-live.toml status
bin/docich-soren91-corner-manual --config config/docich.soren-live.toml stop
```

env が無い場合、Soren91 アダプタの preflight が fail-closed になりコーナーは
開始しない (BOT 無しの表示だけの枠が commit されることはない)。

ゲームプレイBOT (`soren91/main.mjs`) はゲーム設定 `config/games/soren91.toml` で
既定有効 (`[agent] enabled = true` / `[soren91] bot_path`)。切替は Mac 側 CDP
プロキシの応答を待ってから commit し、BOT は tmux 所有ウィンドウで起動して
コーナー終了時に停止する。CDP プロキシが応答しない間は切替がロールバックする
(fail-closed)。

- `[soren91] cdp_port` は **OCI から到達できる Tailscale プロキシの port** を
  指定する。Mac 内の Chrome CDP (127.0.0.1:9322) の port ではない。
- 表示切替だけを試す検証では `[agent] enabled = false` に戻す。この場合も
  コーナー開始には env ファイルが必要で、CDP プロキシの待機は行われない。

## Web UI (docich-webui.service) の導入

モデルチェーン / バックオフ管理 UI を systemd で常駐させる場合のみ導入する
(既定では enable しない。`docich webui` を手動で起動してもよい):

```sh
cd /path/to/docich
sed "s|__DOCICH_ROOT__|$(pwd)|g" scripts/systemd/docich-webui.service \
  > ~/.config/systemd/user/docich-webui.service
systemctl --user daemon-reload
systemctl --user enable --now docich-webui.service

# Tailscale 経由で公開する場合 (Web UI 側は 127.0.0.1:8787 でバインド):
tailscale serve --bg --https=443 http://127.0.0.1:8787
```

- バインド先・ポートは `config/docich.toml` の `[webui]` セクションで変更できる。
- `tailscale serve` を使わない場合は、直接 `http://<tailnet-IP>:8787/` へ
  アクセスできる (Tailscale ACL で到達制御すること)。
- ログは `journalctl --user -u docich-webui -f` で確認できる。
- **コード更新の反映**: 常駐プロセスなので、docich を deploy して
  `src/docich/webui.py` が更新されても再起動するまで旧 UI が配信される。
  owner-only の `restart_webui` operation (`ops/vm_actions/README.md`) で
  固定 unit だけを再起動し、配信中の HTML がデプロイ済み `INDEX_HTML` と
  一致するまでを確認する。

## `loginctl enable-linger` が必要な理由

systemd --user ユニットは既定では「そのユーザーのログインセッションが1つも
無い」と停止対象になる。ヘッドレス VM で SSH セッションを閉じても
docich を常駐させ続けるには、対象ユーザーで一度だけ以下を実行する
(oracle_arm_setup_guide.md §4 と同じ前提):

```sh
loginctl enable-linger "$(whoami)"
```

これを行わないと、SSH を切断した瞬間に user unit が停止しうる。

## 警告: soren-runtime.service の所有権は移さない

**このユニット群は `soren-runtime.service` を start/stop/restart/enable/disable しない。**
依存関係も持たず、Soren本番のXvfb・PulseAudio・FFmpeg・worker ownershipは従来どおり
Soren側に残る。

`docich-corner-rotation.service` だけは例外的に、既存のlive-handoff契約として外部所有
`:99` の指定viewportへゲームwindowを載せる。しかしX serverや配信process自体は
制御しない。これは `config/docich.soren-live.toml` の `managed=false` / `stream=null`
でfail-safeに固定される。移行期間中の `docich-retro-corner.service` は同じunitへの
互換aliasであり、別serviceではない。

## watchdog の誤検知に関する注意

`[watchdog]` を有効化すると、`interval_s` ごとにスクリーンショットを比較し、
同一画面が `freeze_cycles` 回連続するとフリーズとみなして `docich switch`
(現在のゲームの再起動) を実行する。つまり**静的な画面が `freeze_cycles ×
interval_s` 秒続くと自動的に切り替えが走る**。判定は「agent window が
稼働中 (= agent がゲームに対して能動的に act している)」ときだけ行う設計
なので、agent を無効化しているゲームや agent 未起動の状態で誤爆すること
は無い。しかし、agent が有効なゲームでも「長いカットシーン」「ロード画面」
「ダイアログ待ち」のように、正常な進行として画面がしばらく変化しない
ゲームでは、既定値 (`freeze_cycles = 5`, `interval_s = 60` = 5分) のままだと
誤検知しうる。そのようなゲームを運用する場合は `freeze_cycles` を大きく
して静止時間の許容幅を広げること。

## 停止・無効化

```sh
systemctl --user disable --now docich-soren91-corner.timer # 有効化していた場合のみ
systemctl --user disable --now docich-corner-rotation.timer # 有効化していた場合のみ
systemctl --user disable --now docich-rotate.timer        # 有効化していた場合のみ
systemctl --user disable --now docich.service
```

`docich.service` の `ExecStop` は `docich down` (全コンポーネント停止) を
呼ぶので、`stop`/`disable` すれば watchdog window を含め tmux セッション
ごと片付く。

## PAPERコーナー終了後の戦略改善

systemdから動くコーナーは、改善を独立した一時user service
`docich-paper-improve-<id>.service` に投入します。`start_new_session=True` だけでは
親のcgroupを抜けないため、既定の `KillMode=control-group` で親の終了とともに
改善も停止されます。コーナー側のKillModeは変更しません。

一時serviceは `Type=exec`、実行上限1500秒（生成600+60秒を検証リトライ込みで2回）、
終了猶予30秒で、既存のsingle-flight lockを維持します。投入失敗は
`improve_job.spawned=false` となり、元のcgroupへの代替起動は行いません。
ログは従来の `paper-corner-improve-<date>.log`、結果は
`trading/paper_improve_status.json` です。投入成功だけでは完了を意味しません。
新しい `started_at` とterminal statusを確認してください。systemd外の手動実行や
macOSでは従来の独立セッション起動を使います。
