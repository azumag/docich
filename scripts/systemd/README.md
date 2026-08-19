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
| `docich-webui.service` | `docich webui` を常駐させる simple ユニット (Tailscale serve で公開する場合のみ利用) |

## 導入手順

```sh
cd /path/to/docich   # リポジトリのルートへ移動 (git clone 先など)
mkdir -p ~/.config/systemd/user
DOCICH_ROOT="$(pwd)"

for f in docich.service docich-rotate.service docich-rotate.timer; do
  sed "s|__DOCICH_ROOT__|${DOCICH_ROOT}|g" "scripts/systemd/${f}" \
    > ~/.config/systemd/user/"${f}"
done

systemctl --user daemon-reload

# 基盤 (display/audio/stream) を起動する。既定で有効化されるのはこれだけ。
systemctl --user enable --now docich.service

# rotation.games を設定して自動ローテーションを使う場合のみ (既定では有効化しない)
systemctl --user enable --now docich-rotate.timer
```

`docich-rotate.service` は `[Install]` を持たないため `enable` できない
(= timer 経由でのみ起動する設計)。単発で試すだけなら
`systemctl --user start docich-rotate.service` を直接使う。

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

これを行わないと、SSH を切断した瞬間に `docich.service` ごと配信が
停止してしまう。

## 警告: soren-runtime.service とは完全に独立

**同一 VM 上で稼働中の `soren-runtime.service` (Soren 本番配信) を、この
ユニット群は一切参照・制御しない。** `docich.service` の enable/start/stop/
disable は soren-runtime.service の状態に何の影響も与えない (依存関係
[Unit] セクションにも書かれていない)。これは事故防止のための意図的な設計
であり、architecture.md §0 の「名前空間分離による共存」を systemd レベル
でも維持するものである。

このディレクトリのユニットは **既定では何も enable しない**。導入手順を
実行した運用者が明示的に `enable --now` するまで、docich は起動しない
(soren の稼働に影響を与えない安全側のデフォルト)。

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
systemctl --user disable --now docich-rotate.timer   # 有効化していた場合のみ
systemctl --user disable --now docich.service
```

`docich.service` の `ExecStop` は `docich down` (全コンポーネント停止) を
呼ぶので、`stop`/`disable` すれば watchdog window を含め tmux セッション
ごと片付く。
