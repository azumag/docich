# Owner-only VM operations via GitHub Actions

`docich` と `soviet_now` の VM 操作を GitHub Actions に集約する共通 control plane です。main 反映、preview 反映、状態確認、VM command 実行、初回 baseline 登録を扱います。

## Security boundary

- `.github/workflows/vm-operations.yml` は `github.actor_id=9018513`、`github.triggering_actor=azumag`、`refs/heads/main`、`github.ref_protected=true` をすべて要求し、再実行者も owner 以外なら拒否します。
- VM credential は GitHub Environment `vm-operations` にだけ置きます。Environment は **selected branch = main**、**required reviewer = azumag** にし、owner 自身が承認できるよう `prevent self-review` は有効にしません。
- main は branch protection/ruleset で PR 必須・CODEOWNERS approval 必須・force push/delete 禁止にします。設定するまで VM workflow は `github.ref_protected` で fail-closed します。
- SSH host key は `VM_SSH_KNOWN_HOSTS` に固定し、workflow 内で `ssh-keyscan` はしません。
- SSH private key は専用鍵にし、VM の `authorized_keys` では forced command に固定します。通常の対話 shell、port/agent/X11 forwarding、PTY は許可しません。
- candidate ref のコードは Actions runner 上で実行しません。current main の control script が Git object を regular-file-only tar に変換し、`.env`、`.github`、`ops/vm_actions`、`tmp`、`logs`、`data`、`node_modules` など runtime/control path を除外します。symlink と submodule は payload に入れません。
- production deploy は記録済み manifest と VM 実ファイルの SHA-256/mode を比較し、手編集や別 agent の変更を検出したら停止します。新規 tracked path と既存 unmanaged file の衝突も拒否します。
- production `exec` の stdout/stderr は公開 Actions log に流さず、VM の `/home/ubuntu/.local/state/github-vm-ops/logs/` に mode 0600 で保存します。preview `exec` は production `.env` を自動 source せず、結果を Actions log に返します。
- docich/soviet_now 間の同時操作は VM 上の共有 file lock で直列化します。

## One-time setup

1. GitHub の main protection/ruleset を設定し、PR と CODEOWNERS review を必須にします。
2. Environment `vm-operations` を作り、deployment branch を main のみに限定し、required reviewer を `azumag` のみにします。
3. 専用鍵を作ります。秘密鍵は GitHub Environment にだけ登録します。

```bash
ssh-keygen -t ed25519 -f github-vm-ops -C github-vm-ops-actions
```

4. 公開鍵とこのディレクトリを VM に安全な既存経路で置き、一度だけ gateway を root 所有で導入します。

```bash
sudo bash ops/vm_actions/install_vm_gateway.sh ./github-vm-ops.pub ubuntu
```

5. Environment secrets を設定します。

- `VM_SSH_HOST`: VM host/IP
- `VM_SSH_USER`: 通常は `ubuntu`
- `VM_SSH_PORT`: 通常は `22`（空なら22）
- `VM_SSH_PRIVATE_KEY`: 上記専用秘密鍵
- `VM_SSH_KNOWN_HOSTS`: 別の信頼済み接続で fingerprint を確認した known_hosts 行

6. 初回だけ Actions → **VM operations** から `bootstrap / production / ref=main / confirm=production` を実行します。これは candidate を VM staging に置き、現在の production file hash を baseline として記録するだけで、本番ファイルを書き換えません。その後 `deploy / production / main` を実行します。

## Daily use

- **main merge → production**: main push で production deploy が起動します。Environment の owner approval 後、最新 main 以外の stale run は拒否されます。
- **branch/commit を VM test area へ**: `deploy / preview / ref=<branch-or-sha>`。`/home/ubuntu/.local/state/github-vm-ops/releases/<repo>/<sha>` に展開され、本番は変更しません。
- **preview で test command**: 同じ ref を指定して `exec / preview`。例: `python3 -m unittest discover -s tests`。
- **production command**: `exec / production / ref=main / confirm=production`。任意 shell command を送れますが、出力本文は Actions に公開せず VM private log に保存します。
- **status**: `status / production` または `status / preview`。production は現在の managed SHA、preview は指定 SHA の staged 有無だけを返します。

## Live process activation

production deploy は tracked code file の反映と drift-safe rollback までを責務とし、長時間動作中 worker を無条件再起動しません。`soviet_now/core/config.sh` の既定値変更など完全再起動が必要な変更では、deploy 成功後に owner-only `exec / production` で対象 worker を明示的に `TERM` し、既存 supervisor の respawn と PID/ログを確認してください。配信全体を一律 restart せず、既存の運用契約に従って対象だけを再起動します。

## Recovery and drift

production が `VM drift detected` で止まった場合、VM と repository のどちらが正しいか確認してから同期してください。`bootstrap` は既存 baseline があると拒否するため、drift を無視する逃げ道にはなりません。deploy 中の書き込み失敗は直前 tracked files を private backup から戻して検証してから失敗終了します。
