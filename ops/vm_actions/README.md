# Owner-only VM operations via GitHub Actions

`docich` と `soviet_now` の VM 操作を GitHub Actions に集約する共通 control plane です。main の本番反映、branch/commit の preview 反映、状態確認、owner command 実行、初回 baseline 登録を扱います。

## Security boundary

- `.github/workflows/vm-operations.yml` は `github.actor_id=9018513`、`github.triggering_actor=azumag`、`refs/heads/main`、`github.ref_protected=true` をすべて要求します。owner 以外が開始した run と、owner 以外が再実行した run は拒否します。
- VM credential は GitHub Environment `vm-operations` にだけ置き、deployment branch/tag policy は **main branch だけ**にします。main merge 後の自動反映を維持する場合は Environment の required reviewer は設定しません。二重承認が必要なら reviewer を `azumag` のみに追加できます。
- main は branch protection/ruleset で PR 必須・CODEOWNERS approval 必須・force push/delete 禁止にします。この設定が済むまで VM workflow は `github.ref_protected` で fail-closed します。
- SSH host key は `VM_SSH_KNOWN_HOSTS` に固定し、workflow 内で `ssh-keyscan` はしません。専用秘密鍵は forced command に固定し、通常 shell、PTY、port/agent/X11 forwarding を許しません。
- candidate branch のコードを Actions control job 上で import/source/test しません。preview は trusted current-main script が Git object から regular-file-only tar を作り、`.env`、`.github`、`ops/vm_actions`、`tmp`、`logs`、`data`、`node_modules`、symlink、submodule を除外します。
- preview command は `bubblewrap` で network と production filesystem から分離した一時コピー上で実行します。production `.env` や `/home/ubuntu/soren` を preview test から読めません。
- production command の stdout/stderr は公開 Actions log に流さず、VM の `/home/ubuntu/.local/state/github-vm-ops/logs/` に mode 0600 で保存します。**command input 自体は GitHub workflow run の入力として残るため、token/password を command 文字列へ直接書かないでください。**
- docich/soviet_now 間の VM 操作は共有 file lock で直列化します。

## Production deployment modes

### soviet_now

`/home/ubuntu/soren` は Git 管理外なので overlay mode です。初回 `bootstrap` で candidate に対応する既存本番ファイルの SHA-256/mode を baseline として記録します。本番 deploy 前に baseline と VM 実ファイルを比較し、手編集・別 agent の変更・新規 managed path と既存 unmanaged file の衝突を検出したら停止します。反映前の tracked files は private backup に保存し、書き込み中に失敗した場合は復元して hash を再確認します。

### docich

`/home/ubuntu/docich` は既存 Git worktree を維持します。production payload は tar 上書きではなく Git bundle です。初回 `bootstrap` で現在 HEAD と tracked clean state を baseline にし、その後は HEAD と tracked working tree が前回記録と一致するときだけ bundle を fetch して `git reset --hard <verified SHA>` します。失敗時は直前 HEAD へ戻します。

`games/soviet_now` は docich から更新せず、`azumag/soviet_now` 側 pipeline が所有します。他の submodule worktree も docich deploy では自動更新しません。

## One-time setup

1. 両 repository の main protection/ruleset を設定し、PR と CODEOWNERS review を必須にします。
2. 両 repository に Environment `vm-operations` を作り、deployment branch を `main` のみに限定します。
3. VM に `git` と `bubblewrap` があることを確認します。Ubuntu で不足していれば `sudo apt install bubblewrap` など、既存運用に合わせて導入します。
4. Actions 専用 ed25519 鍵を作ります。

```bash
ssh-keygen -t ed25519 -f github-vm-ops -C github-vm-ops-actions
```

5. 公開鍵と `ops/vm_actions/` を既存の信頼済み VM 接続経路で置き、一度だけ gateway を root 所有で導入します。installer は `/home/ubuntu/docich` が Git worktree であることも確認します。

```bash
sudo bash ops/vm_actions/install_vm_gateway.sh ./github-vm-ops.pub ubuntu
```

6. 両 Environment に secrets を設定します。

- `VM_SSH_HOST`: VM host/IP
- `VM_SSH_USER`: 通常は `ubuntu`
- `VM_SSH_PORT`: 通常は `22`（空なら22）
- `VM_SSH_PRIVATE_KEY`: Actions 専用秘密鍵
- `VM_SSH_KNOWN_HOSTS`: 別の信頼済み経路で fingerprint を確認した known_hosts 行

7. 各 repository で初回だけ Actions → **VM operations** から `bootstrap / production / ref=main / confirm=production` を実行します。bootstrap は baseline を記録するだけで、本番コードを変更しません。VM と repository の差分を確認して問題なければ、その後 `deploy / production / main` を実行します。

## Daily use

- **owner が main を merge → production**: main push で production deploy が起動します。最新 main 以外の stale run は拒否します。Environment reviewer を付けない構成なら追加クリックなしで進みます。
- **branch/commit を VM test area へ**: `deploy / preview / ref=<branch-or-sha>`。本番は変更しません。
- **preview test command**: 同じ ref で `exec / preview`。bubblewrap 内で実行され、テスト出力は Actions から確認できます。
- **production command**: `exec / production / ref=main / confirm=production`。VM user 権限の任意 shell command を送れますが、stdout/stderr 本文は VM private log のみに保存します。
- **status**: `status / production` または `status / preview`。production は現在 SHA/baseline 状態または drift、preview は指定 SHA の staged 有無を返します。

## Running process activation

production deploy はまず code/data の整合性ある反映を行い、長時間稼働 worker を無条件には再起動しません。`soviet_now/core/config.sh` の既定値変更のように完全再起動が必要な場合は、deploy 後に owner-only `exec / production` で対象 worker を `TERM` し、既存 supervisor の respawn、PID、起動ログを確認します。配信 service 全体を一律 restart せず、既存の運用契約に従って必要な process だけを反映します。

## Drift / recovery

`VM drift detected` または `tracked VM drift detected` で停止した場合は、VM と repository のどちらを正とするか確認してから同期します。`bootstrap` は baseline が存在すると拒否するため、drift 無視の上書きには使えません。
