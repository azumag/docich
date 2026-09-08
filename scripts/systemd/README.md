# docich systemd --user ユニット (Phase 3)

tmux 常駐 (architecture.md §2) を systemd --user ユニットで包むためのテンプレート。
このディレクトリのユニットファイルは **雛形**であり、`__DOCICH_ROOT__` を実際の
リポジトリ絶対パスに置換してから `~/.config/systemd/user/` に配置して使う。
リポジトリ内のユニットファイル自体は編集しないこと (置換はコピー先で行う)。

| ファイル | 役割 |
|---|---|
| `docich.service` | `docich up` / `docich down` を包む oneshot ユニット (`RemainAfterExit=yes`) |
| `docich-rotate.service` | `docich rotate` を1回実行する oneshot ユニット (`[Install]` なし。timer 専用) |
| `docich-rotate.timer` | `docich-rotate.service` を毎時起動する timer ([rotation] 利用時のみ) |
| `docich-retro-corner.service` | Soren本番 `:99` にメリケンAIレトロゲーム枠を載せる長時間oneshot |
| `docich-retro-corner.timer` | 毎時 `retro-corner tick`。設定timezone/start_hourに一致した時だけ1日1回実行 |
| `docich-webui.service` | `docich webui` を常駐させる simple ユニット (Tailscale serve で公開する場合のみ利用) |

## 導入手順

```sh
cd /path/to/docich   # リポジトリのルートへ移動 (git clone 先など)
mkdir -p ~/.config/systemd/user
DOCICH_ROOT="$(pwd)"

for f in \
  docich.service \
  docich-rotate.service \
  docich-rotate.timer \
  docich-retro-corner.service \
  docich-retro-corner.timer
do
  sed "s|__DOCICH_ROOT__|${DOCICH_ROOT}|g" "scripts/systemd/${f}" \
    > ~/.config/systemd/user/"${f}"
done

systemctl --user daemon-reload

# 基盤 (display/audio/stream) を起動する。必要な場合だけ明示enableする。
systemctl --user enable --now docich.service

# rotation.games を設定して自動ローテーションを使う場合のみ。
systemctl --user enable --now docich-rotate.timer

# メリケンAI レトロゲームコーナーを使う場合。
# 本番設定は config/docich.soren-live.toml の [retro_corner] を読む。
systemctl --user enable --now docich-retro-corner.timer
```

`docich-rotate.service` と `docich-retro-corner.service` は `[Install]` を持たないため
直接 `enable` しない。timer が起動するoneshotである。

## メリケンAI レトロゲームコーナー

本番では `config/docich.soren-live.toml` を必ず使う。このprofileは
`display=:99`, `managed=false`, `stream.mode="null"`, `audio.enabled=false` で、
**SorenのXvfb・音声bus・FFmpegを所有しない**。

毎分のtimerで開始予定を確認します。レトロ枠は毎日20:00予定・実開始60分、
PAPER枠は毎日22:00予定・実開始30分です。いずれも予定時刻を過ぎたら待機状態を保存し、
その後の改善サイクル完了または予想のAPI確定成功を待ちます。単なる試合終了や
改善stateの消失を境界とみなしません。候補がA/B比較待ちなら、その採否完了が改善境界です。

両枠はSoren rootの共通lockで直列化します。開始が遅れても短縮せず、レトロ枠は
ゲーム切替完了後、PAPER枠は詳細表示へ切替後から規定時間を数えます。境界が来なければ
待機を継続し、暗黙のタイムアウト開始は行いません。待機中・実行中の再起動にも状態を保存し、
他枠が未復旧のactive状態なら開始を拒否します。PAPER枠は5分ごとの模擬集計を伝え、
終了後compactへ戻ります。通常ゲーム中も公開データのPAPER workerは継続します。

本番導入では `.venv-trading` に `requirements-trading.txt` をインストールし、
`docich-paper-runtime.service`、`docich-paper-corner.service/timer` と
更新した `docich-retro-corner.service/timer` を同じ方法で配置します。
`docich-paper-runtime.service` と両timerをenableします。過去通知を避けるため、
workerの初回起動より先にlive profileの `trading notify-once` を実行します。
実注文・private API・鍵は使用しません。資金は模擬1万円、投入上限30%です。

状態確認:

```sh
bin/docich --config config/docich.soren-live.toml retro-corner status --json
bin/docich --config config/docich.soren-live.toml paper-corner status
bin/docich --config config/docich.soren-live.toml trading status
```

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

`docich-retro-corner.service` だけは例外的に、既存のlive-handoff契約として外部所有
`:99` の指定viewportへゲームwindowを載せる。しかしX serverや配信process自体は
制御しない。これは `config/docich.soren-live.toml` の `managed=false` / `stream=null`
でfail-safeに固定される。

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
systemctl --user disable --now docich-retro-corner.timer  # 有効化していた場合のみ
systemctl --user disable --now docich-rotate.timer        # 有効化していた場合のみ
systemctl --user disable --now docich.service
```

`docich.service` の `ExecStop` は `docich down` (全コンポーネント停止) を
呼ぶので、`stop`/`disable` すれば watchdog window を含め tmux セッション
ごと片付く。
